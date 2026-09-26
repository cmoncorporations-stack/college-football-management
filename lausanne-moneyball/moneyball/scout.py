"""Constitue le vivier de joueurs depuis Transfermarkt.

    python -m moneyball.scout --competitions C2 C1 FR3 FR2 --max-value 1500000 --max-age 29

Sans option, le périmètre vient de club.json (scout.competitions, max_value, max_age).

Étapes :
1. Trouve le club (club.json : transfermarkt_club_id, sinon recherche par search_name,
   ou --club-id) et récupère l'effectif actuel.
2. Pour chaque championnat : clubs → effectifs → premier filtre (âge, valeur marchande).
3. Statistiques : page « Squad statistics » de chaque club (championnat, saison en
   cours et saison précédente), plus celle de l'ancien club des joueurs arrivés cet été.
4. Pour chaque joueur retenu : profil, historique de valeur, transferts, blessures (optionnel).
   Les joueurs du club lui-même (prêtés ailleurs, ou déjà engagés pour plus tard) sont écartés.
5. Écrit data/squad.json et data/candidates.json au format normalisé attendu par model.py.

Source : www.transfermarkt.com en direct (`tm_direct.py`, aucune dépendance) ; si
TM_API_URL est défini, une instance de transfermarkt-api est utilisée pour les
profils, valeurs et transferts (les statistiques restent lues en direct : les pages
statistiques joueur de Transfermarkt sont désormais rendues côté client).

Le cache disque (data/cache) évite de rappeler le site pour une même page pendant 72 h.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from .club import CLUB, is_club
from .model import league_coef
from .tm_client import TransfermarktClient
from .tm_direct import TransfermarktDirect, current_season

DATA = Path(__file__).resolve().parent.parent / "data"
CUP_WORDS = ("cup", "coupe", "pokal", "coppa", "copa", "trophy", "playoff", "play-off", "relegation",
             "promotion", "supercup", "champions", "europa", "conference", "qualif")


def season_label(season: int | str) -> str:
    s = int(season)
    return f"{s % 100:02d}/{(s + 1) % 100:02d}"


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
        "url": profile.get("url") or f"https://www.transfermarkt.com/{row.get('slug') or '-'}/profil/spieler/{pid}",
        "position": (profile.get("position") or {}).get("main") or row.get("position"),
        "age": profile.get("age") or row.get("age"),
        "date_of_birth": profile.get("date_of_birth") or row.get("date_of_birth"),
        "citizenship": profile.get("citizenship") or row.get("nationality") or [],
        "birth_city": pob.get("city"),
        "birth_country": pob.get("country"),
        "foot": profile.get("foot") or row.get("foot"),
        "height": profile.get("height") or row.get("height"),
        "market_value": profile.get("market_value") or row.get("market_value"),
        "mv_history": _mv_history(mv),
        "contract_expires": club.get("contract_expires") or row.get("contract"),
        "club": club.get("name") or row.get("current_club"),
        "league_id": league_id,
        "stats": [{
            "season": s.get("season_id"), "competition_id": s.get("competition_id"),
            "competition": s.get("competition_name"), "appearances": s.get("appearances") or 0,
            "goals": s.get("goals") or 0, "assists": s.get("assists") or 0,
            "minutes": s.get("minutes_played") or 0, "yellow": s.get("yellow_cards") or 0,
            "red": s.get("red_cards") or 0,
        } for s in stats],
        "transfers": [{"date": t.get("date"), "from": (t.get("club_from") or {}).get("name"),
                       "to": (t.get("club_to") or {}).get("name"), "fee": t.get("fee"),
                       "fee_label": t.get("fee_label"), "upcoming": bool(t.get("upcoming"))}
                      for t in transfers.get("transfers", [])],
        "youth_clubs": transfers.get("youth_clubs") or profile.get("youth_clubs") or [],
        "social_media": profile.get("socialMedia") or [],
        "injury_days": injury_days,
    }


def find_club(tm, club_id: str | None) -> str:
    club_id = club_id or CLUB.get("transfermarkt_club_id")
    if club_id:
        return str(club_id)
    results = [r for r in tm.search_club(CLUB["search_name"]) if r.get("country") == CLUB["search_country"]]
    if not results:
        sys.exit(f"{CLUB['name']} introuvable via la recherche de clubs — passer --club-id.")
    best = max(results, key=lambda r: r.get("market_value") or 0)
    print(f"{CLUB['name']} → id {best['id']} ({best['name']})")
    return best["id"]


def owned_by_club(p: dict) -> bool:
    """Joueur qui appartient déjà au club : prêté ailleurs par lui, ou transfert vers lui annoncé."""
    trs = sorted((t for t in p.get("transfers", []) if t.get("date")), key=lambda t: t["date"])
    if any(t.get("upcoming") and is_club(t.get("to")) for t in trs):
        return True
    past = [t for t in trs if not t.get("upcoming")]
    if not past:
        return False
    label = (past[-1].get("fee_label") or "").lower()
    return is_club(past[-1].get("from")) and "loan" in label and "end of loan" not in label


def league_option(options: list[dict], season: int | str) -> tuple[str | None, str | None]:
    """Parmi les couples (compétition, saison) d'un club, le championnat d'une saison donnée."""
    rows = [o for o in options if o.get("code") and o.get("season") == str(season)]
    known = [o for o in rows if league_coef(o["code"]) is not None]
    if known:
        return known[0]["code"], known[0]["label"]
    for o in rows:
        if not any(w in o["label"].lower() for w in CUP_WORDS):
            return o["code"], o["label"]
    return None, None


def stat_rows(block: dict, season: int | str) -> dict[str, dict]:
    label = season_label(season)
    return {r["id"]: {"id": r["id"], "season_id": label, "competition_id": block["competition_id"],
                      "competition_name": block.get("competition_name"), "club_id": block["club_id"],
                      **{k: r[k] for k in ("appearances", "goals", "assists", "yellow_cards", "red_cards", "minutes_played")}}
            for r in block["rows"]}


def club_season_stats(direct: TransfermarktDirect, club_id: str, comp: str | None, season: int,
                      prev: int, with_prev: bool = True) -> list[dict]:
    """Lignes de statistiques (une par joueur et saison) d'un club : championnat saison en cours,
    puis championnat de la saison précédente (retrouvé dans les options de la page)."""
    out = []
    if comp is not None:
        cur = direct.club_stats(club_id, comp, season)
    else:  # club hors périmètre : on lit d'abord le « Total » pour connaître ses compétitions
        cur = direct.club_stats(club_id, None, prev)
        code, _ = league_option(cur["options"], prev)
        if code:
            cur = direct.club_stats(club_id, code, prev)
        return list(stat_rows(cur, prev).values())
    out.extend(stat_rows(cur, season).values())
    if with_prev:
        code, _ = league_option(cur["options"], prev)
        if code:
            out.extend(stat_rows(direct.club_stats(club_id, code, prev), prev).values())
    return out


def merge_stats(rows: list[dict]) -> list[dict]:
    """Additionne les lignes d'une même saison et compétition (joueur passé par deux clubs)."""
    merged: dict[tuple, dict] = {}
    for r in rows:
        key = (r["season_id"], r["competition_id"])
        if key in merged:
            for k in ("appearances", "goals", "assists", "yellow_cards", "red_cards", "minutes_played"):
                merged[key][k] += r[k]
        else:
            merged[key] = dict(r)
    return sorted(merged.values(), key=lambda r: r["season_id"], reverse=True)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    scope = CLUB["scout"]
    ap.add_argument("--competitions", nargs="+", default=scope.get("competitions", ["C2", "C1", "FR3", "FR2"]),
                    help="Codes compétition Transfermarkt à scanner (défaut : club.json)")
    ap.add_argument("--season", default=None, help="Saison Transfermarkt (ex. 2026) ; défaut : en cours")
    ap.add_argument("--club-id", default=None, help=f"Id Transfermarkt de {CLUB['name']} (sinon club.json ou recherche)")
    ap.add_argument("--max-value", type=int, default=scope.get("max_value", 1_500_000), help="Valeur marchande max (EUR)")
    ap.add_argument("--max-age", type=int, default=scope.get("max_age", 29))
    ap.add_argument("--min-age", type=int, default=scope.get("min_age", 17))
    ap.add_argument("--injuries", action="store_true", help="Récupère aussi l'historique de blessures (+1 appel/joueur, transfermarkt-api seulement)")
    ap.add_argument("--limit", type=int, default=0, help="Limite de joueurs détaillés (0 = tous), pour tester")
    ap.add_argument("--workers", type=int, default=3, help="Requêtes en parallèle (la cadence globale reste limitée)")
    ap.add_argument("--apifootball", nargs="*", type=int, metavar="LIGUE",
                    help="Enrichit avec API-Football (note, passes clés, tirs…) ; ids de ligue, défaut 208 207. Clé : API_FOOTBALL_KEY")
    ap.add_argument("--sofascore", nargs="*", type=int, metavar="TOURNOI",
                    help="Enrichit avec Sofascore (xG, xA, note) ; ids de tournoi, défaut 216 215")
    args = ap.parse_args(argv)

    direct = TransfermarktDirect()
    tm = TransfermarktClient() if os.environ.get("TM_API_URL") else direct
    season = int(args.season) if args.season else current_season()
    prev = season - 1
    pool = ThreadPoolExecutor(max_workers=max(1, args.workers))

    try:
        home_id = find_club(tm, args.club_id)
        squad = tm.club_players(home_id, season)
    except RuntimeError as e:
        sys.exit(f"Transfermarkt inaccessible : {e}\nAucun vivier écrit (pas de repli sur la démo).")
    if not squad:
        sys.exit(f"Effectif {CLUB['short']} vide : page Transfermarkt illisible, aucun vivier écrit.")
    (DATA / "squad.json").write_text(json.dumps({"club_id": home_id, "season": season_label(season),
                                                 "players": squad}, ensure_ascii=False, indent=1))
    print(f"Effectif {CLUB['short']} : {len(squad)} joueurs (saison {season_label(season)})")

    # 1. Clubs et effectifs -------------------------------------------------
    clubs: list[tuple[str, dict]] = []
    for comp in args.competitions:
        block = tm.competition_clubs(comp, season)
        if not block.get("clubs"):
            print(f"{comp} : aucun club trouvé — code de compétition à vérifier")
        print(f"{comp} ({block.get('name')}) : {len(block.get('clubs', []))} clubs")
        clubs.extend((comp, c) for c in block.get("clubs", []) if c["id"] != home_id)
    scanned_ids = {c["id"] for _, c in clubs}   # le club lui-même compte comme « ancien club » pour ses partants

    squads = list(pool.map(lambda cc: tm.club_players(cc[1]["id"], season), clubs))
    shortlist: list[tuple[str, dict]] = []
    for (comp, club), rows in zip(clubs, squads):
        for row in rows:
            age, mv = row.get("age"), row.get("market_value")
            if age is None or not (args.min_age <= age <= args.max_age):
                continue
            if mv is not None and mv > args.max_value:
                continue
            shortlist.append((comp, {**row, "current_club": row.get("current_club") or club["name"], "club_id": club["id"]}))
    if args.limit:
        shortlist = shortlist[: args.limit]
    print(f"Vivier après filtres : {len(shortlist)} joueurs")

    # 2. Statistiques par club ----------------------------------------------
    wanted = {row["id"] for _, row in shortlist}
    stats_by_player: dict[str, list[dict]] = {}

    def add_rows(rows):
        for r in rows:
            if r["id"] in wanted:
                stats_by_player.setdefault(r["id"], []).append(r)

    club_jobs = [(club["id"], comp) for comp, club in clubs if any(r["club_id"] == club["id"] for _, r in shortlist)]
    print(f"Statistiques : {len(club_jobs)} clubs × 2 saisons…")
    for rows in pool.map(lambda j: club_season_stats(direct, j[0], j[1], season, prev), club_jobs):
        add_rows(rows)
    # Joueurs arrivés depuis le début de la saison précédente : statistiques de l'ancien club.
    outside = sorted({row["signed_from_id"] for _, row in shortlist
                      if row.get("signed_from_id") and row["signed_from_id"] not in scanned_ids
                      and (row.get("joined_on") or "") >= f"{prev}-07-01"})
    print(f"Statistiques : {len(outside)} anciens clubs hors périmètre (saison {season_label(prev)})…")

    def outside_rows(cid):
        try:
            return club_season_stats(direct, cid, None, season, prev)
        except Exception as e:  # un club exotique ne doit pas arrêter le scan
            print(f"  ! club {cid} ignoré : {e}")
            return []

    for rows in pool.map(outside_rows, outside):
        add_rows(rows)
    with_stats = sum(1 for pid in wanted if any(r["minutes_played"] for r in stats_by_player.get(pid, [])))
    print(f"  {with_stats}/{len(wanted)} joueurs avec des minutes jouées")

    # 3. Fiches joueur --------------------------------------------------------
    print("Fiches joueur (profil, valeur, transferts)…")
    done = {"n": 0}

    def build(item):
        comp, row = item
        pid = row["id"]
        profile, mv, transfers, injuries = {}, {}, {}, None
        try:
            profile = (tm.player_profile(pid, row.get("slug")) if isinstance(tm, TransfermarktDirect)
                       else tm.player_profile(pid))
        except Exception as e:
            print(f"  ! profil {row.get('name')} ({pid}) : {e}")
        try:
            mv = tm.player_market_value(pid)
        except Exception as e:
            print(f"  ! valeur {row.get('name')} ({pid}) : {e}")
        try:
            transfers = tm.player_transfers(pid)
        except Exception as e:
            print(f"  ! transferts {row.get('name')} ({pid}) : {e}")
        if args.injuries and hasattr(tm, "player_injuries") and not isinstance(tm, TransfermarktDirect):
            try:
                injuries = tm.player_injuries(pid)
            except Exception as e:
                print(f"  ! blessures {row.get('name')} ({pid}) : {e}")
        done["n"] += 1
        if done["n"] % 50 == 0:
            print(f"  {done['n']}/{len(shortlist)}")
        return normalize(pid, comp, row, profile, merge_stats(stats_by_player.get(pid, [])), mv, transfers, injuries)

    candidates = list(pool.map(build, shortlist))
    pool.shutdown()
    owned = [p for p in candidates if owned_by_club(p)]
    if owned:
        print(f"Écartés, déjà au club : {', '.join(p['name'] for p in owned)}")
        candidates = [p for p in candidates if not owned_by_club(p)]
    print(f"Requêtes Transfermarkt : {direct.calls} (dont {direct.captchas} captchas réessayés)")

    if args.apifootball is not None:
        from .apifootball import ApiFootballClient, collect as af_collect, merge_into_candidates as af_merge
        af = ApiFootballClient()
        blocks = {}
        for league in (args.apifootball or [208, 207]):
            blocks[f"{league}/{season}"] = af_collect(af, league, season)
        (DATA / "apifootball.json").write_text(json.dumps(blocks, ensure_ascii=False, indent=1))
        print(f"API-Football : {af_merge(candidates, blocks)}/{len(candidates)} candidats enrichis")

    if args.sofascore is not None:
        from .sofascore import SofascoreClient, collect, merge_into_candidates
        sc = SofascoreClient()
        sofa = {}
        try:
            for t in (args.sofascore or [216, 215]):
                block = collect(sc, t, None, True)
                sofa[f"{t}/{block['season']}"] = block
            (DATA / "sofascore.json").write_text(json.dumps(sofa, ensure_ascii=False, indent=1))
            print(f"Sofascore : {merge_into_candidates(candidates, sofa)}/{len(candidates)} candidats enrichis")
        except Exception as e:
            print(f"! Sofascore indisponible ({e}) — vivier écrit sans xG/xA ; relancer "
                  f"`python -m moneyball.sofascore --merge` plus tard.")

    out = {"source": "transfermarkt-api" if tm is not direct else "www.transfermarkt.com (lecture directe)",
           "base_url": tm.base_url, "genere": date.today().isoformat(), "season": season_label(season),
           "competitions": args.competitions, "filtres": {"max_value": args.max_value,
           "age": [args.min_age, args.max_age]}, "demo": False, "players": candidates}
    (DATA / "candidates.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"→ data/candidates.json ({len(candidates)} joueurs)")


if __name__ == "__main__":
    main()
