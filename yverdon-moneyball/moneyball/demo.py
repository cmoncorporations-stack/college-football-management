"""Vivier de DÉMONSTRATION — joueurs et clubs 100 % fictifs.

Sert à faire tourner la chaîne complète sans accès à Transfermarkt. Les noms,
clubs, valeurs et statistiques sont générés aléatoirement (graine fixe) au
format normalisé de scout.py. À remplacer par `python -m moneyball.scout`.
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

FIRST = ["Luca", "Noah", "Théo", "Mattéo", "Kylian", "Adrien", "Bastien", "Yanis", "Loïc", "Nils",
         "Amadou", "Ibrahima", "Moussa", "Karim", "Samuel", "Julien", "Maxime", "Florian", "Rayan",
         "Elias", "Dario", "Marco", "Jonas", "Tim", "Kevin", "Quentin", "Hugo", "Arthur", "Enzo", "Malik"]
LAST = ["Perrin", "Chappuis", "Rochat", "Favre", "Guignard", "Jaquier", "Bovet", "Cuendet", "Pittet",
        "Diallo", "Traoré", "Koné", "Mendy", "Bamba", "Moreau", "Girard", "Lambert", "Morel", "Brunner",
        "Keller", "Meier", "Rossi", "Bianchi", "Dubois", "Leroy", "Fontaine", "Sow", "Camara", "Oberson", "Mottier"]
# (ligue, club fictif)
CLUBS = [("C2", "FC Démo Lac-Léman"), ("C2", "AC Démo Jura"), ("C2", "Démo Alpes FC"),
         ("C2", "SC Démo Rhin"), ("C1", "FC Démo Métropole"), ("C1", "Démo Sporting"),
         ("FR3", "US Démo Franche-Comté"), ("FR3", "AS Démo Savoie"), ("FR2", "Olympique Démo"),
         ("BE2", "RFC Démo Wallonie"), ("CHPL", "FC Démo Broye")]
POSITIONS = ["Goalkeeper", "Centre-Back", "Centre-Back", "Left-Back", "Right-Back",
             "Defensive Midfield", "Central Midfield", "Attacking Midfield",
             "Left Winger", "Right Winger", "Centre-Forward", "Centre-Forward"]
BIRTH = [("Yverdon-les-Bains", "Switzerland", ["Switzerland"]), ("Payerne", "Switzerland", ["Switzerland"]),
         ("Orbe", "Switzerland", ["Switzerland"]), ("Lausanne", "Switzerland", ["Switzerland"]),
         ("Morges", "Switzerland", ["Switzerland"]), ("Genève", "Switzerland", ["Switzerland"]),
         ("Fribourg", "Switzerland", ["Switzerland"]), ("Neuchâtel", "Switzerland", ["Switzerland"]),
         ("Zürich", "Switzerland", ["Switzerland"]), ("Bern", "Switzerland", ["Switzerland"]),
         ("Besançon", "France", ["France"]), ("Lyon", "France", ["France"]), ("Paris", "France", ["France", "Senegal"]),
         ("Dakar", "Senegal", ["Senegal"]), ("Abidjan", "Cote d'Ivoire", ["Cote d'Ivoire"]),
         ("Liège", "Belgium", ["Belgium"]), ("München", "Germany", ["Germany"]), ("Milano", "Italy", ["Italy"]),
         ("Porto", "Portugal", ["Portugal"]), ("Pontarlier", "France", ["France"])]
EXPECT = {"Goalkeeper": 0.0, "Centre-Back": 0.08, "Left-Back": 0.14, "Right-Back": 0.14,
          "Defensive Midfield": 0.15, "Central Midfield": 0.25, "Attacking Midfield": 0.40,
          "Left Winger": 0.45, "Right Winger": 0.45, "Centre-Forward": 0.55}
YS_FORMER = ["Yverdon-Sport FC"]


def _player(rng: random.Random, i: int, today: date) -> dict:
    league, club = rng.choice(CLUBS)
    pos = rng.choice(POSITIONS)
    age = rng.randint(19, 31)
    city, country, cits = rng.choices(BIRTH, weights=[3, 2, 2, 4, 3, 4, 4, 4, 5, 5, 4, 4, 4, 3, 3, 3, 3, 2, 2, 2])[0]
    talent = rng.betavariate(2.2, 2.2)
    lvl = {"C1": 1.45, "C2": 1.0, "FR2": 1.45, "FR3": 1.0, "BE2": 1.05, "CHPL": 0.7}[league]
    mv = int(round(max(25_000, (talent ** 1.6) * 1_400_000 * lvl * (1.3 if age <= 23 else 1.0 if age <= 27 else 0.6)
                       * rng.uniform(0.45, 1.6)), -4))
    stats = []
    for back, season in enumerate((f"{today.year % 100 - 1}/{today.year % 100}", f"{today.year % 100 - 2}/{today.year % 100 - 1}")):
        minutes = int(rng.uniform(300, 3000) * (0.6 + 0.5 * talent))
        apps = max(1, minutes // rng.randint(60, 85))
        ga90 = EXPECT[pos] * (0.4 + 1.3 * talent) * rng.uniform(0.7, 1.3)
        involvement = ga90 * minutes / 90
        goals = int(round(involvement * (0.62 if "Forward" in pos else 0.45)))
        assists = int(round(involvement - goals))
        stats.append({"season": season, "competition_id": league, "competition": league,
                      "appearances": apps, "goals": goals, "assists": assists, "minutes": minutes,
                      "yellow": rng.randint(0, 8), "red": int(rng.random() < 0.08)})
    contract = today + timedelta(days=rng.choice([90, 150, 240, 300, 420, 540, 700, 900, 1100]))
    hist, v = [], mv * rng.uniform(0.5, 1.4)
    for k in range(6, 0, -1):
        hist.append([(today - timedelta(days=182 * k)).isoformat(), int(round(v, -4))])
        v = v * rng.uniform(0.85, 1.25)
    n_moves = rng.randint(1, 5)
    moves = sorted(rng.sample(range(2014, today.year + 1), min(n_moves, today.year - 2014)))
    former = rng.random() < 0.08
    transfers = [{"date": f"{y}-07-01", "from": (YS_FORMER[0] if former and j == 0 else "Club fictif"),
                  "to": "Club fictif", "fee": None} for j, y in enumerate(moves)]
    youth = ["Yverdon-Sport FC Jeunesse"] if (city in ("Yverdon-les-Bains", "Orbe") and rng.random() < 0.6) else []
    social = ["https://www.instagram.com/demo"] if rng.random() < 0.65 else []
    # Bloc `perf` fictif pour deux tiers des joueurs : moitié au format API-Football
    # (note + passes clés, pas de xG), moitié au format Sofascore (xG + xA)
    sofa = None
    if rng.random() < 0.66:
        s0 = stats[0]
        xg = max(0.0, s0["goals"] * rng.uniform(0.6, 1.5) + rng.uniform(-1, 1))
        sofa = {"season": stats[0]["season"], "minutes": s0["minutes"], "apps": s0["appearances"],
                "goals": s0["goals"], "assists": s0["assists"],
                "rating": round(6.3 + 1.4 * talent + rng.uniform(-0.3, 0.3), 2)}
        if rng.random() < 0.5:
            sofa.update({"source": "sofascore", "xg": round(xg, 2),
                         "xa": round(max(0.0, s0["assists"] * rng.uniform(0.6, 1.4)), 2)})
        else:
            sofa.update({"source": "api-football", "key_passes": int(s0["assists"] * rng.uniform(4, 9) + rng.randint(0, 10))})
    return {
        "perf": sofa, "id": f"DEMO-{i:03d}", "name": f"{rng.choice(FIRST)} {rng.choice(LAST)}", "url": None,
            "position": pos, "age": age, "citizenship": cits, "birth_city": city, "birth_country": country,
            "market_value": mv, "mv_history": hist, "contract_expires": contract.isoformat(),
            "club": club, "league_id": league, "stats": stats, "transfers": transfers,
            "youth_clubs": youth, "social_media": social, "injury_days": rng.choice([0, 0, 0, 14, 30, 60, 120])}


def build(n: int = 80, seed: int = 1898, today: date | None = None) -> tuple[dict, dict]:
    today = today or date.today()
    rng = random.Random(seed)
    players = [_player(rng, i, today) for i in range(1, n + 1)]
    squad_rng = random.Random(seed + 1)
    squad = []
    for pos, count in (("Goalkeeper", 3), ("Centre-Back", 4), ("Left-Back", 1), ("Right-Back", 2),
                       ("Defensive Midfield", 2), ("Central Midfield", 3), ("Attacking Midfield", 2),
                       ("Left Winger", 1), ("Right Winger", 1), ("Centre-Forward", 2)):
        for _ in range(count):
            age = squad_rng.randint(19, 34)
            squad.append({"id": f"YS-DEMO-{len(squad)+1:02d}", "name": f"Joueur fictif {len(squad)+1}",
                          "position": pos, "age": age,
                          "contract": (today + timedelta(days=squad_rng.choice([200, 280, 400, 650, 900]))).isoformat()})
    cand = {"source": "demo", "genere": today.isoformat(), "demo": True,
            "competitions": sorted({l for l, _ in CLUBS}), "players": players}
    return cand, {"club_id": "demo", "demo": True, "players": squad}


if __name__ == "__main__":
    cand, squad = build()
    (DATA / "demo_candidates.json").write_text(json.dumps(cand, ensure_ascii=False, indent=1))
    (DATA / "demo_squad.json").write_text(json.dumps(squad, ensure_ascii=False, indent=1))
    print(f"→ data/demo_candidates.json ({len(cand['players'])} joueurs fictifs), data/demo_squad.json")
