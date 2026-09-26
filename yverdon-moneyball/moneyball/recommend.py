"""Calcule les recommandations et génère le tableau de bord.

    python -m moneyball.recommend            # données Transfermarkt (data/candidates.json)
    python -m moneyball.recommend --demo     # vivier fictif de démonstration

Sorties : data/recommendations.json et dashboard.html (autonome, à ouvrir dans un navigateur).
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from . import demo
from .fan_dna import derive_fan_dna, load_cockpit
from .model import DEFAULT_WEIGHTS, score_pool, squad_needs

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TEMPLATE = ROOT / "dashboard_template.html"


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
    return {
        "genere": today.isoformat(),
        "demo": bool(cand.get("demo")),
        "source": cand.get("source"),
        "competitions": cand.get("competitions"),
        "saison": cand.get("season"),
        "vivier": len(cand["players"]),
        "poids_defaut": DEFAULT_WEIGHTS,
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

    print(f"Vivier {result['vivier']} joueurs · {'DÉMO fictive' if result['demo'] else 'Transfermarkt'}")
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
