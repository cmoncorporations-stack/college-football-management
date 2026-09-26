"""Enrichissement Sofascore : xG, xA, note Sofascore et stats détaillées par joueur.

    python -m moneyball.sofascore --tournaments 216 215 --season 2026/27

Sofascore n'a pas d'API publique documentée ; ce module lit les mêmes points
d'entrée JSON que l'application mobile (api.sofascore.com/api/v1). Usage interne
uniquement, cadence limitée à un appel par seconde, cache disque 72 h.

Identifiants de tournoi Sofascore (visibles dans l'URL sofascore.com) :
    216  Challenge League (SUI)      215  Super League (SUI)
Pour un autre championnat, ouvrir sa page Sofascore et relever le nombre en fin d'URL.

Sortie : data/sofascore.json — { "<tournament>/<season>": {joueurs...} } — puis
`merge_into_candidates` rattache chaque joueur Transfermarkt à sa ligne Sofascore
(nom normalisé + année de naissance) et ajoute un bloc `perf` (source sofascore) à sa fiche.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
API = "https://api.sofascore.com/api/v1"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# Champs Sofascore conservés (nom Sofascore → nom interne).
FIELDS = {
    "rating": "rating", "appearances": "apps", "minutesPlayed": "minutes",
    "goals": "goals", "assists": "assists",
    "expectedGoals": "xg", "expectedAssists": "xa",
    "keyPasses": "key_passes", "bigChancesCreated": "big_chances_created",
    "totalShots": "shots", "shotsOnTarget": "shots_on_target",
    "successfulDribbles": "dribbles", "accuratePassesPercentage": "pass_pct",
    "tackles": "tackles", "interceptions": "interceptions", "clearances": "clearances",
    "groundDuelsWonPercentage": "ground_duels_pct", "aerialDuelsWonPercentage": "aerial_duels_pct",
    "possessionLost": "possession_lost", "yellowCards": "yellow", "redCards": "red",
    "saves": "saves", "goalsConcededOutsideTheBox": "gc_outside_box",
    "goalsConcededInsideTheBox": "gc_inside_box", "cleanSheet": "clean_sheets",
}


def norm_name(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


class SofascoreClient:
    def __init__(self, cache_dir: Path = DATA / "cache" / "sofascore", cache_ttl_h: float = 72,
                 min_interval_s: float = 1.0):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl_s = cache_ttl_h * 3600
        self.min_interval_s = min_interval_s
        self._last = 0.0

    def get(self, path: str) -> dict:
        key = re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_")
        cached = self.cache_dir / f"{key}.json"
        if cached.exists() and time.time() - cached.stat().st_mtime < self.cache_ttl_s:
            return json.loads(cached.read_text())
        delay = 2.0
        for attempt in range(4):
            wait = self.min_interval_s - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            req = urllib.request.Request(API + path, headers={"User-Agent": UA, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read().decode())
                cached.write_text(json.dumps(data))
                return data
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return {}
                if e.code not in (403, 429, 500, 502, 503) or attempt == 3:
                    raise
            except urllib.error.URLError:
                if attempt == 3:
                    raise
            time.sleep(delay)
            delay *= 2
        return {}

    # -- endpoints ---------------------------------------------------------
    def seasons(self, tournament: int) -> list[dict]:
        return self.get(f"/unique-tournament/{tournament}/seasons").get("seasons", [])

    def season_id(self, tournament: int, season_name: str | None) -> tuple[int, str]:
        seasons = self.seasons(tournament)
        if not seasons:
            sys.exit(f"Tournoi Sofascore {tournament} : aucune saison trouvée.")
        if season_name:
            for s in seasons:
                if s.get("year") == season_name or s.get("name", "").endswith(season_name):
                    return s["id"], s.get("year", season_name)
            sys.exit(f"Saison {season_name} introuvable. Disponibles : {[s.get('year') for s in seasons]}")
        return seasons[0]["id"], seasons[0].get("year", "")

    def player_season_stats(self, tournament: int, season: int) -> list[dict]:
        """Toutes les lignes joueur de la saison (statistiques 'overall', paginées)."""
        rows, offset = [], 0
        fields = ",".join(FIELDS)
        while True:
            page = self.get(f"/unique-tournament/{tournament}/season/{season}/statistics"
                            f"?limit=100&offset={offset}&order=-rating&accumulation=total&fields={fields}")
            results = page.get("results", [])
            rows.extend(results)
            if len(results) < 100 or not page.get("page") or page["page"] >= page.get("pages", 1):
                break
            offset += 100
        return rows

    def player(self, player_id: int) -> dict:
        return self.get(f"/player/{player_id}").get("player", {})


def _birth_year(p: dict) -> int | None:
    ts = p.get("dateOfBirthTimestamp")
    return datetime.fromtimestamp(ts, tz=timezone.utc).year if ts else None


def collect(client: SofascoreClient, tournament: int, season_name: str | None, with_birth: bool) -> dict:
    sid, label = client.season_id(tournament, season_name)
    out = {}
    for row in client.player_season_stats(tournament, sid):
        pl, team = row.get("player", {}), row.get("team", {})
        rec = {FIELDS[k]: row.get(k) for k in FIELDS if k in row}
        rec.update({"sofascore_id": pl.get("id"), "name": pl.get("name"), "team": team.get("name"),
                    "position": pl.get("position"), "birth_year": _birth_year(pl)})
        if with_birth and rec["birth_year"] is None and pl.get("id"):
            rec["birth_year"] = _birth_year(client.player(pl["id"]))
        out[str(pl.get("id"))] = rec
    print(f"Sofascore {tournament} / {label} : {len(out)} joueurs")
    return {"tournament": tournament, "season_id": sid, "season": label, "players": out}


def merge_into_candidates(candidates: list[dict], sofa: dict) -> int:
    """Ajoute un bloc `perf` à chaque candidat retrouvé. Retourne le nombre d'appariements."""
    by_name: dict[str, list[dict]] = {}
    for block in sofa.values():
        for rec in block["players"].values():
            by_name.setdefault(norm_name(rec["name"]), []).append({**rec, "season": block["season"]})
    hits = 0
    for p in candidates:
        cands = by_name.get(norm_name(p.get("name")), [])
        if not cands:
            # Tentative « prénom initiale + nom » (Sofascore abrège parfois)
            parts = norm_name(p.get("name")).split()
            if len(parts) >= 2:
                cands = by_name.get(parts[-1], []) + by_name.get(f"{parts[0][0]} {parts[-1]}", [])
        if not cands:
            continue
        by = None
        if p.get("age") is not None:
            by = date.today().year - p["age"]
        best = next((c for c in cands if by and c.get("birth_year") in (by, by - 1)), None) or (cands[0] if len(cands) == 1 else None)
        if best:
            p["perf"] = {**best, "source": "sofascore"}
            hits += 1
    return hits


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tournaments", nargs="+", type=int, default=[216, 215])
    ap.add_argument("--season", default=None, help="ex. 2026/27 ; défaut : saison en cours")
    ap.add_argument("--previous", action="store_true", help="Ajoute aussi la saison précédente")
    ap.add_argument("--no-birth", action="store_true", help="Ne pas appeler /player/{id} pour l'année de naissance")
    ap.add_argument("--merge", action="store_true", help="Rattache aux candidats de data/candidates.json")
    args = ap.parse_args(argv)

    client = SofascoreClient()
    out = {}
    for t in args.tournaments:
        block = collect(client, t, args.season, not args.no_birth)
        out[f"{t}/{block['season']}"] = block
        if args.previous:
            seasons = client.seasons(t)
            idx = next((i for i, s in enumerate(seasons) if s["id"] == block["season_id"]), None)
            if idx is not None and idx + 1 < len(seasons):
                prev = collect(client, t, seasons[idx + 1].get("year"), not args.no_birth)
                out[f"{t}/{prev['season']}"] = prev
    (DATA / "sofascore.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print("→ data/sofascore.json")

    if args.merge:
        path = DATA / "candidates.json"
        cand = json.loads(path.read_text())
        hits = merge_into_candidates(cand["players"], out)
        path.write_text(json.dumps(cand, ensure_ascii=False, indent=1))
        print(f"{hits}/{len(cand['players'])} candidats enrichis → data/candidates.json")


if __name__ == "__main__":
    main()
