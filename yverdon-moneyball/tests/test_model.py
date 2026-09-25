import unittest
from datetime import date

from moneyball import demo, model
from moneyball.fan_dna import derive_fan_dna, load_cockpit
from moneyball.scout import normalize

TODAY = date(2026, 9, 25)


def player(**kw):
    base = {"id": "p", "name": "Test", "position": "Centre-Forward", "age": 24, "citizenship": ["Switzerland"],
            "birth_city": "Zürich", "birth_country": "Switzerland", "market_value": 300_000, "mv_history": [],
            "contract_expires": "2028-06-30", "club": "X", "league_id": "C2",
            "stats": [{"season": "25/26", "competition_id": "C2", "appearances": 30, "goals": 10, "assists": 5,
                       "minutes": 2500, "yellow": 3, "red": 0}],
            "transfers": [], "youth_clubs": [], "social_media": [], "injury_days": 0}
    base.update(kw)
    return base


class FanDnaTest(unittest.TestCase):
    def test_weights_sum_to_one_and_follow_cockpit(self):
        dna = derive_fan_dna(load_cockpit())
        self.assertAlmostEqual(sum(dna["weights"].values()), 1.0, places=2)
        # Pénétration 25,2 % (> 18) et 70 % de New Leads : ancrage et fidélité renforcés
        self.assertGreater(dna["multiplicateurs"]["ancrage"], 1)
        self.assertGreater(dna["multiplicateurs"]["fidelite"], 1)


class ModelTest(unittest.TestCase):
    def test_local_player_scores_higher_fan_fit(self):
        local = model.fan_components(player(birth_city="Yverdon-les-Bains"))
        foreign = model.fan_components(player(birth_city="Porto", birth_country="Portugal", citizenship=["Portugal"]))
        self.assertEqual(local["ancrage"], 1.0)
        self.assertLess(foreign["ancrage"], local["ancrage"])
        self.assertLess(foreign["francophonie"], local["francophonie"])

    def test_former_player_detected(self):
        fc = model.fan_components(player(transfers=[{"date": "2020-07-01", "from": "Yverdon-Sport FC", "to": "X"}]))
        self.assertEqual(fc["_lien"], "Ancien d'Yverdon")

    def test_expiring_contract_is_free(self):
        p = player(contract_expires="2027-01-31")
        self.assertEqual(model.estimated_fee(p, TODAY), 0)
        self.assertEqual(model.value_components(p, TODAY)["contrat"], 1.0)

    def test_production_raises_sport_score(self):
        weak = player(stats=[{**player()["stats"][0], "goals": 1, "assists": 0}])
        self.assertGreater(model.sport_score(player())[0], model.sport_score(weak)[0])

    def test_position_groups(self):
        self.assertEqual(model.position_group("Goalkeeper"), "GK")
        self.assertEqual(model.position_group("Left-Back"), "DEF")
        self.assertEqual(model.position_group("Attacking Midfield"), "MID")
        self.assertEqual(model.position_group("Right Winger"), "ATT")

    def test_undervaluation_prefers_cheap_equal_player(self):
        pool = [player(id=str(i), market_value=200_000 + 50_000 * i) for i in range(8)]
        sport = {p["id"]: 60.0 for p in pool}
        u = model.undervaluation(pool, sport)
        self.assertGreater(u["0"], u["7"])

    def test_season_key(self):
        self.assertEqual(model._season_key("25/26"), 2025)
        self.assertEqual(model._season_key("2024"), 2024)

    def test_full_demo_pipeline(self):
        cand, squad = demo.build(today=TODAY)
        needs = model.squad_needs(squad["players"], TODAY)
        ranked = model.score_pool(cand["players"], derive_fan_dna(load_cockpit())["weights"], needs, TODAY)
        self.assertEqual(len(ranked), len(cand["players"]))
        self.assertTrue(all(0 <= r["scores"]["final"] <= 100 for r in ranked))
        self.assertEqual(ranked, sorted(ranked, key=lambda r: -r["scores"]["final"]))


class NormalizeTest(unittest.TestCase):
    def test_transfermarkt_payload_shape(self):
        profile = {"name": "A B", "url": "https://www.transfermarkt.com/x", "age": 22,
                   "place_of_birth": {"city": "Orbe", "country": "Switzerland"}, "citizenship": ["Switzerland"],
                   "position": {"main": "Left Winger"}, "market_value": 400000,
                   "club": {"name": "FC X", "contract_expires": "2027-06-30"}, "socialMedia": ["https://instagram.com/a"]}
        stats = [{"competition_id": "C2", "competition_name": "Challenge League", "season_id": "25/26",
                  "club_id": "1", "appearances": 20, "goals": 4, "assists": 3, "minutes_played": 1500}]
        mv = {"marketValueHistory": [{"age": 21, "date": "2025-06-01", "club_id": "1", "club_name": "FC X",
                                      "market_value": 250000}]}
        tr = {"transfers": [{"id": "1", "club_from": {"id": "2", "name": "Yverdon-Sport FC"},
                             "club_to": {"id": "1", "name": "FC X"}, "date": "2024-07-01"}],
              "youth_clubs": ["FC Orbe"]}
        p = normalize("123", "C2", {"name": "A B"}, profile, stats, mv, tr, None)
        self.assertEqual(p["position"], "Left Winger")
        self.assertEqual(p["stats"][0]["minutes"], 1500)
        self.assertEqual(p["mv_history"], [["2025-06-01", 250000]])
        self.assertEqual(model.fan_components(p)["_lien"], "Ancien d'Yverdon")


if __name__ == "__main__":
    unittest.main()
