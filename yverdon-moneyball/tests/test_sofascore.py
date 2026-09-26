import unittest
from datetime import date

from moneyball import model
from moneyball.sofascore import merge_into_candidates, norm_name

TODAY = date.today()


def tm_player(name, age, **kw):
    base = {"id": name, "name": name, "position": "Centre-Forward", "age": age, "citizenship": ["Switzerland"],
            "birth_city": "Lausanne", "birth_country": "Switzerland", "market_value": 300_000, "mv_history": [],
            "contract_expires": "2028-06-30", "club": "X", "league_id": "C2",
            "stats": [{"season": "25/26", "competition_id": "C2", "appearances": 30, "goals": 4, "assists": 2,
                       "minutes": 2500, "yellow": 3, "red": 0}],
            "transfers": [], "youth_clubs": [], "social_media": [], "injury_days": 0}
    base.update(kw)
    return base


def sofa_block(*players):
    return {"216/26/27": {"tournament": 216, "season_id": 1, "season": "26/27",
                          "players": {str(i): p for i, p in enumerate(players)}}}


class SofascoreTest(unittest.TestCase):
    def test_norm_name_strips_accents(self):
        self.assertEqual(norm_name("Théo Müller-Da Silva"), "theo mullerda silva")

    def test_merge_matches_on_name_and_birth_year(self):
        by = TODAY.year - 24
        sofa = sofa_block({"sofascore_id": 1, "name": "Luca Perrin", "birth_year": by, "xg": 6.2, "xa": 1.1,
                           "rating": 7.1, "minutes": 2400, "goals": 3, "assists": 1},
                          {"sofascore_id": 2, "name": "Luca Perrin", "birth_year": by - 8, "xg": 1.0,
                           "rating": 6.4, "minutes": 900, "goals": 1, "assists": 0})
        cands = [tm_player("Luca Perrin", 24), tm_player("Inconnu Total", 22)]
        self.assertEqual(merge_into_candidates(cands, sofa), 1)
        self.assertEqual(cands[0]["sofascore"]["sofascore_id"], 1)
        self.assertNotIn("sofascore", cands[1])

    def test_xg_replaces_goals_in_sport_score(self):
        # Même joueur Transfermarkt ; Sofascore dit qu'il a créé bien plus que ses 4 buts
        plain = tm_player("A", 24)
        rich = tm_player("A", 24, sofascore={"xg": 11.0, "xa": 4.0, "minutes": 2500, "rating": 7.4,
                                             "goals": 4, "assists": 2, "season": "26/27"})
        s_plain, d_plain = model.sport_score(plain)
        s_rich, d_rich = model.sport_score(rich)
        self.assertGreater(s_rich, s_plain)
        self.assertEqual(d_plain["source"], "transfermarkt")
        self.assertEqual(d_rich["source"], "transfermarkt + sofascore")
        self.assertAlmostEqual(d_rich["xg_a_90"], (11 + 0.7 * 4) / 2500 * 90, places=2)

    def test_underperformance_tag(self):
        p = tm_player("B", 24, sofascore={"xg": 9.0, "xa": 1.0, "minutes": 2000, "rating": 6.9,
                                          "goals": 4, "assists": 1, "season": "26/27"})
        needs = {"ATT": {"besoin": 0.5}}
        ranked = model.score_pool([p] + [tm_player(f"f{i}", 25) for i in range(5)],
                                  {"ancrage": .3, "francophonie": .15, "fidelite": .2, "media": .2, "lien_club": .15},
                                  needs, TODAY)
        me = next(r for r in ranked if r["id"] == "B")
        self.assertIn("Sous-performe ses xG", me["tags"])


if __name__ == "__main__":
    unittest.main()
