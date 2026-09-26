"""Accès direct à www.transfermarkt.com, sans transfermarkt-api ni dépendance externe.

Même contrat que `tm_client.TransfermarktClient` (formats de réponse de
transfermarkt-api : `date_of_birth`, `market_value`, `marketValueHistory`,
`club_from`/`club_to`…), mais les pages HTML sont lues et analysées ici avec la
bibliothèque standard (html.parser). Utilisé quand aucune instance de
transfermarkt-api n'est disponible (TM_API_URL absent), ce qui est le cas par
défaut.

Particularités constatées le 26.09.2026 :
- Transfermarkt sert un captcha AWS WAF (HTTP 405 « Human Verification ») sur
  environ une requête sur deux, au hasard : on réessaie simplement.
- Depuis d'autres réseaux, il sert à la place un défi JavaScript AWS WAF
  (HTTP 202, page « gokuProps ») à chaque requête : ce n'est pas une page
  Transfermarkt, elle n'est jamais mise en cache, et après les tentatives le
  client abandonne avec un message explicite (constaté le 26.09.2026).
- Les pages « statistiques détaillées » d'un joueur sont désormais rendues côté
  client (composants tm-player-performance-*) : plus aucun tableau dans le HTML,
  transfermarkt-api renvoie donc une liste vide. On lit à la place la page
  « Squad statistics » du club (`/leistungsdaten/verein/{id}`), une page par
  club, compétition et saison, qui donne matchs, buts, passes, cartons et
  minutes de tout l'effectif.
- Historique de valeur marchande et transferts : points d'entrée JSON `ceapi`.

Cadence : un appel toutes les 1,2 s par défaut, cache disque 72 h dans data/cache/.
"""
from __future__ import annotations

import hashlib
import http.cookiejar
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

BASE_URL = "https://www.transfermarkt.com"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0.0.0 Safari/537.36")
VOID = {"img", "br", "input", "meta", "link", "hr", "source", "wbr", "col", "base", "area", "embed", "param", "track"}
# Fermetures implicites : ouvrir la balise de gauche ferme les balises de droite encore ouvertes.
IMPLICIT_CLOSE = {
    "td": {"td", "th"}, "th": {"td", "th"}, "tr": {"tr", "td", "th"},
    "tbody": {"thead", "tbody", "tr", "td", "th"}, "thead": {"tbody", "tr", "td", "th"},
    "li": {"li"}, "option": {"option"}, "p": {"p"}, "dt": {"dt", "dd"}, "dd": {"dt", "dd"},
}


# --------------------------------------------------------------------------- mini-DOM
class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "data")

    def __init__(self, tag: str | None, attrs: dict | None = None, parent: "Node | None" = None, data: str = ""):
        self.tag, self.attrs, self.parent, self.data = tag, attrs or {}, parent, data
        self.children: list[Node] = []

    # -- navigation
    def iter(self):
        for c in self.children:
            yield c
            yield from c.iter()

    def find_all(self, tag: str | None = None, cls: str | None = None, **attrs) -> list["Node"]:
        out = []
        for n in self.iter():
            if n.tag is None or (tag and n.tag != tag):
                continue
            if cls and cls not in n.classes():
                continue
            if any(n.attrs.get(k) != v for k, v in attrs.items()):
                continue
            out.append(n)
        return out

    def find(self, tag: str | None = None, cls: str | None = None, **attrs) -> "Node | None":
        r = self.find_all(tag, cls, **attrs)
        return r[0] if r else None

    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()

    def get(self, key: str, default=None):
        return self.attrs.get(key, default)

    def text(self, sep: str = " ") -> str:
        parts = [self.data] if self.tag is None else [n.data for n in self.iter() if n.tag is None]
        return re.sub(r"\s+", " ", sep.join(p for p in parts if p)).replace("\xa0", " ").strip()

    def own_text(self) -> str:
        return re.sub(r"\s+", " ", "".join(c.data for c in self.children if c.tag is None)).replace("\xa0", " ").strip()

    def ancestor(self, tag: str) -> "Node | None":
        n = self.parent
        while n is not None and n.tag != tag:
            n = n.parent
        return n

    def cells(self) -> list["Node"]:
        """Cellules directes d'une ligne (ignore les tableaux imbriqués)."""
        return [c for c in self.children if c.tag in ("td", "th")]


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.cur = self.root

    def _close_up_to(self, tags: set[str]):
        n = self.cur
        while n is not self.root:
            if n.tag in tags:
                self.cur = n.parent
                return
            if n.tag in ("table", "div", "ul", "ol", "select", "dl"):
                return  # on ne franchit pas un conteneur
            n = n.parent

    def handle_starttag(self, tag, attrs):
        if tag in IMPLICIT_CLOSE:
            self._close_up_to(IMPLICIT_CLOSE[tag])
        node = Node(tag, dict(attrs), self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, dict(attrs), self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not self.root:
            if n.tag == tag:
                self.cur = n.parent
                return
            n = n.parent

    def handle_data(self, data):
        if data:
            self.cur.children.append(Node(None, parent=self.cur, data=data))


def parse_html(text: str) -> Node:
    b = _Builder()
    b.feed(text)
    return b.root


# --------------------------------------------------------------------------- conversions
def parse_money(s: str | None) -> int | None:
    """'€150k' → 150000 ; '€1.20m' → 1200000 ; '€1.2bn' → 1_200_000_000 ; '-' → None."""
    if not s:
        return None
    m = re.search(r"([\d.,]+)\s*(bn|m|k|th\.)?", s.replace("€", "").strip().lower())
    if not m:
        return None
    num = float(m.group(1).replace(",", "."))
    mult = {"bn": 1e9, "m": 1e6, "k": 1e3, "th.": 1e3}.get(m.group(2), 1)
    return int(round(num * mult))


def parse_date(s: str | None) -> str | None:
    """'16/10/1997' ou '16.10.1997' ou 'Oct 16, 1997' → '1997-10-16'."""
    if not s:
        return None
    s = s.strip()
    m = re.search(r"(\d{1,2})[/.](\d{1,2})[/.](\d{4})", s)
    if m:
        d, mo, y = (int(v) for v in m.groups())
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return None
    m = re.search(r"([A-Z][a-z]{2}) (\d{1,2}), (\d{4})", s)
    if m:
        months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
        try:
            return date(int(m.group(3)), months.index(m.group(1).lower()) + 1, int(m.group(2))).isoformat()
        except ValueError:
            return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    return m.group(0) if m else None


def parse_int(s: str | None) -> int:
    """'2.340'' → 2340 ; '-' → 0."""
    if not s:
        return 0
    digits = re.sub(r"[^\d]", "", s)
    return int(digits) if digits else 0


def parse_height(s: str | None) -> int | None:
    m = re.search(r"(\d)[,.](\d{2})", s or "")
    return int(m.group(1) + m.group(2)) if m else None


def id_from_href(href: str | None) -> str | None:
    m = re.search(r"/(?:spieler|verein|wettbewerb)/([A-Za-z0-9]+)", href or "")
    return m.group(1) if m else None


def slug_from_href(href: str | None) -> str | None:
    m = re.match(r"/([^/]+)/", href or "")
    return m.group(1) if m else None


def th_label(th: Node) -> str:
    """Libellé d'un en-tête : attribut title du <th> ou d'un descendant (icônes), sinon texte."""
    for n in [th, *th.iter()]:
        if n.tag is not None and n.get("title"):
            return n.get("title").lower()
    return th.text().lower()


def is_waf_challenge(status: int, body: str) -> bool:
    """Défi JavaScript AWS WAF (HTTP 202, page « gokuProps ») : pas une page Transfermarkt."""
    head = body[:5000]
    return status == 202 or "gokuProps" in head or "awsWafCookieDomainList" in head


def current_season() -> int:
    t = date.today()
    return t.year if t.month >= 7 else t.year - 1


# --------------------------------------------------------------------------- client
class TransfermarktDirect:
    def __init__(self, cache_dir: Path = CACHE_DIR, cache_ttl_h: float = 72, min_interval_s: float = 0.9,
                 retries: int = 8, verbose: bool = False):
        self.base_url = BASE_URL
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl_s = cache_ttl_h * 3600
        self.min_interval_s = min_interval_s
        self.retries = retries
        self.verbose = verbose
        self._lock = threading.Lock()
        self._last_call = 0.0
        self.calls = 0
        self.captchas = 0
        jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    # -- plomberie ---------------------------------------------------------
    def _cache_path(self, url: str, ext: str) -> Path:
        return self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ext)

    def _throttle(self):
        with self._lock:
            wait = self.min_interval_s - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()
            self.calls += 1

    def fetch(self, path: str, as_json: bool = False) -> str | dict:
        url = path if path.startswith("http") else self.base_url + path
        cached = self._cache_path(url, ".json" if as_json else ".html")
        if cached.exists() and time.time() - cached.stat().st_mtime < self.cache_ttl_s:
            text = cached.read_text(encoding="utf-8")
            return json.loads(text) if as_json else text
        headers = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
                   "Accept": "application/json, text/plain, */*" if as_json
                   else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
        if as_json:
            headers["Referer"] = self.base_url + "/"
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                req = urllib.request.Request(url, headers=headers)
                with self._opener.open(req, timeout=60) as resp:
                    status = resp.status
                    body = resp.read().decode("utf-8", "replace")
                if is_waf_challenge(status, body):
                    raise urllib.error.HTTPError(url, 405, "waf challenge", None, None)  # type: ignore[arg-type]
                if as_json:
                    data = json.loads(body)
                elif "Human Verification" in body[:3000] and "<table" not in body:
                    raise urllib.error.HTTPError(url, 405, "captcha", None, None)  # type: ignore[arg-type]
                tmp = cached.with_suffix(cached.suffix + ".tmp")
                tmp.write_text(body, encoding="utf-8")
                tmp.replace(cached)
                return data if as_json else body
            except urllib.error.HTTPError as e:
                last_err = e
                if e.code == 405:  # captcha AWS WAF, servi au hasard : on réessaie
                    self.captchas += 1
                elif e.code == 404:
                    raise
                elif e.code not in (429, 500, 502, 503, 504):
                    raise
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ConnectionError) as e:
                last_err = e
            if self.verbose:
                print(f"    retry {attempt + 1} {path}: {last_err}")
            time.sleep(min(2.0 * (attempt + 1), 12.0) if getattr(last_err, "code", 0) == 405 else 3.0 * (attempt + 1))
        if getattr(last_err, "code", 0) == 405:
            raise RuntimeError(f"{url} : abandon après {self.retries + 1} tentatives — Transfermarkt sert un "
                               f"défi anti-robot (AWS WAF) à ce client à chaque essai")
        raise RuntimeError(f"{url} : abandon après {self.retries + 1} tentatives ({last_err})")

    def page(self, path: str) -> Node:
        return parse_html(self.fetch(path))

    # -- points d'entrée (mêmes formes que transfermarkt-api) ----------------
    def search_club(self, name: str) -> list[dict]:
        doc = self.page(f"/schnellsuche/ergebnis/schnellsuche?query={urllib.parse.quote(name)}")
        results = []
        for box in doc.find_all("div", cls="box"):
            h2 = box.find("h2")
            if not h2 or "Clubs" not in h2.text():
                continue
            for tr in box.find_all("tr"):
                link = next((a for a in tr.find_all("a") if "/verein/" in (a.get("href") or "")), None)
                if not link or tr.ancestor("tr") is not None:
                    continue
                cells = tr.cells()
                flag = tr.find("img", cls="flaggenrahmen")
                rechts = [c for c in cells if "rechts" in c.classes()]
                zentriert = [c.text() for c in cells if "zentriert" in c.classes() and c.text().isdigit()]
                results.append({"id": id_from_href(link.get("href")), "url": link.get("href"),
                                "name": link.get("title") or link.text(),
                                "country": flag.get("title") if flag else None,
                                "squad": int(zentriert[0]) if zentriert else None,
                                "market_value": parse_money(rechts[0].text()) if rechts else None})
        return results

    def competition_clubs(self, competition_id: str, season_id: str | int | None = None) -> dict:
        season = str(season_id or current_season())
        doc = self.page(f"/-/startseite/wettbewerb/{competition_id}/plus/?saison_id={season}")
        clubs, seen = [], set()
        for td in doc.find_all("td", cls="hauptlink"):
            if "no-border-links" not in td.classes():
                continue
            a = td.find("a")
            cid = id_from_href(a.get("href")) if a else None
            if cid and cid not in seen:
                seen.add(cid)
                clubs.append({"id": cid, "name": a.get("title") or a.text(), "slug": slug_from_href(a.get("href"))})
        h1 = doc.find("h1")
        return {"id": competition_id, "name": h1.text() if h1 else competition_id, "season_id": season, "clubs": clubs}

    def club_players(self, club_id: str, season_id: str | int | None = None) -> list[dict]:
        season = str(season_id or current_season())
        doc = self.page(f"/-/kader/verein/{club_id}/saison_id/{season}/plus/1")
        table = doc.find("div", id="yw1")
        if table is None:
            return []
        headers = [th_label(th) for th in table.find_all("th")]
        club_h1 = doc.find("h1")
        club_name = club_h1.text() if club_h1 else None

        def col(*keys):
            for i, h in enumerate(headers):
                if any(k in h for k in keys):
                    return i
            return None

        idx = {"dob": col("date of birth"), "nat": col("nat"), "height": col("height"), "foot": col("foot"),
               "joined": col("joined"), "signed": col("signed from"), "contract": col("contract"),
               "mv": col("market value"), "club": col("current club")}
        players = []
        for tr in table.find_all("tr"):
            if tr.ancestor("tr") is not None or not any(c in ("odd", "even") for c in tr.classes()):
                continue
            cells = tr.cells()
            link = next((a for a in tr.find_all("a") if "/profil/spieler/" in (a.get("href") or "")), None)
            if not link:
                continue
            inline = tr.find("table", cls="inline-table")
            pos = None
            if inline:
                rows = [r for r in inline.find_all("tr")]
                if len(rows) >= 2:
                    pos = rows[-1].text()

            def cell(key):
                i = idx.get(key)
                return cells[i] if i is not None and i < len(cells) else None

            dob_cell = cell("dob")
            dob_txt = dob_cell.text() if dob_cell else ""
            age = re.search(r"\((\d+)\)", dob_txt)
            nat_cell = cell("nat")
            signed_cell = cell("signed")
            signed_link = signed_cell.find("a") if signed_cell else None
            signed_img = signed_cell.find("img") if signed_cell else None
            status = [s.get("title") for s in (inline.find_all("span") if inline else []) if s.get("title")]
            players.append({
                "id": id_from_href(link.get("href")), "name": link.text(), "slug": slug_from_href(link.get("href")),
                "position": pos, "date_of_birth": parse_date(dob_txt), "age": int(age.group(1)) if age else None,
                "nationality": [i.get("title") for i in nat_cell.find_all("img") if i.get("title")] if nat_cell else [],
                "current_club": (cell("club").find("img") or Node("x")).get("title") if cell("club") else club_name,
                "height": parse_height(cell("height").text()) if cell("height") else None,
                "foot": (cell("foot").text() or None) if cell("foot") else None,
                "joined_on": parse_date(cell("joined").text()) if cell("joined") else None,
                "signed_from": signed_img.get("title") if signed_img else (signed_cell.text() if signed_cell else None),
                "signed_from_id": id_from_href(signed_link.get("href")) if signed_link else None,
                "contract": parse_date(cell("contract").text()) if cell("contract") else None,
                "market_value": parse_money(cell("mv").text()) if cell("mv") else None,
                "status": "; ".join(status),
            })
        return players

    def player_profile(self, player_id: str, slug: str | None = None) -> dict:
        doc = self.page(f"/{slug or '-'}/profil/spieler/{player_id}")
        h1 = doc.find("h1", cls="data-header__headline-wrapper")
        shirt = h1.find("span", cls="data-header__shirt-number") if h1 else None
        name = h1.text() if h1 else ""
        if shirt:
            name = name.replace(shirt.text(), "").strip()
        # Tableau d'informations : libellé (regular) suivi de sa valeur (bold).
        info: dict[str, Node] = {}
        spans = [s for s in doc.find_all("span") if "info-table__content" in s.classes()]
        for i, s in enumerate(spans):
            if "info-table__content--regular" in s.classes() and i + 1 < len(spans):
                info[s.text().rstrip(":").strip().lower()] = spans[i + 1]

        def val(label):
            n = info.get(label)
            return n.text() if n else None

        dob_txt = val("date of birth/age") or val("date of birth") or ""
        age = re.search(r"\((\d+)\)", dob_txt)
        pob = info.get("place of birth")
        pob_img = pob.find("img") if pob else None
        cit = info.get("citizenship")
        citizenship = [i.get("title") for i in cit.find_all("img") if i.get("title")] if cit else []
        if not citizenship and cit:
            citizenship = [c.strip() for c in re.split(r"\s{2,}", cit.text()) if c.strip()]
        main_pos = doc.find("dd", cls="detail-position__position")
        others = []
        for dt in doc.find_all("dt", cls="detail-position__title"):
            if "other" in dt.text().lower():
                box = dt.parent
                others = [d.text() for d in box.find_all("dd")] if box else []
        mv = doc.find("a", cls="data-header__market-value-wrapper")
        mv_txt = ""
        if mv:
            mv_txt = " ".join(c.text() for c in mv.children if not (c.tag == "p"))
        club_span = doc.find("span", cls="data-header__club")
        club_a = club_span.find("a") if club_span else None
        header = {}
        for lab in doc.find_all("span", cls="data-header__label"):
            content = lab.find("span", cls="data-header__content")
            if content:
                header[lab.own_text().rstrip(":").strip().lower()] = content.text()
        social = doc.find("div", cls="social-media-toolbar__icons")
        social_links = [a.get("href") for a in social.find_all("a") if a.get("href")] if social else []
        youth = []
        for box in doc.find_all("div", cls="tm-player-additional-data"):
            h2 = box.find("h2")
            content = box.find("div", cls="content")
            if h2 and "youth" in h2.text().lower() and content:
                youth = [c.strip() for c in content.text().split(",") if c.strip()]
        canonical = doc.find("link", rel="canonical")
        desc = doc.find("meta", name="description")
        contract = header.get("contract expires") or val("contract expires")
        return {
            "id": player_id, "url": canonical.get("href") if canonical else f"{self.base_url}/-/profil/spieler/{player_id}",
            "name": name, "description": desc.get("content") if desc else "",
            "full_name": val("full name"), "name_in_home_country": val("name in home country"),
            "date_of_birth": parse_date(dob_txt), "age": int(age.group(1)) if age else None,
            "place_of_birth": {"city": (pob.own_text() or (pob.find("span") or Node("x")).own_text()) if pob else None,
                               "country": pob_img.get("title") if pob_img else None},
            "height": parse_height(val("height")), "citizenship": citizenship,
            "position": {"main": main_pos.text() if main_pos else val("position"), "other": others},
            "foot": val("foot"), "shirt_number": shirt.text().lstrip("#") if shirt else None,
            "club": {"id": id_from_href(club_a.get("href")) if club_a else None,
                     "name": (club_a.get("title") or club_a.text()) if club_a else val("current club"),
                     "joined": parse_date(header.get("joined") or val("joined")),
                     "contract_expires": parse_date(contract), "contract_option": header.get("contract option")},
            "market_value": parse_money(mv_txt), "socialMedia": social_links, "youth_clubs": youth,
        }

    def player_market_value(self, player_id: str) -> dict:
        data = self.fetch(f"/ceapi/marketValueDevelopment/graph/{player_id}", as_json=True)
        hist = []
        for h in data.get("list", []):
            hist.append({"age": int(h.get("age") or 0) if str(h.get("age", "")).isdigit() else None,
                         "date": parse_date(h.get("datum_mw")), "club_id": id_from_href(h.get("wappen")) or "",
                         "club_name": h.get("verein"), "market_value": h.get("y")})
        return {"id": player_id, "market_value": parse_money(data.get("current")),
                "marketValueHistory": [h for h in hist if h["date"]], "ranking": {}}

    def player_transfers(self, player_id: str) -> dict:
        data = self.fetch(f"/ceapi/transferHistory/list/{player_id}", as_json=True)
        transfers = []
        for t in data.get("transfers", []):
            fee_raw = (t.get("fee") or "").strip().lower()
            fee = 0 if fee_raw in ("free transfer", "free") else parse_money(t.get("fee")) if "€" in fee_raw else None
            transfers.append({
                "id": (re.search(r"transfer_id/(\d+)", t.get("url") or "") or [None, None])[1],
                "club_from": {"id": id_from_href((t.get("from") or {}).get("href")), "name": (t.get("from") or {}).get("clubName")},
                "club_to": {"id": id_from_href((t.get("to") or {}).get("href")), "name": (t.get("to") or {}).get("clubName")},
                "date": t.get("dateUnformatted") or parse_date(t.get("date")), "upcoming": bool(t.get("upcoming")),
                "season": t.get("season"), "market_value": parse_money(t.get("marketValue")), "fee": fee,
                "fee_label": t.get("fee"),
            })
        return {"id": player_id, "transfers": transfers, "youth_clubs": None}

    def player_stats(self, player_id: str) -> list[dict]:
        """Les statistiques par joueur ne sont plus dans le HTML : voir club_stats()."""
        return []

    def club_stats(self, club_id: str, competition_id: str | None, season_id: str | int) -> dict:
        """Statistiques de tout l'effectif d'un club pour une compétition et une saison.

        `competition_id=None` → toutes compétitions confondues (« Total »). La réponse
        contient aussi `options` : les couples (compétition, saison) disponibles pour ce
        club, ce qui permet de retrouver le championnat qu'il disputait une saison donnée.
        """
        season = str(season_id)
        doc = self.page(f"/-/leistungsdaten/verein/{club_id}/plus/1?reldata={competition_id or ''}%26{season}")
        options, selected = [], None
        sel = doc.find("select", name="reldata")
        for o in (sel.find_all("option") if sel else []):
            code, _, yr = (o.get("value") or "").partition("&")
            options.append({"code": code or None, "season": yr, "label": o.text()})
            if "selected" in o.attrs:
                selected = (code or None, yr)
        rows = []
        table = doc.find("div", id="yw1")
        if table is not None and (selected is None or selected == (competition_id or None, season)):
            headers = [th_label(th) for th in table.find_all("th")]

            def col(*keys):
                for i, h in enumerate(headers):
                    if any(k in h for k in keys):
                        return i
                return None

            idx = {"apps": col("appearances", "matches"), "goals": col("goals"), "assists": col("assists"),
                   "yellow": col("yellow cards"), "yellow2": col("second yellow"), "red": col("red cards"),
                   "minutes": col("minutes")}
            for tr in table.find_all("tr"):
                if tr.ancestor("tr") is not None or not any(c in ("odd", "even") for c in tr.classes()):
                    continue
                link = next((a for a in tr.find_all("a") if "/profil/spieler/" in (a.get("href") or "")), None)
                if not link:
                    continue
                cells = tr.cells()

                def num(key):
                    i = idx.get(key)
                    return parse_int(cells[i].text()) if i is not None and i < len(cells) else 0

                inline = tr.find("table", cls="inline-table")
                pos = inline.find_all("tr")[-1].text() if inline and len(inline.find_all("tr")) >= 2 else None
                rows.append({"id": id_from_href(link.get("href")), "name": link.get("title") or link.text(),
                             "position": pos, "appearances": num("apps"), "goals": num("goals"),
                             "assists": num("assists"), "yellow_cards": num("yellow"),
                             "red_cards": num("red") + num("yellow2"), "minutes_played": num("minutes")})
        label = next((o["label"] for o in options if (o["code"], o["season"]) == (competition_id or None, season)), None)
        return {"club_id": club_id, "competition_id": competition_id, "season_id": season,
                "competition_name": re.sub(r"\s*\d{2}/\d{2}$", "", label) if label else None,
                "rows": rows, "options": options}
