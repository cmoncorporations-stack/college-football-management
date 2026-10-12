import unittest

from moneyball import model
from moneyball.apifootball import merge_into_candidates, normalize_row
from tests.test_sofascore import tm_player

ROW = {"player": {"id": 501, "name": "L. Perrin", "firstname": "Luca", "lastname": "Perrin", "age": 24,
                  "birth": {"date": "2002-03-14", "place": "Yverdon-les-Bains", "country": "Switzerland"},
                  "nationality": "Switzerland"},
       "statistics": [{"team": {"id": 1, "name": "FC Aarau"}, "league": {"id": 208, "name": "Challenge League", "season": 2026},
                       "games": {"appearences": 12, "lineups": 11, "minutes": 980, "position": "Attacker", "rating": "7.183333"},
                       "shots": {"total": 30, "on": 14}, "goals": {"total": 6, "conceded": 0, "assists": 2, "saves": None},
                       "passes": {"total": 300, "key": 18, "accuracy": 78}, "tackles": {"total": 8, "blocks": 1, "interceptions": 3},
                       "duels": {"total": 120, "won": 60}, "dribbles": {"attempts": 25, "success": 13, "past": None},
                       "fouls": {"drawn": 10, "committed": 8}, "cards": {"yellow": 2, "yellowred": 0, "red": 0},
                       "penalty": {"won": None, "commited": None, "scored": 1, "missed": 0, "saved": None}}]}


class ApiFootballTest(unittest.TestCase):
    def test_normalize_row(self):
        rec = normalize_row(ROW, 208, 2026)
        self.assertEqual(rec["source"], "api-football")
        self.assertEqual(rec["birth_date"], "2002-03-14")
        self.assertEqual((rec["apps"], rec["minutes"], rec["goals"], rec["assists"], rec["key_passes"]), (12, 980, 6, 2, 18))
        self.assertAlmostEqual(rec["rating"], 7.183333, places=5)
        self.assertEqual(rec["season"], "2026/27")

    def test_merge_prefers_birth_date(self):
        rec = normalize_row(ROW, 208, 2026)
        blocks = {"208/2026": {"league": 208, "season": 2026, "players": {"501": rec}}}
        good = tm_player("Luca Perrin", 24, date_of_birth="2002-03-14")
        homonym = tm_player("Luca Perrin", 31, date_of_birth="1995-01-01")
        self.assertEqual(merge_into_candidates([good, homonym], blocks), 1)
        self.assertIn("perf", good)
        self.assertNotIn("perf", homonym)

    def test_key_passes_feed_sport_score(self):
        rec = normalize_row(ROW, 208, 2026)
        plain = tm_player("A", 24)
        rich = tm_player("A", 24, perf=rec)
        s_plain, _ = model.sport_score(plain)
        s_rich, d = model.sport_score(rich)
        self.assertEqual(d["source"], "transfermarkt + api-football")
        self.assertAlmostEqual(d["xg_a_90"], (6 + 0.7 * 2 + 0.1 * 18) / 980 * 90, places=2)
        self.assertGreater(s_rich, s_plain)


if __name__ == "__main__":
    unittest.main()
