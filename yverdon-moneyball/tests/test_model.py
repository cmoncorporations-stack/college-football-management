import unittest
from datetime import date

from moneyball import demo, model
from moneyball.club import CLUB
from moneyball.fan_dna import BASE_WEIGHTS, COCKPIT, derive_fan_dna, load_cockpit
from moneyball.scout import normalize

TODAY = date(2026, 9, 25)


def fan_weights() -> dict:
    """Poids du cockpit du club ; poids de base si le cockpit n'est pas versionné (dépôt public)."""
    return derive_fan_dna(load_cockpit())["weights"] if COCKPIT.exists() else dict(BASE_WEIGHTS)


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
    @unittest.skipUnless(COCKPIT.exists(), "data/fanbase_cockpit.json absent : importer le cockpit du club")
    def test_weights_sum_to_one_and_follow_cockpit(self):
        cockpit = load_cockpit()
        dna = derive_fan_dna(cockpit)
        self.assertAlmostEqual(sum(dna["weights"].values()), 1.0, places=2)
        # Pénétration au-dessus de 18 % : ancrage renforcé. Base dominée par les New Leads :
        # fidélité renforcée, sauf si le cockpit dit le scoring inexploitable (multiplicateur 1,0).
        self.assertEqual(dna["multiplicateurs"]["ancrage"] > 1, cockpit["penetration"]["indice"] > 18)
        if cockpit["brevo"].get("scoringExploitable", True):
            self.assertGreater(dna["multiplicateurs"]["fidelite"], 1)
        else:
            self.assertEqual(dna["multiplicateurs"]["fidelite"], 1.0)


class ModelTest(unittest.TestCase):
    def test_local_player_scores_higher_fan_fit(self):
        local = model.fan_components(player(birth_city=CLUB["bassin"][0].title()))
        foreign = model.fan_components(player(birth_city="Porto", birth_country="Portugal", citizenship=["Portugal"]))
        self.assertEqual(local["ancrage"], 1.0)
        self.assertLess(foreign["ancrage"], local["ancrage"])
        self.assertLess(foreign["francophonie"], local["francophonie"])

    def test_former_player_detected(self):
        fc = model.fan_components(player(transfers=[{"date": "2020-07-01", "from": CLUB["name"], "to": "X"}]))
        self.assertEqual(fc["_lien"], model.LIEN_ANCIEN)

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
        pool = [player(id=str(i), market_value=200_000 + 20_000 * i, age=20 + i % 9) for i in range(30)]
        sport = {p["id"]: 60.0 for p in pool}
        u = model.undervaluation(pool, sport)
        self.assertGreater(u["0"], u["29"])

    def test_undervaluation_controls_for_league(self):
        # Même profil : les joueurs de FR3 valent structurellement moins ; le plus cher de
        # chaque ligue doit ressortir moins sous-évalué que le moins cher de la même ligue.
        pool = ([player(id=f"c{i}", league_id="C1", market_value=500_000 + 30_000 * i, age=21 + i % 8) for i in range(15)]
                + [player(id=f"f{i}", league_id="FR3", market_value=150_000 + 10_000 * i, age=21 + i % 8) for i in range(15)])
        sport = {p["id"]: 60.0 for p in pool}
        u = model.undervaluation(pool, sport)
        self.assertGreater(u["f0"], u["f14"])
        self.assertGreater(u["c0"], u["c14"])
        self.assertLess(u["f14"], 0.9)   # pas « sous-évalué » juste parce qu'il joue en National

    def test_shrinkage_tames_small_samples(self):
        pool = [player(id=f"r{i}", stats=[{**player()["stats"][0], "goals": 3 + i % 9, "assists": i % 4}]) for i in range(40)]
        base = model.pool_baselines(pool)
        self.assertGreaterEqual(base["ATT"]["n"], 20)
        hot_streak = player(id="h", stats=[{**player()["stats"][0], "goals": 2, "assists": 0, "minutes": 180, "appearances": 3}])
        full_season = player(id="f", stats=[{**player()["stats"][0], "goals": 12, "assists": 3, "minutes": 2500, "appearances": 30}])
        s_hot, d_hot = model.sport_score(hot_streak, base)
        s_full, d_full = model.sport_score(full_season, base)
        self.assertLess(s_hot, s_full)                # 1 but/90 sur 180 min ne bat pas une vraie saison
        self.assertLess(d_hot["rang_production"], 0.62)   # rang tiré vers le milieu : confiance < 0,2
        self.assertLess(d_hot["confiance"], 0.2)
        self.assertEqual(d_full["reference"], "vivier")

    def test_projection_and_net_gain(self):
        young_free = player(id="y", age=21, market_value=300_000, contract_expires="2027-01-31")
        old_locked = player(id="o", age=30, market_value=300_000, contract_expires="2029-06-30")
        vy = model.value_components(young_free, TODAY)
        vo = model.value_components(old_locked, TODAY)
        self.assertGreater(vy["valeur_projetee_24m"], 300_000)
        self.assertLess(vo["valeur_projetee_24m"], 300_000)
        self.assertGreater(vy["plus_value"], vo["plus_value"])
        self.assertEqual(model.age_growth_24m("GK", 25), 0.30)
        self.assertEqual(model.age_growth_24m("ATT", 25), 0.12)   # les gardiens culminent plus tard

    def test_season_key(self):
        self.assertEqual(model._season_key("25/26"), 2025)
        self.assertEqual(model._season_key("2024"), 2024)

    def test_full_demo_pipeline(self):
        cand, squad = demo.build(today=TODAY)
        needs = model.squad_needs(squad["players"], TODAY)
        ranked = model.score_pool(cand["players"], fan_weights(), needs, TODAY)
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
        tr = {"transfers": [{"id": "1", "club_from": {"id": "2", "name": CLUB["name"]},
                             "club_to": {"id": "1", "name": "FC X"}, "date": "2024-07-01"}],
              "youth_clubs": ["FC Orbe"]}
        p = normalize("123", "C2", {"name": "A B"}, profile, stats, mv, tr, None)
        self.assertEqual(p["position"], "Left Winger")
        self.assertEqual(p["stats"][0]["minutes"], 1500)
        self.assertEqual(p["mv_history"], [["2025-06-01", 250000]])
        self.assertEqual(model.fan_components(p)["_lien"], model.LIEN_ANCIEN)


if __name__ == "__main__":
    unittest.main()
