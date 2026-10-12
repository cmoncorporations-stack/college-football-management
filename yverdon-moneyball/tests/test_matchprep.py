"""Tests de la préparation de match : statistiques de tirs (extrait réel), xG-tirs, notes Poisson, coupe, onze, saison."""
import math
import unittest
from collections import Counter
from pathlib import Path

from moneyball import matchprep as mp

FIX = Path(__file__).resolve().parent / "fixtures"


class StatsPageTest(unittest.TestCase):
    def test_real_stats_page(self):
        # Etoile Carouge FC – Yverdon Sport FC, 09.10.2026 (0:2) : possession non publiée en Challenge League.
        st = mp.parse_match_stats((FIX / "match_4897476_stats.html").read_text(encoding="utf-8"))
        self.assertEqual(st["home"], {"shots": 10, "off_target": 2, "saved": 3, "corners": 4, "free_kicks": 15,
                                      "fouls": 15, "offsides": 0})
        self.assertEqual((st["away"]["shots"], st["away"]["off_target"], st["away"]["saved"]), (14, 6, 3))
        self.assertIsNone(mp.parse_match_stats("<div>pas de statistiques</div>"))

    def test_xg_from_shots(self):
        model = {"par_tir_cadre": 0.3, "par_tir_non_cadre": 0.02}
        # Yverdon : 3 tirs arrêtés + 2 buts = 5 cadrés, 6 non cadrés
        self.assertAlmostEqual(mp.xg_of({"saved": 3, "off_target": 6}, 2, model), 1.62)


class ModelTest(unittest.TestCase):
    def test_fit_xg_recovers_coefficients(self):
        rows = [{"on_target": on, "off_target": off, "goals": 0.3 * on + 0.02 * off}
                for on in range(1, 9) for off in range(0, 10, 3)]
        fit = mp.fit_xg(rows)
        self.assertAlmostEqual(fit["par_tir_cadre"], 0.3, places=3)
        self.assertAlmostEqual(fit["par_tir_non_cadre"], 0.02, places=3)
        self.assertAlmostEqual(fit["r2"], 1.0, places=3)

    def test_ratings_rank_strong_team_first(self):
        teams = ["A", "B", "C", "D"]
        strength = {"A": 2.4, "B": 1.4, "C": 1.0, "D": 0.6}
        matches = [{"home": h, "away": a, "y_home": strength[h] * 1.2 / strength[a] ** 0.5,
                    "y_away": strength[a] / strength[h] ** 0.5}
                   for h in teams for a in teams if h != a for _ in range(2)]
        r = mp.fit_ratings(matches, teams, prior_matches=0)
        self.assertEqual(sorted(teams, key=lambda t: -r["att"][t]), ["A", "B", "C", "D"])
        self.assertEqual(sorted(teams, key=lambda t: -r["def"][t]), ["A", "B", "C", "D"])
        self.assertGreater(r["home"], 0)
        self.assertAlmostEqual(sum(r["att"].values()), 0.0, places=6)
        # le prior tire une équipe sans match vers sa note a priori
        r2 = mp.fit_ratings(matches, teams + ["E"], prior={"E": (0.4, 0.0)})
        self.assertGreater(r2["att"]["E"], r2["att"]["C"])

    def test_outcome_and_knockout(self):
        p = mp.outcome_probs(1.8, 1.2)
        self.assertAlmostEqual(p["dom"] + p["nul"] + p["ext"], 1.0, places=3)
        self.assertGreater(p["dom"], p["ext"])
        self.assertAlmostEqual(p["clean_sheet_dom"], math.exp(-1.2), places=4)
        ko = mp.knockout(0.7, 3.4)
        self.assertGreater(ko["qualif_ext"], ko["ext"])
        self.assertAlmostEqual(ko["qualif_dom"] + ko["qualif_ext"], 1.0, places=4)
        self.assertLessEqual(ko["tirs_au_but"], ko["prolongation"])

    def test_cup_model_learns_level_gap(self):
        rows = []
        for gap, g in ((1.2, 4), (1.2, 5), (0.8, 3), (0.8, 2), (1.5, 6), (1.0, 3)):
            rows.append({"goals": g, "gap": gap, "home": 0})
            rows.append({"goals": 0 if gap > 1.1 else 1, "gap": -gap, "home": 1})
        m = mp.fit_cup_model(rows)
        self.assertGreater(m["beta"], 0.5)
        self.assertEqual(m["n"], 12)

    def test_simulation_keeps_current_points(self):
        ratings = {"mu": 0.2, "home": 0.2, "att": {"A": 0.3, "B": -0.3}, "def": {"A": 0.3, "B": -0.3}}
        sim = mp.simulate_season(ratings, [{"home": "A", "away": "B", "score": (2, 0)}],
                                 [{"home": "B", "away": "A"}], ["A", "B"], n=2000)
        self.assertEqual(sim["A"]["points_actuels"], 3)
        self.assertGreater(sim["A"]["p_rang"][1], 0.8)
        self.assertAlmostEqual(sim["A"]["p_rang"][1] + sim["B"]["p_rang"][1], 1.0, places=3)


class LineupTest(unittest.TestCase):
    def player(self, pid, pos, sel, played=None):
        return {"id": pid, "name": pid, "position": pos, "selection": sel, "sport": 50.0, "part_3_derniers": 1.0,
                "dynamique": 0.5, "positions": Counter(played or {pos: 5})}

    def test_slot_fit(self):
        self.assertEqual(mp.slot_fit(Counter({"Centre-Back": 10}), "Centre-Back", "CB1"), 1.0)
        self.assertEqual(mp.slot_fit(Counter({"Centre-Back": 10}), "Centre-Back", "DM"), 0.75)
        self.assertEqual(mp.slot_fit(Counter(), "Goalkeeper", "GK"), 1.0)
        self.assertEqual(mp.slot_fit(Counter({"Goalkeeper": 3}), "Goalkeeper", "CF"), 0.0)
        self.assertAlmostEqual(mp.slot_fit(Counter({"Centre-Back": 4, "Defensive Midfield": 4}), None, "DM"), 0.875)

    def test_pick_xi_respects_positions(self):
        squad = [self.player("gk", "Goalkeeper", 0.8), self.player("gk2", "Goalkeeper", 0.3),
                 self.player("rb", "Right-Back", 0.7), self.player("lb", "Left-Back", 0.7),
                 self.player("cb1", "Centre-Back", 0.9), self.player("cb2", "Centre-Back", 0.6),
                 self.player("dm", "Defensive Midfield", 0.5, {"Defensive Midfield": 3, "Centre-Back": 4}),
                 self.player("cm1", "Central Midfield", 0.6), self.player("am", "Attacking Midfield", 0.7),
                 self.player("rw", "Right Winger", 0.6), self.player("lw", "Left Winger", 0.6),
                 self.player("cf", "Centre-Forward", 0.7), self.player("cf2", "Centre-Forward", 0.2)]
        xi = {r["creneau"]: r["id"] for r in mp.pick_xi(squad)}
        self.assertEqual(len(xi), 11)
        self.assertEqual(xi["GK"], "gk")
        self.assertEqual({xi["CB1"], xi["CB2"]}, {"cb1", "cb2"})
        self.assertEqual(xi["DM"], "dm")
        self.assertEqual(xi["CF"], "cf")
        self.assertNotIn("cf2", xi.values())


if __name__ == "__main__":
    unittest.main()
