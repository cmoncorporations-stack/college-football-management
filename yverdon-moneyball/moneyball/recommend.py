"""Calcule les recommandations et génère le tableau de bord.

    python -m moneyball.recommend            # données Transfermarkt (data/candidates.json)
    python -m moneyball.recommend --demo     # vivier fictif de démonstration

Sorties : data/recommendations.json et dashboard.html (autonome, à ouvrir dans un navigateur).
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
from datetime import date
from pathlib import Path

from . import demo, geo
from .fan_dna import derive_fan_dna, load_cockpit
from .model import (CUP_WEIGHT, DEFAULT_WEIGHTS, DYN_SHRINK_MATCHES, DYN_WEIGHT, EURO_DEFAULT_COEF, LEAGUE_COEF,
                    LEAGUE_PREFIX_COEF, SEASON_RAMP_MATCHES, current_season_key, dynamic_tags, position_group,
                    recent_stats, score_pool, season_dynamics, squad_needs, standings_from_defence)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TEMPLATE = ROOT / "dashboard_template.html"
MATCHLOG = DATA / "matchlog_2627.json"
DEFENCE = DATA / "defence.json"


def model_version() -> str:
    """Numéro de commit court du dépôt (version du modèle), « local » hors dépôt git."""
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or "local"
    except (OSError, subprocess.SubprocessError):
        return "local"


def sources_summary(players: list[dict]) -> dict:
    """Répartition réelle des sources : joueurs avec statistiques Transfermarkt, gardiens avec
    buts encaissés, joueurs avec un bloc Sofascore / API-Football."""
    n = len(players)
    with_tm = sum(1 for p in players if any(r.get("minutes") for r in p.get("stats", [])))
    gks = [p for p in players if position_group(p.get("position")) == "GK"]
    gk_conceded = sum(1 for p in gks if recent_stats(p)["conceded"] is not None)
    with_team = sum(1 for p in players if recent_stats(p)["team_c90"])
    sofa = sum(1 for p in players if (p.get("sofascore") or p.get("perf") or {}).get("xg") is not None)
    af = sum(1 for p in players if (p.get("perf") or {}).get("key_passes") is not None)
    return {"vivier": n, "transfermarkt_stats": with_tm, "transfermarkt_equipe": with_team,
            "gardiens": len(gks), "gardiens_buts_encaisses": gk_conceded, "sofascore": sofa, "api_football": af}


def sport_by_line(ranked: list[dict]) -> dict:
    out = {}
    for g in ("GK", "DEF", "MID", "ATT"):
        v = [j["scores"]["sport"] for j in ranked if j["groupe"] == g]
        out[g] = {"n": len(v), "moyenne": round(statistics.mean(v), 1) if v else None,
                  "ecart_type": round(statistics.pstdev(v), 1) if len(v) > 1 else None}
    return out


def load_matchlog() -> dict | None:
    return json.loads(MATCHLOG.read_text()) if MATCHLOG.exists() else None


def journal_summary(matchlog: dict | None, players: list[dict], squad: list[dict]) -> dict | None:
    """Couverture du journal 2026/27 : joueurs avec journal, journées couvertes par championnat."""
    if not matchlog:
        return None
    logs = matchlog.get("players") or {}
    with_log = sum(1 for p in players if (logs.get(p["id"]) or {}).get("entries"))
    squad_log = sum(1 for p in squad if (logs.get(p["id"]) or {}).get("entries"))
    comps = {}
    for code, c in (matchlog.get("competitions") or {}).items():
        comps[code] = {"nom": c.get("name"), "type": c.get("type"), "matchs": c.get("matches"),
                       "journees": c.get("max_matchday")}
    cov = matchlog.get("coverage") or {}
    return {"date": matchlog.get("today"), "joueurs_avec_journal": with_log, "vivier": len(players),
            "effectif_avec_journal": squad_log, "effectif": len(squad), "competitions": comps,
            "feuilles_lues": cov.get("matches_read"), "feuilles_listees": cov.get("matches_listed"),
            "pages_blessures": cov.get("injury_pages"), "source": matchlog.get("source")}


def squad_season(squad: list[dict], matchlog: dict | None, today: date, standings: dict | None) -> list[dict]:
    """L'effectif cette saison : dynamique de chaque joueur d'Yverdon, trié par minutes."""
    out = []
    for p in squad:
        pp = {**p, "id": p["id"], "position": p.get("position")}
        dyn = season_dynamics(pp, matchlog, today, standings) if matchlog else None
        out.append({"id": p["id"], "name": p.get("name"), "position": p.get("position"),
                    "groupe": position_group(p.get("position")), "age": p.get("age"), "contract": p.get("contract"),
                    "market_value": p.get("market_value"), "dynamique": dyn, "tags": dynamic_tags(dyn) if matchlog else []})
    out.sort(key=lambda r: (-(r["dynamique"] or {}).get("minutes", -1), r["name"] or ""))
    return out


def _reason(before: dict, after: dict) -> str:
    """Pourquoi un joueur monte ou descend : forme, titularisation, blessure, minutes ou production."""
    dyn = after["detail"]["sport"].get("dynamique") or {}
    delta = after["scores"]["sport"] - before["scores"]["sport"]
    up = delta >= 0
    if (dyn.get("statut_actuel") or {}).get("status") == "injured" and not up:
        return "blessure"
    if dyn.get("availability_2627", 0) >= 0.3 and not up:
        return "blessure / suspension"
    ss = dyn.get("starter_share")
    if ss is not None and ((up and ss >= 0.8) or (not up and ss <= 0.3)):
        return "titularisation" if up else "peu titularisé"
    f5 = dyn.get("form_5")
    if f5 is not None and ((up and f5 >= 1.3) or (not up and f5 <= 0.7)):
        return "forme"
    rt = dyn.get("role_trend")
    if rt is not None and ((up and rt >= 0.3) or (not up and rt <= -0.3)):
        return "gagne sa place" if up else "perd sa place"
    ms = dyn.get("minutes_share")
    if ms is not None and ((up and ms >= 0.7) or (not up and ms <= 0.3)):
        return "temps de jeu"
    if "Saison 26/27 non lue" in after.get("tags", []):
        return "sans journal"
    return "production 26/27" if up else "production 26/27 en retrait"


def compare(before: list[dict], after: list[dict], n: int = 10) -> dict:
    """Top 20 avant/après, dix plus fortes montées et descentes de classement avec la raison."""
    rank_b = {j["id"]: i + 1 for i, j in enumerate(before)}
    rank_a = {j["id"]: i + 1 for i, j in enumerate(after)}
    by_b = {j["id"]: j for j in before}
    moves = []
    for j in after:
        b = by_b.get(j["id"])
        if not b:
            continue
        moves.append({"id": j["id"], "name": j["name"], "club": j["club"], "groupe": j["groupe"],
                      "rang_avant": rank_b[j["id"]], "rang_apres": rank_a[j["id"]],
                      "delta": rank_b[j["id"]] - rank_a[j["id"]],
                      "sport_avant": b["scores"]["sport"], "sport_apres": j["scores"]["sport"],
                      "final_avant": b["scores"]["final"], "final_apres": j["scores"]["final"],
                      "raison": _reason(b, j)})
    moves = [m for m in moves if m["delta"]]
    moves.sort(key=lambda m: -m["delta"])
    top = lambda lst: [{"id": j["id"], "name": j["name"], "club": j["club"], "groupe": j["groupe"],  # noqa: E731
                        "final": j["scores"]["final"], "sport": j["scores"]["sport"]} for j in lst[:20]]
    return {"top20_avant": top(before), "top20_apres": top(after),
            "top20_par_ligne_avant": {g: sum(1 for j in before[:20] if j["groupe"] == g) for g in ("GK", "DEF", "MID", "ATT")},
            "top20_par_ligne_apres": {g: sum(1 for j in after[:20] if j["groupe"] == g) for g in ("GK", "DEF", "MID", "ATT")},
            "sport_par_ligne_avant": sport_by_line(before), "sport_par_ligne_apres": sport_by_line(after),
            "montees": moves[:n], "descentes": sorted(moves, key=lambda m: m["delta"])[:n]}


def run(use_demo: bool, today: date, top: int = 60, with_matchlog: bool = True) -> dict:
    if use_demo:
        cand, squad = demo.build(today=today)
        matchlog = None
    else:
        cand = json.loads((DATA / "candidates.json").read_text())
        squad = json.loads((DATA / "squad.json").read_text())
        matchlog = load_matchlog() if with_matchlog else None
    defence = json.loads(DEFENCE.read_text()) if DEFENCE.exists() and not use_demo else None
    standings = standings_from_defence(defence, current_season_key(today)) if defence else None
    cockpit = load_cockpit()
    dna = derive_fan_dna(cockpit)
    needs = squad_needs(squad["players"], today, matchlog)
    ranked = score_pool(cand["players"], dna["weights"], needs, today, matchlog=matchlog, standings=standings)
    comparison = None
    if matchlog:
        needs_before = squad_needs(squad["players"], today)
        before = score_pool(cand["players"], dna["weights"], needs_before, today)
        comparison = compare(before, ranked)
    estimated = [j for j in ranked if j.get("market_value_estimated")]
    return {
        "genere": today.isoformat(),
        "date_donnees": (matchlog or {}).get("today") or cand.get("genere"),
        "date_vivier": cand.get("genere"),
        "version_modele": model_version(),
        "demo": bool(cand.get("demo")),
        "source": cand.get("source"),
        "sources": sources_summary(cand["players"]),
        "competitions": cand.get("competitions"),
        "saison": cand.get("season"),
        "vivier": len(cand["players"]),
        "valeurs_estimees": len(estimated),
        "poids_defaut": DEFAULT_WEIGHTS,
        "coefficients_ligue": {"exacts": LEAGUE_COEF, "prefixes": dict(LEAGUE_PREFIX_COEF)},
        "dynamique": {"poids": DYN_WEIGHT, "retrecissement_matchs": DYN_SHRINK_MATCHES, "poids_coupe": CUP_WEIGHT,
                      "niveau_europe": EURO_DEFAULT_COEF, "rampe_saison_matchs": SEASON_RAMP_MATCHES},
        "journal": journal_summary(matchlog, cand["players"], squad["players"]),
        "effectif_saison": squad_season(squad["players"], matchlog, today, standings),
        "comparaison": comparison,
        "sport_par_ligne": sport_by_line(ranked),
        "top20": {g: sum(1 for j in ranked[:20] if j["groupe"] == g) for g in ("GK", "DEF", "MID", "ATT")},
        "lieux_non_reconnus": geo.unrecognized(cand["players"]),
        "fan_dna": dna,
        "besoins": needs,
        "joueurs": ranked[:top] if top else ranked,
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="Utilise le vivier fictif de démonstration")
    ap.add_argument("--top", type=int, default=0, help="Nombre de joueurs conservés (0 = tous)")
    ap.add_argument("--today", default=None, help="Date de calcul AAAA-MM-JJ (défaut : aujourd'hui)")
    ap.add_argument("--no-matchlog", action="store_true", help="Ignore data/matchlog_2627.json (modèle sans dynamique)")
    args = ap.parse_args(argv)

    today = date.fromisoformat(args.today) if args.today else date.today()
    result = run(args.demo, today, args.top, not args.no_matchlog)
    (DATA / "recommendations.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    html = TEMPLATE.read_text().replace("/*__DATA__*/null", json.dumps(result, ensure_ascii=False))
    (ROOT / "dashboard.html").write_text(html)

    print(f"Vivier {result['vivier']} joueurs · {'DÉMO fictive' if result['demo'] else 'Transfermarkt'} · "
          f"modèle {result['version_modele']} · données du {result['date_donnees']}")
    print("Sources :", result["sources"])
    print("Sport par ligne :", result["sport_par_ligne"])
    print("Top 20 par ligne :", result["top20"], "· valeurs estimées :", result["valeurs_estimees"])
    print("Besoins :", {g: n["besoin"] for g, n in result["besoins"].items()})
    if result.get("journal"):
        jn = result["journal"]
        print(f"Journal 26/27 du {jn['date']} : {jn['joueurs_avec_journal']}/{jn['vivier']} joueurs du vivier, "
              f"{jn['effectif_avec_journal']}/{jn['effectif']} joueurs YS ; journées :",
              {k: v["journees"] for k, v in jn["competitions"].items() if v["type"] == "league"})
    if result.get("comparaison"):
        cp = result["comparaison"]
        print("Top 20 par ligne avant → après :", cp["top20_par_ligne_avant"], "→", cp["top20_par_ligne_apres"])
        print("Sport par ligne avant :", {g: v["moyenne"] for g, v in cp["sport_par_ligne_avant"].items()})
        for key, label in (("montees", "Montées"), ("descentes", "Descentes")):
            print(f"\n{label} :")
            for m in cp[key]:
                print(f"  {m['rang_avant']:>4} → {m['rang_apres']:<4} {m['name']:<24} {m['club']:<24} "
                      f"S {m['sport_avant']:5.1f} → {m['sport_apres']:5.1f}  {m['raison']}")
    print("Poids fan fit :", result["fan_dna"]["weights"])
    for g in ("GK", "DEF", "MID", "ATT"):
        best = [j for j in result["joueurs"] if j["groupe"] == g][:3]
        print(f"\n{g}")
        for j in best:
            s = j["scores"]
            print(f"  {s['final']:5.1f}  {j['name']:<22} {j['age']:>2} ans  {j['club']:<24} "
                  f"S{s['sport']:>5.1f} V{s['valeur']:>5.1f} F{s['fan']:>5.1f}  {', '.join(j['tags'])}")
    print("\n→ data/recommendations.json, dashboard.html")


if __name__ == "__main__":
    main()
