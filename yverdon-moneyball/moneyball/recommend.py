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
from .model import (DEFAULT_WEIGHTS, LEAGUE_COEF, LEAGUE_PREFIX_COEF, position_group, recent_stats,
                    score_pool, squad_needs)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TEMPLATE = ROOT / "dashboard_template.html"


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


def run(use_demo: bool, today: date, top: int = 60) -> dict:
    if use_demo:
        cand, squad = demo.build(today=today)
    else:
        cand = json.loads((DATA / "candidates.json").read_text())
        squad = json.loads((DATA / "squad.json").read_text())
    cockpit = load_cockpit()
    dna = derive_fan_dna(cockpit)
    needs = squad_needs(squad["players"], today)
    ranked = score_pool(cand["players"], dna["weights"], needs, today)
    estimated = [j for j in ranked if j.get("market_value_estimated")]
    return {
        "genere": today.isoformat(),
        "date_donnees": cand.get("genere"),
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
    args = ap.parse_args(argv)

    result = run(args.demo, date.today(), args.top)
    (DATA / "recommendations.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    html = TEMPLATE.read_text().replace("/*__DATA__*/null", json.dumps(result, ensure_ascii=False))
    (ROOT / "dashboard.html").write_text(html)

    print(f"Vivier {result['vivier']} joueurs · {'DÉMO fictive' if result['demo'] else 'Transfermarkt'} · "
          f"modèle {result['version_modele']} · données du {result['date_donnees']}")
    print("Sources :", result["sources"])
    print("Sport par ligne :", result["sport_par_ligne"])
    print("Top 20 par ligne :", result["top20"], "· valeurs estimées :", result["valeurs_estimees"])
    print("Besoins :", {g: n["besoin"] for g, n in result["besoins"].items()})
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
