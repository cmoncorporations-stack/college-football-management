"""Enrichissement API-Football (api-sports.io) : l'API gratuite documentée retenue.

    export API_FOOTBALL_KEY=...                 # clé gratuite : dashboard.api-football.com
    python -m moneyball.apifootball --leagues 208 207 --season 2026 --merge

Plan gratuit : 100 requêtes par jour, tous les points d'entrée, 1 100+ championnats.
Le point d'entrée /players (statistiques saison par championnat) rend 20 joueurs par
page : la Challenge League tient en ~15 pages, la Super League en ~20. Le cache
disque (72 h) évite de rejouer les pages déjà lues.

Identifiants de championnat API-Football :
    208  Challenge League (SUI)   207  Super League (SUI)
    62   Ligue 2 (FRA)            63   National (FRA)      145  Challenger Pro League (BEL)
`--find "Challenge League"` interroge /leagues pour vérifier un id.

Ce que l'on récupère par joueur et par saison : matchs, titularisations, minutes,
note moyenne (échelle 1-10), buts, passes décisives, tirs (total / cadrés), passes
clés, précision de passe, tacles, interceptions, duels, dribbles, cartons, penaltys,
et la date de naissance exacte (appariement fiable avec Transfermarkt).
API-Football ne fournit pas de xG au niveau saison : le modèle utilise alors
buts + passes + 0,1 × passes clés comme production, et la note pour un tiers.

Sortie : data/apifootball.json puis, avec --merge, un bloc `perf` par candidat.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
API = "https://v3.football.api-sports.io"


def norm_name(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


class ApiFootballClient:
    def __init__(self, key: str | None = None, cache_dir: Path = DATA / "cache" / "apifootball",
                 cache_ttl_h: float = 72, min_interval_s: float = 6.5):
        # 6,5 s entre deux appels : sous la limite de 10 requêtes/minute du plan gratuit.
        self.key = key or os.environ.get("API_FOOTBALL_KEY")
        if not self.key:
            sys.exit("Clé manquante : export API_FOOTBALL_KEY=... (gratuite sur dashboard.api-football.com)")
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl_s = cache_ttl_h * 3600
        self.min_interval_s = min_interval_s
        self._last = 0.0
        self.calls = 0

    def get(self, path: str, **params) -> dict:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{API}{path}?{query}" if query else f"{API}{path}"
        cached = self.cache_dir / (re.sub(r"[^A-Za-z0-9]+", "_", path + query).strip("_") + ".json")
        if cached.exists() and time.time() - cached.stat().st_mtime < self.cache_ttl_s:
            return json.loads(cached.read_text())
        delay = 5.0
        for attempt in range(4):
            wait = self.min_interval_s - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            self.calls += 1
            req = urllib.request.Request(url, headers={"x-apisports-key": self.key, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 3:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise
            errors = data.get("errors")
            if errors and not (isinstance(errors, list) and not errors):
                # L'API renvoie 200 avec un objet errors quand la clé ou le quota pose problème
                raise RuntimeError(f"API-Football : {errors}")
            cached.write_text(json.dumps(data, ensure_ascii=False))
            return data
        return {}

    def find_league(self, name: str) -> list[dict]:
        rows = self.get("/leagues", search=name).get("response", [])
        return [{"id": r["league"]["id"], "name": r["league"]["name"], "country": r["country"]["name"],
                 "seasons": [s["year"] for s in r.get("seasons", [])][-3:]} for r in rows]

    def league_players(self, league: int, season: int) -> list[dict]:
        rows, page = [], 1
        while True:
            data = self.get("/players", league=league, season=season, page=page)
            rows.extend(data.get("response", []))
            paging = data.get("paging") or {}
            if page >= (paging.get("total") or 1):
                break
            page += 1
        return rows

    def team_squad(self, team: int) -> list[dict]:
        res = self.get("/players/squads", team=team).get("response", [])
        return res[0].get("players", []) if res else []


def _num(v):
    return None if v in (None, "") else float(v)


def normalize_row(row: dict, league: int, season: int) -> dict | None:
    """Une ligne /players → bloc `perf` (statistiques du championnat demandé uniquement)."""
    pl = row.get("player", {})
    stats = [s for s in row.get("statistics", []) if (s.get("league") or {}).get("id") == league] or row.get("statistics", [])
    if not stats:
        return None
    s = stats[0]
    g, sh, pa, ta, du, dr, ca, pe = (s.get(k) or {} for k in ("games", "shots", "passes", "tackles", "duels", "dribbles", "cards", "penalty"))
    goals = s.get("goals") or {}
    return {
        "source": "api-football", "api_football_id": pl.get("id"), "name": pl.get("name"),
        "birth_date": (pl.get("birth") or {}).get("date"), "nationality": pl.get("nationality"),
        "team": (s.get("team") or {}).get("name"), "team_id": (s.get("team") or {}).get("id"),
        "season": f"{season}/{str(season + 1)[-2:]}", "position": g.get("position"),
        "apps": g.get("appearences") or 0, "lineups": g.get("lineups") or 0, "minutes": g.get("minutes") or 0,
        "rating": _num(g.get("rating")),
        "goals": goals.get("total") or 0, "assists": goals.get("assists") or 0,
        "conceded": goals.get("conceded"), "saves": goals.get("saves"),
        "shots": sh.get("total") or 0, "shots_on_target": sh.get("on") or 0,
        "key_passes": pa.get("key") or 0, "pass_pct": _num(pa.get("accuracy")),
        "tackles": ta.get("total") or 0, "interceptions": ta.get("interceptions") or 0,
        "duels": du.get("total") or 0, "duels_won": du.get("won") or 0,
        "dribbles": dr.get("attempts") or 0, "dribbles_won": dr.get("success") or 0,
        "yellow": ca.get("yellow") or 0, "red": (ca.get("red") or 0) + (ca.get("yellowred") or 0),
        "penalties_scored": pe.get("scored") or 0, "penalties_missed": pe.get("missed") or 0,
    }


def collect(client: ApiFootballClient, league: int, season: int) -> dict:
    players = {}
    for row in client.league_players(league, season):
        rec = normalize_row(row, league, season)
        if rec and rec["minutes"]:
            players[str(rec["api_football_id"])] = rec
    print(f"API-Football {league} / {season}-{season + 1} : {len(players)} joueurs ({client.calls} appels)")
    return {"league": league, "season": season, "players": players}


def merge_into_candidates(candidates: list[dict], blocks: dict) -> int:
    """Rattache par date de naissance exacte + nom de famille, sinon nom complet normalisé."""
    by_birth: dict[str, list[dict]] = {}
    by_name: dict[str, list[dict]] = {}
    for b in blocks.values():
        for rec in b["players"].values():
            if rec.get("birth_date"):
                by_birth.setdefault(rec["birth_date"], []).append(rec)
            by_name.setdefault(norm_name(rec["name"]), []).append(rec)
    hits = 0
    for p in candidates:
        found = None
        dob = (p.get("date_of_birth") or "")[:10]
        pname = norm_name(p.get("name"))
        last = pname.split()[-1] if pname else ""
        if dob and dob in by_birth:
            same = by_birth[dob]
            found = next((r for r in same if last and last in norm_name(r["name"])), same[0] if len(same) == 1 else None)
        if not found and pname in by_name and len(by_name[pname]) == 1:
            found = by_name[pname][0]
        if found:
            # Si plusieurs saisons ont été collectées, on garde la plus récente
            p["perf"] = found
            hits += 1
    return hits


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--leagues", nargs="+", type=int, default=[208, 207])
    ap.add_argument("--season", type=int, default=None, help="Année de début (2026 pour 2026/27) ; défaut : saison en cours")
    ap.add_argument("--find", default=None, help="Cherche un championnat par nom et affiche ses ids")
    ap.add_argument("--merge", action="store_true", help="Rattache aux candidats de data/candidates.json")
    args = ap.parse_args(argv)

    client = ApiFootballClient()
    if args.find:
        for r in client.find_league(args.find):
            print(f"  {r['id']:>5}  {r['name']:<32} {r['country']:<16} saisons {r['seasons']}")
        return
    today = date.today()
    season = args.season or (today.year if today.month >= 7 else today.year - 1)
    blocks = {}
    for league in args.leagues:
        b = collect(client, league, season)
        blocks[f"{league}/{season}"] = b
    (DATA / "apifootball.json").write_text(json.dumps(blocks, ensure_ascii=False, indent=1))
    print(f"→ data/apifootball.json · {client.calls} appels API utilisés aujourd'hui")

    if args.merge:
        path = DATA / "candidates.json"
        cand = json.loads(path.read_text())
        hits = merge_into_candidates(cand["players"], blocks)
        path.write_text(json.dumps(cand, ensure_ascii=False, indent=1))
        print(f"{hits}/{len(cand['players'])} candidats enrichis → data/candidates.json")


if __name__ == "__main__":
    main()
