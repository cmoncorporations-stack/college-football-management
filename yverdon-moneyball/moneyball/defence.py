"""Données défensives Transfermarkt : buts encaissés des gardiens et résultats des équipes.

    python -m moneyball.defence            # enrichit data/candidates.json et data/squad.json

Constat du 26.09.2026 : la page « Squad statistics » d'un club (`leistungsdaten/verein`)
ne contient pas les colonnes buts encaissés / clean sheets (le parseur `club_stats` les
lit si elles apparaissent). Deux pages les remplacent, une par compétition et saison :

- « Clean sheets », vue détaillée (`/-/weisseWeste/wettbewerb/{code}/saison_id/{saison}/plus/1`) :
  matchs, clean sheets, buts encaissés et minutes de chaque gardien ;
- le classement (`/-/tabelle/wettbewerb/{code}/saison_id/{saison}`) : rang, matchs et buts
  encaissés de chaque équipe, d'où les buts encaissés/90 de l'équipe et la médiane de la ligue.

Chaque ligne de statistiques d'un joueur (`stats[]`) reçoit :
  conceded, clean_sheets            gardiens seulement (None sinon)
  team_conceded, team_matches       équipe du joueur cette saison-là (via club_id)
  team_rank, team_count             rang au classement et nombre d'équipes
  league_conceded_90_median         médiane des buts encaissés/90 des équipes de la ligue
Les autres champs des joueurs sont conservés tels quels.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .model import league_coef, position_group
from .tm_direct import TransfermarktDirect

DATA = Path(__file__).resolve().parent.parent / "data"


def _season_id(label: str) -> int:
    """'25/26' → 2025."""
    head = str(label).split("/")[0]
    n = int(head)
    return n + 2000 if n < 100 else n


def _norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def pairs_needed(players: list[dict]) -> list[tuple[str, int]]:
    """Couples (compétition, saison) des lignes de championnat avec minutes."""
    out = set()
    for p in players:
        for r in p.get("stats", []):
            if r.get("minutes") and r.get("competition_id") and league_coef(r["competition_id"]) is not None:
                out.add((r["competition_id"], _season_id(r["season"])))
    return sorted(out)


def collect(direct: TransfermarktDirect, pairs: list[tuple[str, int]], gk_pairs: set[tuple[str, int]] | None = None,
            workers: int = 3) -> dict:
    """Lit classement (toutes les paires) et clean sheets (paires où un gardien du vivier a joué)."""
    gk_pairs = set(pairs) if gk_pairs is None else gk_pairs
    pool = ThreadPoolExecutor(max_workers=max(1, workers))

    def table(pair):
        try:
            return direct.league_table(pair[0], pair[1])
        except Exception as e:
            print(f"  ! classement {pair[0]} {pair[1]} : {e}")
            return []

    def sheets(pair):
        if pair not in gk_pairs:
            return []
        try:
            return direct.clean_sheets(pair[0], pair[1])
        except Exception as e:
            print(f"  ! clean sheets {pair[0]} {pair[1]} : {e}")
            return []

    tables = dict(zip(pairs, pool.map(table, pairs)))
    cs = dict(zip(pairs, pool.map(sheets, pairs)))
    pool.shutdown()
    return {f"{c}/{s}": {"competition_id": c, "season_id": s, "table": tables[(c, s)], "goalkeepers": cs[(c, s)]}
            for c, s in pairs}


def inject(players: list[dict], defence: dict) -> dict:
    """Ajoute les colonnes défensives aux lignes stats ; retourne un décompte."""
    n_gk, n_gk_rows, n_team = 0, 0, 0
    for p in players:
        is_gk = position_group(p.get("position")) == "GK"
        got = False
        for r in p.get("stats", []):
            r.setdefault("conceded", None)
            r.setdefault("clean_sheets", None)
            if not r.get("competition_id") or not r.get("minutes"):
                continue
            block = defence.get(f"{r['competition_id']}/{_season_id(r['season'])}")
            if not block:
                continue
            table = block["table"]
            if table:
                rates = sorted(t["goals_against"] / t["matches"] * 1 for t in table if t["matches"])
                median = rates[len(rates) // 2] if rates else None
                team = next((t for t in table if t["club_id"] and t["club_id"] == r.get("club_id")), None)
                if team is None and r.get("club"):
                    team = next((t for t in table if _norm(t["club"]) == _norm(r.get("club"))), None)
                if team and team["matches"]:
                    r["team_conceded"], r["team_matches"] = team["goals_against"], team["matches"]
                    r["team_rank"], r["team_count"] = team["rank"], team["teams"]
                    r["league_conceded_90_median"] = round(median, 3) if median is not None else None
                    n_team += 1
            if is_gk:
                mine = [g for g in block["goalkeepers"] if g["id"] == p["id"]]
                if mine:
                    r["conceded"] = sum(g["conceded"] for g in mine)
                    r["clean_sheets"] = sum(g["clean_sheets"] for g in mine)
                    r["gk_matches"] = sum(g["matches"] for g in mine)
                    n_gk_rows += 1
                    got = True
        n_gk += got
    return {"gardiens_avec_buts_encaisses": n_gk, "lignes_gardien": n_gk_rows, "lignes_avec_equipe": n_team}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args(argv)
    cand = json.loads((DATA / "candidates.json").read_text())
    squad = json.loads((DATA / "squad.json").read_text())
    players = cand["players"] + squad["players"]
    pairs = pairs_needed(players)
    gk_pairs = set(pairs_needed([p for p in players if position_group(p.get("position")) == "GK"]))
    print(f"Défense : {len(pairs)} couples compétition/saison, dont {len(gk_pairs)} avec des gardiens du vivier")
    direct = TransfermarktDirect()
    defence = collect(direct, pairs, gk_pairs, args.workers)
    (DATA / "defence.json").write_text(json.dumps(defence, ensure_ascii=False, indent=1))
    print("Vivier :", inject(cand["players"], defence))
    print("Effectif YS :", inject(squad["players"], defence))
    (DATA / "candidates.json").write_text(json.dumps(cand, ensure_ascii=False, indent=1))
    (DATA / "squad.json").write_text(json.dumps(squad, ensure_ascii=False, indent=1))
    print(f"Requêtes Transfermarkt : {direct.calls} (dont {direct.captchas} captchas réessayés)")


if __name__ == "__main__":
    main()
