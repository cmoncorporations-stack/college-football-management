"""Journal match par match de la saison en cours (2026/27), toutes compétitions, lu sur Transfermarkt.

    python -m moneyball.matchlog                     # tous les clubs du vivier + Yverdon → data/matchlog_2627.json
    python -m moneyball.matchlog --clubs 322         # un club (test)
    python -m moneyball.matchlog --workers 3 --today 2026-10-11

Constat du 11.10.2026 : la page « performances détaillées » d'un joueur
(`/leistungsdatendetails/spieler/{id}/saison/2026/.../plus/1`) est rendue côté client par le
composant `tm-player-performance-table-new` (data-type `matchPerformanceByCompetition`), qui
interroge l'API `tmapi` (hôte transfermarkt.technology) — aucune ligne de match dans le HTML
et l'hôte de l'API n'est pas joignable depuis cet environnement. Le journal est donc
reconstruit à partir des pages encore servies en HTML sur www.transfermarkt.com :

1. le calendrier de chaque club (`/spielplandatum/verein/{id}/plus/1?saison_id=2026`) : toutes
   les compétitions de la saison, un identifiant de feuille de match par rencontre ;
2. la feuille de match (`/spielbericht/index/spielbericht/{id}`) : date, journée, compétition,
   score, buts (buteur, passeur, minute), remplacements (entrant, sortant, minute), cartons ;
3. les compositions (`/spielbericht/aufstellung/spielbericht/{id}`) : onze de départ et banc de
   chaque équipe, poste occupé dans ce match ;
4. les absences : page « Suspensions et blessures » de chaque club (état au jour de la lecture)
   et historique de blessures de chaque joueur absent d'au moins une feuille
   (`/verletzungen/spieler/{id}`) pour dater les absences pour blessure.

La minute d'un événement est encodée dans un sprite (`background-position: -Xpx -Ypx`) :
minute = X/36 + 10·Y/36 + 1 (vérifié contre la frise chronologique des feuilles de match).
Les suspensions ne sont pas publiées match par match : une absence qui suit immédiatement un
carton rouge (ou une suspension listée par le club) est notée `suspended`, les autres absences
sans blessure connue `not_in_squad`.

Cache disque 72 h dans data/cache/ (jamais versionné), captcha réessayé, `--workers` comme scout.py.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from .model import league_coef
from .tm_direct import Node, TransfermarktDirect, current_season, id_from_href, parse_html

DATA = Path(__file__).resolve().parent.parent / "data"
OUT = DATA / "matchlog_2627.json"
CUP_WORDS = ("cup", "coupe", "pokal", "coppa", "copa", "trophy", "trophée")
EURO_WORDS = ("champions league", "europa league", "conference league", "uefa")
EURO_CODES = {"CL", "EL", "UCOL", "CLQ", "ELQ", "UCOLQ", "CLI", "ECLQ", "UEL", "UCL"}
STATUSES = ("played", "bench", "not_in_squad", "injured", "suspended", "other")


# --------------------------------------------------------------------------- utilitaires
def parse_short_date(s: str | None) -> str | None:
    """'Fri 24/07/26' ou 'Fri, 09/10/26' → '2026-07-24'."""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s or "")
    if not m:
        return None
    d, mo, y = (int(v) for v in m.groups())
    y = y + 2000 if y < 100 else y
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def sprite_minute(style: str | None) -> int | None:
    """Minute encodée dans le sprite horloge : 'background-position: -324px -180px;' → 60."""
    m = re.search(r"-?(\d+)px\s+-?(\d+)px", style or "")
    if not m:
        return None
    x, y = int(m.group(1)), int(m.group(2))
    return x // 36 + (y // 36) * 10 + 1


def competition_code(href: str | None) -> str | None:
    m = re.search(r"wettbewerb/([A-Za-z0-9]+)", href or "")
    return m.group(1) if m else None


def competition_type(code: str | None, label: str | None) -> str:
    """league (championnat à coefficient connu), national_cup, european, other."""
    lab = (label or "").lower()
    if code and league_coef(code) is not None:
        return "league"
    if (code or "") in EURO_CODES or any(w in lab for w in EURO_WORDS):
        return "european"
    if any(w in lab for w in CUP_WORDS):
        return "national_cup"
    return "other"


def _score(text: str | None) -> tuple[int, int] | None:
    m = re.search(r"(\d+)\s*:\s*(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _player_link(node: Node) -> Node | None:
    return next((a for a in node.find_all("a") if "/spieler/" in (a.get("href") or "")), None)


# --------------------------------------------------------------------------- parseurs
def parse_fixtures(html: str, club_id: str) -> list[dict]:
    """Calendrier d'un club : une entrée par rencontre (jouée ou à venir), toutes compétitions."""
    doc = parse_html(html)
    out, comp, comp_name = [], None, None
    for table in doc.find_all("table"):
        if table.ancestor("table") is not None:
            continue
        headers = [th.text().lower() for th in table.find_all("th")]
        if "matchday" not in headers and "home team" not in headers:
            continue
        for tr in table.find_all("tr"):
            if tr.ancestor("tr") is not None:
                continue
            cells = tr.cells()
            if not cells:
                continue
            links = [a for a in tr.find_all("a") if a.get("href")]
            comp_link = next((a for a in links if re.search(r"wettbewerb/[A-Za-z0-9]+", a.get("href"))), None)
            report = next((a for a in links if "/spielbericht/" in a.get("href")), None)
            if report is None:
                # ligne d'en-tête de compétition (une cellule, lien vers la compétition)
                if comp_link is not None and len(cells) <= 2:
                    comp, comp_name = competition_code(comp_link.get("href")), comp_link.text() or comp_link.get("title")
                continue
            clubs = []
            for a in links:
                cid = id_from_href(a.get("href")) if "/verein/" in a.get("href") else None
                if cid and (not clubs or clubs[-1][0] != cid):
                    clubs.append((cid, a.get("title") or a.text()))
            if len(clubs) < 2:
                continue
            m = re.search(r"/spielbericht/(\d+)", report.get("href"))
            texts = [c.text() for c in cells]
            result = next((t for t in reversed(texts) if re.fullmatch(r"\d+:\d+.*|-:-", t)), None)
            md = texts[0].strip()
            out.append({
                "id": m.group(1), "date": parse_short_date(texts[1] if len(texts) > 1 else ""),
                "competition_id": comp, "competition": comp_name,
                "matchday": int(md) if md.isdigit() else (md or None),
                "home_id": clubs[0][0], "home": clubs[0][1], "away_id": clubs[1][0], "away": clubs[1][1],
                "result": result if result and result != "-:-" else None,
                "played": bool(result and result != "-:-"),
            })
    return out


def parse_match_report(html: str) -> dict:
    """Feuille de match : en-tête (date, journée, compétition, score, équipes) et événements."""
    doc = parse_html(html)
    out = {"date": None, "matchday": None, "competition_id": None, "competition": None,
           "home_id": None, "away_id": None, "home": None, "away": None,
           "score": None, "extra_time": False, "goals": [], "subs": [], "cards": [], "bench": {}}
    datum = doc.find("p", cls="sb-datum")
    if datum:
        txt = datum.text()
        out["date"] = parse_short_date(txt)
        md = re.match(r"\s*(.+?)\s*\|", txt)
        if md:
            head = md.group(1).strip()
            n = re.match(r"(\d+)\.\s*Matchday", head)
            out["matchday"] = int(n.group(1)) if n else head
        a = next((a for a in datum.find_all("a") if "wettbewerb/" in (a.get("href") or "")), None)
        if a:
            out["competition_id"] = competition_code(a.get("href"))
    # compétition : titre de la première boîte de la feuille (lien startseite/wettbewerb ou pokalwettbewerb)
    for h2 in doc.find_all("h2"):
        a = next((a for a in h2.find_all("a") if re.search(r"/(?:pokal)?wettbewerb/[A-Za-z0-9]+", a.get("href") or "")
                  and "startseite" in (a.get("href") or "")), None)
        if a is not None:
            out["competition_id"] = competition_code(a.get("href")) or out["competition_id"]
            out["competition"] = a.get("title") or a.text() or None
            break
    for side, key in (("sb-heim", "home"), ("sb-gast", "away")):
        team = doc.find("div", cls=side)
        link = team.find("a", cls="sb-vereinslink") if team else None
        if link:
            out[key + "_id"], out[key] = id_from_href(link.get("href")), link.get("title") or link.text()
    end = doc.find("div", cls="sb-endstand")
    if end:
        out["score"] = _score(end.own_text() or end.text())
        out["extra_time"] = bool(re.search(r"AET|a\.e\.t|extra time|n\.V\.", end.text(), re.I))

    def minute(li: Node) -> int | None:
        clock = li.find("span", cls="sb-sprite-uhr-klein")
        return sprite_minute(clock.get("style")) if clock else None

    def team_of(li: Node) -> str:
        return "home" if "sb-aktion-heim" in li.classes() else "away"

    tore = doc.find("div", id="sb-tore")
    for li in (tore.find_all("li") if tore else []):
        action = li.find("div", cls="sb-aktion-aktion")
        if action is None:
            continue
        links = [a for a in action.find_all("a") if "/spieler/" in (a.get("href") or "")]
        text = action.text()
        own = bool(re.search(r"own[- ]goal", text, re.I))
        scorer = id_from_href(links[0].get("href")) if links else None
        assist = None
        if "Assist:" in text and len(links) > 1:
            assist = id_from_href(links[1].get("href"))
        out["goals"].append({"minute": minute(li), "team": team_of(li), "scorer_id": scorer,
                             "assist_id": assist, "own_goal": own, "penalty": "penalty" in text.lower()})
    wechsel = doc.find("div", id="sb-wechsel")
    for li in (wechsel.find_all("li") if wechsel else []):
        ein = li.find("span", cls="sb-aktion-wechsel-ein")
        aus = li.find("span", cls="sb-aktion-wechsel-aus")
        pin = _player_link(ein) if ein else None
        pout = _player_link(aus) if aus else None
        if pin is None and pout is None:
            continue
        reason = re.sub(r"^.*?,\s*", "", aus.text()) if aus and "," in aus.text() else None
        out["subs"].append({"minute": minute(li), "team": team_of(li),
                            "in_id": id_from_href(pin.get("href")) if pin else None,
                            "out_id": id_from_href(pout.get("href")) if pout else None, "reason": reason})
    karten = doc.find("div", id="sb-karten")
    for li in (karten.find_all("li") if karten else []):
        action = li.find("div", cls="sb-aktion-aktion")
        link = _player_link(action) if action else None
        if link is None:
            continue
        classes = " ".join(" ".join(s.classes()) for s in li.find_all("span"))
        kind = "second_yellow" if "sb-gelbrot" in classes else "red" if "sb-rot" in classes else "yellow"
        out["cards"].append({"minute": minute(li), "team": team_of(li), "player_id": id_from_href(link.get("href")),
                             "type": kind})
    # Banc (table.ersatzbank) : la page index le donne aussi, avec l'abréviation du poste.
    benches = doc.find_all("table", cls="ersatzbank")
    for i, table in enumerate(benches[:2]):
        rows = []
        for tr in table.find_all("tr"):
            link = _player_link(tr)
            if link is None:
                continue
            cells = tr.cells()
            rows.append({"id": id_from_href(link.get("href")), "name": link.get("title") or link.text(),
                         "position": cells[-1].text() if cells else None})
        out["bench"]["home" if i == 0 else "away"] = rows
    return out


def parse_lineups(html: str) -> dict:
    """Page compositions : {club_id: {"start": [...], "bench": [...]}} avec poste occupé."""
    doc = parse_html(html)
    out: dict[str, dict] = {}
    for box in doc.find_all("div", cls="box"):
        h2 = box.find("h2")
        if h2 is None:
            continue
        title = h2.text().lower()
        key = "start" if "starting" in title or "line-up" in title else "bench" if "substitute" in title else None
        if key is None:
            continue
        club_a = next((a for a in h2.find_all("a") if "/verein/" in (a.get("href") or "")), None)
        cid = id_from_href(club_a.get("href")) if club_a else None
        if cid is None:
            continue
        entry = out.setdefault(cid, {"name": club_a.get("title") or club_a.text(), "start": [], "bench": []})
        for table in box.find_all("table"):
            if table.ancestor("table") is not None or "inline-table" in table.classes():
                continue
            for tr in table.find_all("tr"):
                if tr.ancestor("tr") is not None:
                    continue
                link = next((a for a in tr.find_all("a") if "/spieler/" in (a.get("href") or "") and a.text().strip()), None)
                if link is None:
                    continue
                cells = tr.cells()
                pos = cells[0].get("title") if cells else None
                if not pos:
                    inline = tr.find("table", cls="inline-table")
                    rows = inline.find_all("tr") if inline else []
                    if len(rows) >= 2:
                        pos = rows[-1].text().split(",")[0].strip()
                entry[key].append({"id": id_from_href(link.get("href")), "name": link.get("title") or link.text(),
                                   "position": pos or None})
    return out


def parse_injuries(html: str) -> list[dict]:
    """Historique de blessures d'un joueur : [{season, injury, from, until, days, games_missed}]."""
    doc = parse_html(html)
    out = []
    for table in doc.find_all("table"):
        headers = [th.text().lower() for th in table.find_all("th")]
        if "injury" not in headers:
            continue
        for tr in table.find_all("tr"):
            cells = [c.text() for c in tr.cells()]
            if len(cells) < 6 or not re.search(r"\d{2}/\d{2}/\d{4}", cells[2]):
                continue
            days = re.search(r"\d+", cells[4])
            out.append({"season": cells[0], "injury": cells[1], "from": parse_short_date(cells[2]),
                        "until": parse_short_date(cells[3]), "days": int(days.group()) if days else None,
                        "games_missed": int(cells[5]) if cells[5].strip().isdigit() else None})
    return out


def parse_club_absences(html: str) -> list[dict]:
    """Page « Suspensions et blessures » d'un club : absences en cours au jour de la lecture."""
    doc = parse_html(html)
    out = []
    for box in doc.find_all("div", cls="box"):
        h2 = box.find("h2")
        if h2 is None or "risk" in h2.text().lower():
            continue
        for table in box.find_all("table"):
            headers = [th.text().lower() for th in table.find_all("th")]
            if "reason" not in headers:
                continue
            section = None
            for tr in table.find_all("tr"):
                if tr.ancestor("tr") is not None:
                    continue
                cells = tr.cells()
                link = _player_link(tr)
                if link is None:
                    if len(cells) == 1 and cells[0].text():
                        section = cells[0].text().lower()
                    continue
                texts = [c.text() for c in cells]
                idx = {h: i for i, h in enumerate(headers)}

                def col(name, default=None):
                    i = idx.get(name)
                    return texts[i] if i is not None and i < len(texts) else default

                reason = col("reason", "")
                low = (reason + " " + (section or "")).lower()
                cat = "suspended" if ("suspens" in low or "ban" in low or "card" in low) else "injured"
                missed = col("missed matches", "")
                out.append({"player_id": id_from_href(link.get("href")), "name": link.get("title") or link.text(),
                            "reason": reason, "category": cat, "since": parse_short_date(col("since")),
                            "until": parse_short_date(col("expected return")),
                            "missed_matches": int(missed) if missed.strip().isdigit() else None})
    return out


# --------------------------------------------------------------------------- assemblage
def _eff(minute: int | None, default: int) -> int:
    """Minute effective d'un remplacement : 46' (début de seconde période) compte 45."""
    if minute is None:
        return default
    return 45 if minute == 46 else minute


def player_match_entry(pid: str, club_id: str, match: dict, report: dict, lineup: dict) -> dict | None:
    """Ligne de journal d'un joueur pour une rencontre de son club (None si club absent de la feuille)."""
    side = "home" if report.get("home_id") == club_id else "away" if report.get("away_id") == club_id else None
    if side is None or club_id not in lineup:
        return None
    own = lineup[club_id]
    opp_side = "away" if side == "home" else "home"
    score = report.get("score")
    full = 120 if report.get("extra_time") else 90
    gf, ga = (score if side == "home" else (score[1], score[0])) if score else (None, None)
    outcome = None if gf is None else "V" if gf > ga else "N" if gf == ga else "D"
    start = next((r for r in own["start"] if r["id"] == pid), None)
    bench = next((r for r in own["bench"] if r["id"] == pid), None)
    entry = {"match_id": match["id"], "date": report.get("date") or match.get("date"),
             "competition_id": report.get("competition_id") or match.get("competition_id"),
             "competition": report.get("competition") or match.get("competition"),
             "matchday": report.get("matchday") if report.get("matchday") is not None else match.get("matchday"),
             "home": side == "home", "opponent": report.get(opp_side) or match.get(opp_side),
             "opponent_id": report.get(opp_side + "_id") or match.get(opp_side + "_id"),
             "result": f"{gf}:{ga}" if gf is not None else match.get("result"), "outcome": outcome,
             "status": None, "position": (start or bench or {}).get("position"), "started": start is not None,
             "minutes": 0, "goals": 0, "assists": 0, "yellow": 0, "red": 0,
             "subbed_on_minute": None, "subbed_off_minute": None, "conceded": None}
    subs = [s for s in report["subs"] if s["team"] == side]
    cards = [c for c in report["cards"] if c["player_id"] == pid]
    on_m = next((s["minute"] for s in subs if s["in_id"] == pid), None)
    off_m = next((s["minute"] for s in subs if s["out_id"] == pid), None)
    red_m = next((c["minute"] for c in cards if c["type"] in ("red", "second_yellow")), None)
    if start is None and (bench is None or on_m is None):
        entry["status"] = "bench" if bench is not None else "not_in_squad"
        return entry
    entry["status"] = "played"
    on = 0 if start is not None else _eff(on_m, 0)
    off = full
    if off_m is not None:
        off = _eff(off_m, full)
    if red_m is not None:
        off = min(off, red_m)
    entry["minutes"] = max(0, off - on)
    entry["subbed_on_minute"], entry["subbed_off_minute"] = on_m, off_m
    for g in report["goals"]:
        if g["team"] == side and not g["own_goal"] and g["scorer_id"] == pid:
            entry["goals"] += 1
        if g["team"] == side and g["assist_id"] == pid:
            entry["assists"] += 1
    entry["yellow"] = sum(1 for c in cards if c["type"] == "yellow")
    entry["red"] = 1 if red_m is not None else 0
    conceded = 0
    for g in report["goals"]:
        if g["team"] == opp_side:
            gm = g["minute"] if g["minute"] is not None else 0
            if on < gm <= off or (gm == 0 and on == 0):
                conceded += 1
    entry["conceded"] = conceded
    return entry


def _in_injury(d: str, injuries: list[dict], today: str) -> dict | None:
    for inj in injuries:
        start, end = inj.get("from"), inj.get("until") or today
        if start and start <= d <= end:
            return inj
    return None


def assemble(players: dict[str, dict], club_matches: dict[str, list[dict]], reports: dict[str, dict],
             lineups: dict[str, dict], injuries: dict[str, list[dict]], absences: dict[str, list[dict]],
             today: str, joined: dict[str, str] | None = None) -> dict:
    """Journal par joueur. `players` : {pid: {"name", "club_id"}} ; `joined` : date d'arrivée au club."""
    joined = joined or {}
    logs: dict[str, dict] = {}
    red_before: dict[str, set[str]] = {}   # pid → ids des matchs suivant un carton rouge
    for cid, matches in club_matches.items():
        played = [m for m in matches if m["played"] and m["id"] in reports and m["id"] in lineups
                  and cid in lineups[m["id"]] and (m.get("date") or "") <= today]
        played.sort(key=lambda m: (m.get("date") or "", m["id"]))
        for i, m in enumerate(played[:-1]):
            rep = reports[m["id"]]
            for c in rep["cards"]:
                if c["type"] in ("red", "second_yellow"):
                    red_before.setdefault(c["player_id"], set()).add(played[i + 1]["id"])
    for pid, info in players.items():
        cid = info["club_id"]
        matches = sorted([m for m in club_matches.get(cid, []) if m["played"] and (m.get("date") or "") <= today],
                         key=lambda m: (m.get("date") or "", m["id"]))
        entries, skipped = [], 0
        cur_abs = next((a for a in absences.get(cid, []) if a["player_id"] == pid), None)
        for m in matches:
            rep, lu = reports.get(m["id"]), lineups.get(m["id"])
            if not rep or not lu or cid not in lu or not lu[cid]["start"]:
                skipped += 1
                continue
            e = player_match_entry(pid, cid, m, rep, lu)
            if e is None:
                skipped += 1
                continue
            if e["status"] == "not_in_squad":
                inj = _in_injury(e["date"] or "", injuries.get(pid, []), today)
                if inj:
                    e["status"], e["status_detail"] = "injured", inj["injury"]
                elif m["id"] in red_before.get(pid, set()) or (
                        cur_abs and cur_abs["category"] == "suspended" and (cur_abs["since"] or "") <= (e["date"] or "")):
                    e["status"], e["status_detail"] = "suspended", (cur_abs or {}).get("reason") or "carton rouge au match précédent"
                elif cur_abs and cur_abs["category"] == "injured" and (cur_abs["since"] or "9") <= (e["date"] or ""):
                    e["status"], e["status_detail"] = "injured", cur_abs["reason"]
                elif joined.get(pid) and (e["date"] or "") < joined[pid]:
                    e["status"], e["status_detail"] = "other", "pas encore au club"
            entries.append(e)
        current = None
        if cur_abs:
            current = {"status": cur_abs["category"], "reason": cur_abs["reason"], "since": cur_abs["since"],
                       "until": cur_abs["until"], "missed_matches": cur_abs["missed_matches"]}
        elif entries and entries[-1]["status"] == "injured":
            current = {"status": "injured", "reason": entries[-1].get("status_detail"), "since": None, "until": None,
                       "missed_matches": None}
        ongoing = [i for i in injuries.get(pid, []) if i.get("from") and not i.get("until")]
        if current is None and ongoing:
            current = {"status": "injured", "reason": ongoing[0]["injury"], "since": ongoing[0]["from"],
                       "until": None, "missed_matches": None}
        logs[pid] = {"name": info.get("name"), "club_id": cid, "entries": entries,
                     "team_matches": len(entries), "matches_without_lineup": skipped,
                     "current_status": current,
                     "injuries_2627": [i for i in injuries.get(pid, []) if (i.get("until") or "9999") >= "2026-07-01"]}
    return logs


# --------------------------------------------------------------------------- client
class MatchlogClient(TransfermarktDirect):
    def fixtures(self, club_id: str, season: int) -> list[dict]:
        return parse_fixtures(self.fetch(f"/-/spielplandatum/verein/{club_id}/plus/1?saison_id={season}"), club_id)

    def match_report(self, match_id: str) -> dict:
        return parse_match_report(self.fetch(f"/spielbericht/index/spielbericht/{match_id}"))

    def lineups(self, match_id: str) -> dict:
        return parse_lineups(self.fetch(f"/spielbericht/aufstellung/spielbericht/{match_id}"))

    def injuries(self, player_id: str, slug: str | None = None) -> list[dict]:
        return parse_injuries(self.fetch(f"/{slug or '-'}/verletzungen/spieler/{player_id}"))

    def club_absences(self, club_id: str) -> list[dict]:
        return parse_club_absences(self.fetch(f"/-/sperrenundverletzungen/verein/{club_id}/plus/1"))


def current_club_id(p: dict) -> str | None:
    """Club actuel d'un joueur du vivier : ligne de statistiques de la saison en cours."""
    for r in p.get("stats", []):
        if r.get("season") == "26/27" and r.get("club_id"):
            return r["club_id"]
    return None


def joined_date(p: dict) -> str | None:
    """Date d'arrivée au club actuel (effectif : joined_on ; vivier : dernier transfert ≤ aujourd'hui)."""
    if p.get("joined_on"):
        return p["joined_on"]
    dates = sorted(t["date"] for t in p.get("transfers", []) if t.get("date") and t["date"] <= date.today().isoformat())
    return dates[-1] if dates else None


def _pmap(pool: ThreadPoolExecutor, fn, items, label: str, verbose: bool = True):
    results, done, t0 = {}, 0, time.time()
    futures = {pool.submit(fn, it): it for it in items}
    for fut, it in futures.items():
        key = it if not isinstance(it, (list, tuple)) else it[0]
        try:
            results[key] = fut.result()
        except Exception as e:  # une page défaillante n'arrête pas la collecte
            print(f"  ! {label} {key} : {e}", flush=True)
            results[key] = None
        done += 1
        if done % 50 == 0 or done == len(items):
            print(f"  {label} : {done}/{len(items)} ({time.time() - t0:.0f} s)", flush=True)
    return results


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, default=None, help="Saison Transfermarkt (défaut : en cours, 2026 pour 2026/27)")
    ap.add_argument("--today", default=None, help="Date de référence AAAA-MM-JJ (défaut : aujourd'hui)")
    ap.add_argument("--clubs", nargs="*", default=None, help="Limiter à ces ids de club (test)")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--skip-injuries", action="store_true", help="Ne lit pas l'historique de blessures des joueurs")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    season = args.season or current_season()
    today = args.today or date.today().isoformat()

    cand = json.loads((DATA / "candidates.json").read_text())
    squad = json.loads((DATA / "squad.json").read_text())
    players: dict[str, dict] = {}
    joined: dict[str, str] = {}
    for p in squad["players"]:
        players[p["id"]] = {"name": p.get("name"), "club_id": squad["club_id"], "slug": p.get("slug"), "squad": True}
        if joined_date(p):
            joined[p["id"]] = joined_date(p)
    for p in cand["players"]:
        cid = current_club_id(p)
        if not cid:
            continue
        players.setdefault(p["id"], {"name": p.get("name"), "club_id": cid,
                                     "slug": (p.get("url") or "").split("/")[3] if (p.get("url") or "").count("/") > 3 else None,
                                     "squad": False})
        if joined_date(p):
            joined.setdefault(p["id"], joined_date(p))
    clubs = sorted({info["club_id"] for info in players.values()})
    if args.clubs:
        clubs = [c for c in clubs if c in set(args.clubs)]
        players = {pid: i for pid, i in players.items() if i["club_id"] in set(clubs)}
    print(f"Journal {season}/{season % 100 + 1} au {today} : {len(players)} joueurs, {len(clubs)} clubs", flush=True)

    client = MatchlogClient()
    pool = ThreadPoolExecutor(max_workers=max(1, args.workers))

    # 1. Calendriers
    fixtures = _pmap(pool, lambda cid: client.fixtures(cid, season), clubs, "calendriers")
    club_matches = {cid: (fixtures.get(cid) or []) for cid in clubs}
    todo = sorted({m["id"] for ms in club_matches.values() for m in ms if m["played"] and (m.get("date") or "") <= today})
    print(f"  {sum(len(v) for v in club_matches.values())} rencontres listées, {len(todo)} feuilles de match jouées à lire", flush=True)

    # 2. Feuilles de match + compositions
    reports = _pmap(pool, client.match_report, todo, "feuilles de match")
    lineups = _pmap(pool, client.lineups, todo, "compositions")
    reports = {k: v for k, v in reports.items() if v}
    lineups = {k: v for k, v in lineups.items() if v}

    # 3. Absences en cours (clubs), puis blessures des joueurs absents d'au moins une feuille
    absences = _pmap(pool, client.club_absences, clubs, "absences clubs")
    absences = {k: (v or []) for k, v in absences.items()}
    first = assemble(players, club_matches, reports, lineups, {}, absences, today, joined)
    need = [pid for pid, log in first.items()
            if any(e["status"] == "not_in_squad" for e in log["entries"])] if not args.skip_injuries else []
    print(f"  {len(need)} joueurs absents d'au moins une feuille → historique de blessures", flush=True)
    inj = _pmap(pool, lambda pid: client.injuries(pid, players[pid].get("slug")), need, "blessures")
    injuries = {k: (v or []) for k, v in inj.items()}
    pool.shutdown()

    logs = assemble(players, club_matches, reports, lineups, injuries, absences, today, joined)

    # Compétitions couvertes (journées jouées par championnat)
    comps: dict[str, dict] = {}
    for cid, ms in club_matches.items():
        for m in ms:
            if not m["played"] or (m.get("date") or "") > today or m["id"] not in reports:
                continue
            rep = reports[m["id"]]
            code = rep.get("competition_id") or m.get("competition_id")
            c = comps.setdefault(code or "?", {"name": rep.get("competition") or m.get("competition"),
                                               "type": competition_type(code, rep.get("competition") or m.get("competition")),
                                               "matches": set(), "max_matchday": 0})
            c["matches"].add(m["id"])
            if isinstance(m.get("matchday"), int):
                c["max_matchday"] = max(c["max_matchday"], m["matchday"])
    for c in comps.values():
        c["matches"] = len(c["matches"])

    matches_out = {}
    for cid, ms in club_matches.items():
        for m in ms:
            if m["id"] in reports:
                rep = reports[m["id"]]
                matches_out[m["id"]] = {k: rep.get(k) or m.get(k) for k in ("date", "competition_id", "competition",
                                                                           "matchday", "home_id", "home", "away_id", "away")}
                matches_out[m["id"]]["score"] = rep.get("score")
                matches_out[m["id"]]["extra_time"] = rep.get("extra_time")
                matches_out[m["id"]]["lineups"] = m["id"] in lineups and bool(lineups[m["id"]])
    with_log = sum(1 for l in logs.values() if l["entries"])
    out = {"genere": date.today().isoformat(), "today": today, "season": f"{season % 100:02d}/{(season + 1) % 100:02d}",
           "source": "www.transfermarkt.com : calendriers des clubs, feuilles de match (index + compositions), "
                     "absences des clubs, historique de blessures",
           "coverage": {"players": len(players), "players_with_log": with_log, "clubs": len(clubs),
                        "matches_listed": len(todo), "matches_read": len(reports), "lineups_read": len(lineups),
                        "injury_pages": len(need), "requests": client.calls, "captchas": client.captchas},
           "competitions": comps,
           "clubs": {cid: {"matches": [m["id"] for m in ms if m["played"] and (m.get("date") or "") <= today],
                           "upcoming": [m["id"] for m in ms if not m["played"]]} for cid, ms in club_matches.items()},
           "matches": matches_out,
           "players": logs}
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"→ {args.out} : {with_log}/{len(players)} joueurs avec journal, {len(reports)}/{len(todo)} feuilles lues, "
          f"{client.calls} requêtes ({client.captchas} captchas)", flush=True)
    print("Compétitions :", {k: (v['type'], v['matches'], v['max_matchday']) for k, v in comps.items()}, flush=True)


if __name__ == "__main__":
    main()
