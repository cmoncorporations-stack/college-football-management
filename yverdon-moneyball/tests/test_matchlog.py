"""Tests du journal match par match 2026/27 sur des extraits HTML réels de www.transfermarkt.com
(tests/fixtures/, lus le 11.10.2026 : Etoile Carouge FC – Yverdon Sport FC, 10e journée de Challenge League)."""
import unittest
from pathlib import Path

from moneyball import matchlog as ml

FIX = Path(__file__).resolve().parent / "fixtures"


def read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


class ParserTest(unittest.TestCase):
    def test_sprite_minute_matches_timeline(self):
        # Vérifié contre la frise chronologique de la feuille 4897476 : 60', 78', 54', 46'.
        self.assertEqual(ml.sprite_minute("background-position: -324px -180px;"), 60)
        self.assertEqual(ml.sprite_minute("background-position: -252px -252px;"), 78)
        self.assertEqual(ml.sprite_minute("background-position: -108px -180px;"), 54)
        self.assertEqual(ml.sprite_minute("background-position: -180px -144px;"), 46)
        self.assertIsNone(ml.sprite_minute(None))

    def test_fixtures_list_all_competitions(self):
        rows = ml.parse_fixtures(read("fixtures_322_2026.html"), "322")
        self.assertEqual(len(rows), 20)
        played = [r for r in rows if r["played"]]
        self.assertEqual(len(played), 11)
        self.assertEqual({r["competition_id"] for r in rows}, {"C2", "SCC"})
        cup = next(r for r in rows if r["competition_id"] == "SCC" and r["played"])
        self.assertEqual((cup["id"], cup["date"], cup["matchday"], cup["home_id"], cup["away_id"], cup["result"]),
                         ("4912844", "2026-08-16", "First Round", "14199", "322", "0:5"))
        last = next(r for r in rows if r["id"] == "4897476")
        self.assertEqual((last["date"], last["matchday"], last["home_id"], last["away_id"], last["result"]),
                         ("2026-10-09", 10, "1292", "322", "0:2"))
        upcoming = next(r for r in rows if r["id"] == "4897485")
        self.assertFalse(upcoming["played"])
        self.assertIsNone(upcoming["result"])

    def test_match_report_header_and_events(self):
        rep = ml.parse_match_report(read("match_4897476_index.html"))
        self.assertEqual(rep["date"], "2026-10-09")
        self.assertEqual(rep["matchday"], 10)
        self.assertEqual((rep["competition_id"], rep["competition"]), ("C2", "Challenge League"))
        self.assertEqual((rep["home_id"], rep["away_id"]), ("1292", "322"))
        self.assertEqual(rep["score"], (0, 2))
        self.assertFalse(rep["extra_time"])
        self.assertEqual([(g["minute"], g["team"], g["scorer_id"], g["assist_id"]) for g in rep["goals"]],
                         [(60, "away", "289404", "294583"), (78, "away", "390179", None)])
        self.assertEqual(len(rep["subs"]), 10)
        self.assertEqual(rep["subs"][0], {"minute": 46, "team": "away", "in_id": "310361", "out_id": "98167",
                                          "reason": "Tactical"})
        self.assertEqual([(c["minute"], c["player_id"], c["type"]) for c in rep["cards"]],
                         [(54, "1071955", "yellow"), (73, "653483", "yellow"), (75, "417895", "yellow")])
        self.assertEqual(len(rep["bench"]["home"]), 9)
        self.assertEqual(rep["bench"]["away"][0], {"id": "894261", "name": "Maxime Rouiller", "position": "GK"})

    def test_lineups_with_positions(self):
        lu = ml.parse_lineups(read("match_4897476_lineups.html"))
        self.assertEqual(set(lu), {"1292", "322"})
        self.assertEqual(len(lu["322"]["start"]), 11)
        self.assertEqual(len(lu["322"]["bench"]), 9)
        self.assertEqual(lu["322"]["start"][0], {"id": "237664", "name": "Simon Enzler", "position": "Goalkeeper"})
        self.assertEqual(lu["322"]["bench"][0]["position"], "Goalkeeper")
        self.assertEqual(lu["1292"]["name"], "Etoile Carouge FC")

    def test_injuries_and_club_absences(self):
        inj = ml.parse_injuries(read("injuries_417895.html"))
        self.assertEqual(inj[0], {"season": "25/26", "injury": "unknown injury", "from": "2026-02-18",
                                  "until": "2026-03-11", "days": 22, "games_missed": 3})
        self.assertEqual(len(inj), 11)
        absent = ml.parse_club_absences(read("absences_322.html"))
        self.assertEqual([(a["player_id"], a["category"], a["since"], a["missed_matches"]) for a in absent],
                         [("154475", "injured", "2026-09-16", 2), ("583015", "injured", "2026-10-07", 1)])

    def test_competition_type(self):
        self.assertEqual(ml.competition_type("C2", "Challenge League"), "league")
        self.assertEqual(ml.competition_type("SCC", "Swiss Cup"), "national_cup")
        self.assertEqual(ml.competition_type("CDF", "Coupe de France"), "national_cup")
        self.assertEqual(ml.competition_type("UCOL", "UEFA Conference League"), "european")
        self.assertEqual(ml.competition_type(None, "Total"), "other")


class EntryTest(unittest.TestCase):
    def setUp(self):
        self.rep = ml.parse_match_report(read("match_4897476_index.html"))
        self.lu = ml.parse_lineups(read("match_4897476_lineups.html"))
        self.match = next(m for m in ml.parse_fixtures(read("fixtures_322_2026.html"), "322") if m["id"] == "4897476")

    def entry(self, pid, club="322"):
        return ml.player_match_entry(pid, club, self.match, self.rep, self.lu)

    def test_starter_full_match_keeper(self):
        e = self.entry("237664")
        self.assertEqual((e["status"], e["started"], e["minutes"], e["conceded"], e["position"]),
                         ("played", True, 90, 0, "Goalkeeper"))
        self.assertEqual((e["home"], e["opponent_id"], e["result"], e["outcome"]), (False, "1292", "2:0", "V"))

    def test_substitute_scores(self):
        e = self.entry("390179")   # Bastien Toma : entré à la 59e, buteur à la 78e
        self.assertEqual((e["status"], e["started"], e["subbed_on_minute"], e["minutes"], e["goals"]),
                         ("played", False, 59, 31, 1))

    def test_half_time_substitution_counts_45(self):
        out = self.entry("98167")    # Valon Fazliu sorti à la 46e
        on = self.entry("310361")    # Varol Tasar entré à la 46e
        self.assertEqual((out["minutes"], out["subbed_off_minute"]), (45, 46))
        self.assertEqual((on["minutes"], on["subbed_on_minute"]), (45, 46))

    def test_assist_yellow_bench_and_absent(self):
        self.assertEqual(self.entry("294583")["assists"], 1)          # Demhasaj passeur
        self.assertEqual(self.entry("417895")["yellow"], 1)           # Pos averti
        self.assertEqual(self.entry("894261")["status"], "bench")     # Rouiller sur le banc
        self.assertEqual(self.entry("999999")["status"], "not_in_squad")
        self.assertIsNone(self.entry("237664", club="12345"))         # club absent de la feuille

    def test_home_side_conceded_on_pitch(self):
        # Carouge : Zrankeon sorti à la 87e a encaissé les deux buts (60e, 78e) ; Boulkous sorti à la 65e un seul.
        self.assertEqual(self.entry("819474", "1292")["conceded"], 2)
        self.assertEqual(self.entry("1406429", "1292")["conceded"], 1)
        self.assertEqual(self.entry("819474", "1292")["outcome"], "D")


class AssembleTest(unittest.TestCase):
    def test_statuses_from_injuries_red_cards_and_arrival(self):
        rep = ml.parse_match_report(read("match_4897476_index.html"))
        lu = ml.parse_lineups(read("match_4897476_lineups.html"))
        fixtures = ml.parse_fixtures(read("fixtures_322_2026.html"), "322")
        m10 = next(m for m in fixtures if m["id"] == "4897476")
        # Deux « matchs » : la même feuille datée du 20.09 (journée 9) puis la vraie du 09.10.
        m9 = {**m10, "id": "prev", "date": "2026-09-20", "matchday": 9}
        rep9 = {**rep, "date": "2026-09-20", "matchday": 9,
                "cards": rep["cards"] + [{"minute": 80, "team": "away", "player_id": "333", "type": "red"}]}
        lu9 = {**lu, "322": {**lu["322"], "start": lu["322"]["start"][:10] + [{"id": "333", "name": "Expulsé",
                                                                              "position": "Centre-Back"}]}}
        players = {"237664": {"name": "Simon Enzler", "club_id": "322"},
                   "333": {"name": "Expulsé", "club_id": "322"},
                   "154475": {"name": "Aurélien Chappuis", "club_id": "322"},
                   "111": {"name": "Recrue", "club_id": "322"},
                   "222": {"name": "Inconnu", "club_id": "322"}}
        injuries = {"154475": [{"season": "26/27", "injury": "unknown injury", "from": "2026-09-16", "until": None,
                                "days": 25, "games_missed": 2}]}
        logs = ml.assemble(players, {"322": [m9, m10]}, {"prev": rep9, "4897476": rep},
                           {"prev": lu9, "4897476": lu}, injuries, {"322": []}, "2026-10-11",
                           joined={"111": "2026-10-01"})
        self.assertEqual([e["status"] for e in logs["237664"]["entries"]], ["played", "played"])
        self.assertEqual([e["status"] for e in logs["333"]["entries"]], ["played", "suspended"])
        self.assertEqual(logs["333"]["entries"][0]["red"], 1)
        self.assertEqual(logs["333"]["entries"][0]["minutes"], 80)
        self.assertEqual([e["status"] for e in logs["154475"]["entries"]], ["injured", "injured"])
        self.assertEqual(logs["154475"]["current_status"]["status"], "injured")
        self.assertEqual([e["status"] for e in logs["111"]["entries"]], ["other", "not_in_squad"])
        self.assertEqual([e["status"] for e in logs["222"]["entries"]], ["not_in_squad", "not_in_squad"])
        self.assertIsNone(logs["222"]["current_status"])
        self.assertEqual(logs["237664"]["team_matches"], 2)


if __name__ == "__main__":
    unittest.main()
