"""Constitue le vivier de joueurs depuis transfermarkt-api.

    python -m moneyball.scout --competitions C2 C1 FR3 FR2 BE2 --max-value 1500000 --max-age 29

Étapes :
1. Trouve Yverdon Sport (recherche par nom ou --club-id) et récupère l'effectif actuel.
2. Pour chaque championnat : clubs → effectifs → premier filtre (âge, valeur marchande).
3. Pour chaque joueur retenu : profil, statistiques, historique de valeur, transferts,
   blessures (optionnel).
4. Écrit data/squad.json et data/candidates.json au format normalisé attendu par model.py.

Le cache disque (data/cache) évite de rappeler l'API pour un même joueur pendant 72 h.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .tm_client import TransfermarktClient

DATA = Path(__file__).resolve().parent.parent / "data"


def _mv_history(mv: dict) -> list[list]:
    return [[h["date"], h.get("market_value")] for h in mv.get("marketValueHistory", []) if h.get("market_value")]


def normalize(pid: str, league_id: str, row: dict, profile: dict, stats: list[dict],
              mv: dict, transfers: dict, injuries: list[dict] | None) -> dict:
    today = date.today()
    injury_days = 0
    for inj in injuries or []:
        if (inj.get("from_date") or "")[:4].isdigit() and int(inj["from_date"][:4]) >= today.year - 2:
            injury_days += inj.get("days") or 0
    pob = profile.get("place_of_birth") or {}
    club = profile.get("club") or {}
    return {
        "id": pid,
        "name": profile.get("name") or row.get("name"),
        "url": profile.get("url"),
        "position": (profile.get("position") or {}).get("main") or row.get("position"),
        "age": profile.get("age") or row.get("age"),
        "date_of_birth": profile.get("date_of_birth") or row.get("date_of_birth"),
        "citizenship": profile.get("citizenship") or row.get("nationality") or [],
        "birth_city": pob.get("city"),
        "birth_country": pob.get("country"),
        "foot": profile.get("foot"),
        "height": profile.get("height"),
        "market_value": profile.get("market_value") or row.get("market_value"),
        "mv_history": _mv_history(mv),
        "contract_expires": club.get("contract_expires") or row.get("contract"),
        "club": club.get("name"),
        "league_id": league_id,
        "stats": [{
            "season": s.get("season_id"), "competition_id": s.get("competition_id"),
            "competition": s.get("competition_name"), "appearances": s.get("appearances") or 0,
            "goals": s.get("goals") or 0, "assists": s.get("assists") or 0,
            "minutes": s.get("minutes_played") or 0, "yellow": s.get("yellow_cards") or 0,
            "red": s.get("red_cards") or 0,
        } for s in stats],
        "transfers": [{"date": t.get("date"), "from": (t.get("club_from") or {}).get("name"),
                       "to": (t.get("club_to") or {}).get("name"), "fee": t.get("fee")}
                      for t in transfers.get("transfers", [])],
        "youth_clubs": transfers.get("youth_clubs") or [],
        "social_media": profile.get("socialMedia") or [],
        "injury_days": injury_days,
    }


def find_yverdon(tm: TransfermarktClient, club_id: str | None) -> str:
    if club_id:
        return club_id
    results = [r for r in tm.search_club("Yverdon") if r.get("country") == "Switzerland"]
    if not results:
        sys.exit("Yverdon Sport introuvable via /clubs/search — passer --club-id.")
    best = max(results, key=lambda r: r.get("market_value") or 0)
    print(f"Yverdon Sport → id {best['id']} ({best['name']})")
    return best["id"]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--competitions", nargs="+", default=["C2", "C1", "FR3", "FR2", "BE2"],
                    help="Codes compétition Transfermarkt à scanner")
    ap.add_argument("--season", default=None, help="Saison Transfermarkt (ex. 2026) ; défaut : en cours")
    ap.add_argument("--club-id", default=None, help="Id Transfermarkt d'Yverdon Sport (sinon recherche)")
    ap.add_argument("--max-value", type=int, default=1_500_000, help="Valeur marchande max (EUR)")
    ap.add_argument("--max-age", type=int, default=29)
    ap.add_argument("--min-age", type=int, default=17)
    ap.add_argument("--injuries", action="store_true", help="Récupère aussi l'historique de blessures (+1 appel/joueur)")
    ap.add_argument("--limit", type=int, default=0, help="Limite de joueurs détaillés (0 = tous), pour tester")
    ap.add_argument("--apifootball", nargs="*", type=int, metavar="LIGUE",
                    help="Enrichit avec API-Football (note, passes clés, tirs…) ; ids de ligue, défaut 208 207. Clé : API_FOOTBALL_KEY")
    ap.add_argument("--sofascore", nargs="*", type=int, metavar="TOURNOI",
                    help="Enrichit avec Sofascore (xG, xA, note) ; ids de tournoi, défaut 216 215")
    args = ap.parse_args(argv)

    tm = TransfermarktClient()
    ys_id = find_yverdon(tm, args.club_id)
    squad = tm.club_players(ys_id, args.season)
    (DATA / "squad.json").write_text(json.dumps({"club_id": ys_id, "players": squad}, ensure_ascii=False, indent=1))
    print(f"Effectif Yverdon : {len(squad)} joueurs")

    shortlist: list[tuple[str, dict]] = []
    for comp in args.competitions:
        clubs = tm.competition_clubs(comp, args.season).get("clubs", [])
        print(f"{comp} : {len(clubs)} clubs")
        for club in clubs:
            if club["id"] == ys_id:
                continue
            for row in tm.club_players(club["id"], args.season):
                age, mv = row.get("age"), row.get("market_value")
                if age is None or not (args.min_age <= age <= args.max_age):
                    continue
                if mv is not None and mv > args.max_value:
                    continue
                shortlist.append((comp, {**row, "current_club": club["name"]}))
    if args.limit:
        shortlist = shortlist[: args.limit]
    print(f"Vivier après filtres : {len(shortlist)} joueurs — récupération des fiches…")

    candidates = []
    for i, (comp, row) in enumerate(shortlist, 1):
        pid = row["id"]
        try:
            candidates.append(normalize(
                pid, comp, row,
                tm.player_profile(pid), tm.player_stats(pid), tm.player_market_value(pid),
                tm.player_transfers(pid), tm.player_injuries(pid) if args.injuries else None))
        except Exception as e:  # une fiche cassée ne doit pas arrêter le scan
            print(f"  ! {row.get('name')} ({pid}) ignoré : {e}")
        if i % 25 == 0:
            print(f"  {i}/{len(shortlist)}")

    if args.apifootball is not None:
        from .apifootball import ApiFootballClient, collect as af_collect, merge_into_candidates as af_merge
        af = ApiFootballClient()
        today = date.today()
        season = int(args.season) if args.season else (today.year if today.month >= 7 else today.year - 1)
        blocks = {}
        for league in (args.apifootball or [208, 207]):
            blocks[f"{league}/{season}"] = af_collect(af, league, season)
        (DATA / "apifootball.json").write_text(json.dumps(blocks, ensure_ascii=False, indent=1))
        print(f"API-Football : {af_merge(candidates, blocks)}/{len(candidates)} candidats enrichis")

    if args.sofascore is not None:
        from .sofascore import SofascoreClient, collect, merge_into_candidates
        sc = SofascoreClient()
        sofa = {}
        for t in (args.sofascore or [216, 215]):
            block = collect(sc, t, None, True)
            sofa[f"{t}/{block['season']}"] = block
        (DATA / "sofascore.json").write_text(json.dumps(sofa, ensure_ascii=False, indent=1))
        print(f"Sofascore : {merge_into_candidates(candidates, sofa)}/{len(candidates)} candidats enrichis")

    out = {"source": "transfermarkt-api", "base_url": tm.base_url, "genere": date.today().isoformat(),
           "competitions": args.competitions, "filtres": {"max_value": args.max_value,
           "age": [args.min_age, args.max_age]}, "demo": False, "players": candidates}
    (DATA / "candidates.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"→ data/candidates.json ({len(candidates)} joueurs)")


if __name__ == "__main__":
    main()
