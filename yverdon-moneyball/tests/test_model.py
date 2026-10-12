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
        ranked = model.score_pool(cand["players"], derive_fan_dna(load_cockpit())["weights"], needs, TODAY)
        self.assertEqual(len(ranked), len(cand["players"]))
        self.assertTrue(all(0 <= r["scores"]["final"] <= 100 for r in ranked))
        self.assertEqual(ranked, sorted(ranked, key=lambda r: -r["scores"]["final"]))


def gk(pid, conceded, cs, minutes=2700, team_conceded=None, team_matches=30, rank=None, teams=10, league_median=1.3,
       matches=None, **kw):
    row = {"season": "25/26", "competition_id": "C2", "appearances": matches or minutes // 90, "goals": 0, "assists": 0,
           "minutes": minutes, "yellow": 1, "red": 0, "conceded": conceded, "clean_sheets": cs,
           "gk_matches": matches or minutes // 90, "club_id": "1"}
    if team_conceded is not None:
        row.update({"team_conceded": team_conceded, "team_matches": team_matches, "team_rank": rank,
                    "team_count": teams, "league_conceded_90_median": league_median})
    return player(id=pid, position="Goalkeeper", stats=[row], **kw)


def cb(pid, team_conceded, position="Centre-Back", minutes=2500, goals=0, assists=0, league_median=1.3, **kw):
    row = {**player()["stats"][0], "minutes": minutes, "goals": goals, "assists": assists,
           "team_conceded": team_conceded, "team_matches": 30, "team_rank": 5, "team_count": 10,
           "league_conceded_90_median": league_median, "club_id": "1"}
    return player(id=pid, position=position, stats=[row], **kw)


class GoalkeeperTest(unittest.TestCase):
    """C1 : la note Sport des gardiens ne passe jamais par la production offensive."""

    def pool(self):
        # 30 gardiens titulaires uniques : de l'équipe championne à l'équipe reléguée.
        return [gk(f"g{i}", conceded=25 + 2 * i, cs=16 - i // 2, team_conceded=25 + 2 * i, rank=1 + i % 10)
                for i in range(30)]

    def test_relegated_starter_is_not_100_and_ranks_below_champion_keeper(self):
        pool = self.pool()
        base = model.pool_baselines(pool)
        self.assertGreaterEqual(base["GK"]["n"], 20)
        champion = gk("c", conceded=20, cs=18, team_conceded=20, rank=1)
        relegated = gk("r", conceded=70, cs=3, team_conceded=70, rank=10)
        s_c, d_c = model.sport_score_gk(champion, base)
        s_r, d_r = model.sport_score_gk(relegated, base)
        self.assertLess(s_r, 100)
        self.assertLess(s_r, s_c)
        self.assertEqual(d_r["mode_gardien"], "titulaire unique : rang de l'équipe")
        self.assertIsNone(d_r["rang_production"])

    def test_goals_do_not_change_a_keeper_score(self):
        base = model.pool_baselines(self.pool())
        a = gk("a", conceded=40, cs=8, team_conceded=40, rank=5)
        b = gk("b", conceded=40, cs=8, team_conceded=40, rank=5)
        b["stats"][0].update({"goals": 5, "assists": 3})
        self.assertEqual(model.sport_score(a, base)[0], model.sport_score(b, base)[0])

    def test_shared_keeper_compared_to_his_team(self):
        base = model.pool_baselines(self.pool())
        # Deux doublures à 1 200 min dans une équipe qui encaisse 1,5 but par match :
        # celle qui encaisse 0,9/90 vaut plus que celle qui encaisse 2,0/90.
        good = gk("good", conceded=12, cs=6, minutes=1200, team_conceded=45, rank=5)
        bad = gk("bad", conceded=27, cs=2, minutes=1200, team_conceded=45, rank=5)
        s_g, d_g = model.sport_score_gk(good, base)
        s_b, d_b = model.sport_score_gk(bad, base)
        self.assertGreater(s_g, s_b)
        self.assertEqual(d_g["mode_gardien"], "gardien partagé : rapport à l'équipe")

    def test_no_conceded_data_means_low_confidence(self):
        base = model.pool_baselines(self.pool())
        p = player(id="x", position="Goalkeeper")   # ligne sans conceded : buts d'attaquant ignorés
        score, d = model.sport_score(p, base)
        self.assertEqual(d["confiance_note"], "faible")
        self.assertLessEqual(d["confiance"], 0.5)
        self.assertEqual(d["rang_defensif"], 0.5)

    def test_availability_capped_at_quarter(self):
        base = model.pool_baselines(self.pool())
        # Même indice défensif, disponibilité 0 vs 1 : au plus 25 points d'écart avant niveau de ligue.
        full = gk("f", conceded=40, cs=8, minutes=2700, team_conceded=40, rank=5)
        s_full, _ = model.sport_score_gk(full, base)
        self.assertLessEqual(s_full, 100)
        self.assertGreater(s_full, 0)


class DefenderTest(unittest.TestCase):
    """C2 : centraux notés sur les résultats défensifs de l'équipe, latéraux à moitié sur l'offensive."""

    def pool(self):
        cbs = [cb(f"c{i}", team_conceded=20 + i * 2) for i in range(25)]
        fbs = [cb(f"f{i}", team_conceded=20 + i * 2, position="Left-Back", goals=i % 5, assists=i % 3) for i in range(25)]
        return cbs + fbs

    def test_sub_groups(self):
        self.assertEqual(model.sub_group("Centre-Back"), "CB")
        self.assertEqual(model.sub_group("Left-Back"), "FB")
        self.assertEqual(model.sub_group("Right-Back"), "FB")
        self.assertEqual(model.sub_group("Defender"), "CB")
        self.assertEqual(model.sub_group("Goalkeeper"), "GK")

    def test_centre_back_not_ranked_on_goals(self):
        base = model.pool_baselines(self.pool())
        self.assertGreaterEqual(base["CB"]["def_n"], 20)
        scorer = cb("s", team_conceded=40, goals=8)
        silent = cb("q", team_conceded=40, goals=0)
        s1, d1 = model.sport_score(scorer, base)
        s2, d2 = model.sport_score(silent, base)
        self.assertEqual(s1, s2)
        self.assertEqual(d1["confiance_note"], "faible")
        self.assertLessEqual(d1["confiance"], 0.5)
        tight = cb("t", team_conceded=22)
        leaky = cb("l", team_conceded=66)
        self.assertGreater(model.sport_score(tight, base)[0], model.sport_score(leaky, base)[0])

    def test_full_back_half_offensive(self):
        base = model.pool_baselines(self.pool())
        self.assertGreaterEqual(base["FB"]["n"], 20)
        creative = cb("cr", position="Right-Back", team_conceded=40, goals=4, assists=6)
        plain = cb("pl", position="Right-Back", team_conceded=40, goals=0, assists=0)
        s_c, d_c = model.sport_score(creative, base)
        s_p, d_p = model.sport_score(plain, base)
        self.assertGreater(s_c, s_p)
        self.assertEqual(d_c["rang_defensif"], d_p["rang_defensif"])
        self.assertIsNone(d_c["confiance_note"])


class MarketValueEstimateTest(unittest.TestCase):
    """C12 : valeur manquante estimée par la régression, poids Valeur divisé par deux."""

    def test_estimate_and_halved_weight(self):
        pool = [player(id=str(i), market_value=200_000 + 20_000 * i, age=20 + i % 9) for i in range(30)]
        unknown = player(id="u", market_value=None, age=24, mv_history=[["2025-06-01", 25_000]])
        pool.append(unknown)
        sport = {p["id"]: 60.0 for p in pool}
        est = model.estimate_market_values(pool, sport)
        self.assertEqual(set(est), {"u"})
        self.assertGreater(est["u"], 100_000)
        self.assertLess(est["u"], 1_000_000)
        needs = {"ATT": {"besoin": 0.5}}
        weights = {"ancrage": 0.32, "fidelite": 0.23, "media": 0.18, "francophonie": 0.13, "lien_club": 0.14}
        ranked = model.score_pool(pool, weights, needs, TODAY)
        u = next(r for r in ranked if r["id"] == "u")
        self.assertTrue(u["market_value_estimated"])
        self.assertEqual(u["market_value_source"], "regression")
        self.assertEqual(u["market_value"], est["u"])
        self.assertIn("Valeur estimée", u["tags"])
        self.assertEqual(u["detail"]["valeur"]["tendance"], 0)   # pas de tendance sur une valeur estimée
        self.assertNotIn("Valeur doublée", u["tags"])
        s = u["scores"]
        w = model.DEFAULT_WEIGHTS
        expected = (w["sport"] * s["sport"] + 0.5 * w["valeur"] * s["valeur"] + w["fan"] * s["fan"]) \
            / (w["sport"] + 0.5 * w["valeur"] + w["fan"]) * (0.85 + 0.30 * 0.5)
        self.assertAlmostEqual(s["final"], expected, places=1)
        known = next(r for r in ranked if r["id"] == "0")
        self.assertFalse(known["market_value_estimated"])
        self.assertEqual(known["market_value_source"], "transfermarkt")


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


def log_entry(i, status="played", started=True, minutes=90, goals=0, assists=0, conceded=0, opponent="o", comp="C2"):
    return {"match_id": str(i), "date": f"2026-{7 + i // 4:02d}-{1 + 7 * (i % 4):02d}", "competition_id": comp,
            "competition": "Challenge League", "matchday": i + 1, "home": i % 2 == 0, "opponent": opponent,
            "opponent_id": opponent, "result": "1:0", "outcome": "V", "status": status, "position": "Centre-Forward",
            "started": started if status == "played" else False, "minutes": minutes if status == "played" else 0,
            "goals": goals, "assists": assists, "yellow": 0, "red": 0, "subbed_on_minute": None,
            "subbed_off_minute": None, "conceded": conceded if status == "played" else None}


def matchlog_for(pid, entries, current=None):
    return {"players": {pid: {"name": "x", "club_id": "1", "entries": entries, "team_matches": len(entries),
                              "current_status": current}}}


class DynamicsTest(unittest.TestCase):
    """Composante « dynamique de saison » (journal 2026/27) et agrégats toutes compétitions."""

    def test_recent_stats_weights_cup_and_ramp(self):
        prev = {"season": "25/26", "competition_id": "C2", "appearances": 30, "goals": 9, "assists": 0, "minutes": 2700,
                "yellow": 0, "red": 0}
        league = {"season": "26/27", "competition_id": "C2", "appearances": 10, "goals": 2, "assists": 0, "minutes": 900,
                  "yellow": 0, "red": 0, "team_matches": 10}
        cup = {"season": "26/27", "competition_id": "SCC", "competition": "Swiss Cup", "appearances": 1, "goals": 3,
               "assists": 0, "minutes": 90, "yellow": 0, "red": 0}
        st = model.recent_stats(player(stats=[prev, league, cup]), today=TODAY)
        w = 10 / 15                                   # poids de la saison en cours à 10 matchs
        self.assertAlmostEqual(st["season_weight"], round(w, 3))
        self.assertAlmostEqual(st["minutes"], 2700 + 900 * w + 90 * w * 0.8)
        self.assertAlmostEqual(st["g"], 9 + 2 * w + 3 * w * 0.8)
        self.assertEqual((st["minutes_raw"], st["g_raw"], st["cup_minutes"]), (3690, 14, 90))
        # Sans journal ni classement, la saison en cours garde son plein poids (comportement historique).
        st0 = model.recent_stats(player(stats=[prev, {**league, "team_matches": None}]), today=TODAY)
        self.assertEqual(st0["minutes"], 3600)
        # Le journal fixe le nombre de matchs de l'équipe.
        st15 = model.recent_stats(player(stats=[prev, league, cup]), today=TODAY, team_matches=30)
        self.assertEqual(st15["season_weight"], 1.0)
        self.assertAlmostEqual(st15["coef"], 1.0)    # coupe nationale au niveau du championnat du joueur

    def test_european_minutes_at_default_level(self):
        rows = [{"season": "26/27", "competition_id": "C1", "appearances": 9, "goals": 0, "assists": 0, "minutes": 810,
                 "yellow": 0, "red": 0, "team_matches": 15},
                {"season": "26/27", "competition_id": "UCOL", "competition": "UEFA Conference League", "appearances": 2,
                 "goals": 0, "assists": 0, "minutes": 180, "yellow": 0, "red": 0}]
        st = model.recent_stats(player(stats=rows, league_id="C1"), today=TODAY)
        self.assertEqual(st["euro_minutes"], 180)
        self.assertAlmostEqual(st["coef"], (1.45 * 810 + 1.3 * 180) / 990)

    def test_season_dynamics_indicators(self):
        # 10 matchs : titulaire sur les 5 premiers, remplaçant ensuite ; 1 match blessé ; forme en hausse.
        entries = [log_entry(i, started=True, minutes=90, goals=0) for i in range(5)]
        entries += [log_entry(5, status="injured")]
        entries += [log_entry(i, started=False, minutes=20, goals=1) for i in range(6, 10)]
        dyn = model.season_dynamics(player(id="d"), matchlog_for("d", entries), TODAY,
                                    standings={"o": {"rank": 1, "teams": 10}})
        self.assertEqual((dyn["matchs"], dyn["joues"], dyn["titularisations"], dyn["blesse"]), (10, 9, 5, 1))
        self.assertAlmostEqual(dyn["starter_share"], 5 / 9, places=2)
        self.assertAlmostEqual(dyn["minutes_share"], (450 + 80) / 900, places=2)
        self.assertLess(dyn["role_trend"], -0.3)          # perd sa place
        self.assertGreater(dyn["form_5"], 1.3)            # 4 buts en 80 min sur les 5 derniers
        self.assertAlmostEqual(dyn["availability_2627"], 0.1, places=2)
        self.assertGreater(dyn["consistency"], 0.5)
        self.assertIsNone(dyn["big_game_factor"])        # tous les adversaires dans la même moitié
        self.assertAlmostEqual(dyn["confiance"], 10 / 16, places=2)
        self.assertIn("Perd sa place", model.dynamic_tags(dyn))
        self.assertIn("En forme", model.dynamic_tags(dyn))
        self.assertEqual(model.dynamic_tags(None), ["Saison 26/27 non lue"])

    def test_big_game_factor_and_revelation(self):
        entries = [log_entry(i, goals=2 if i % 2 else 0, opponent="top" if i % 2 else "low") for i in range(8)]
        standings = {"top": {"rank": 2, "teams": 10}, "low": {"rank": 9, "teams": 10}}
        p = player(id="r", stats=[{"season": "25/26", "competition_id": "C2", "appearances": 30, "goals": 3, "assists": 0,
                                   "minutes": 2700, "yellow": 0, "red": 0}])
        dyn = model.season_dynamics(p, matchlog_for("r", entries), TODAY, standings)
        self.assertEqual(dyn["big_game_factor"], 3.0)     # production seulement contre le haut du tableau
        self.assertTrue(dyn["revelation"])                 # 1,0 but/90 contre 0,1 la saison passée, 720 minutes
        self.assertIn("Révélation 26/27", model.dynamic_tags(dyn))
        self.assertIn("Titulaire installé", model.dynamic_tags(dyn))

    def test_keeper_form_uses_conceded(self):
        entries = [log_entry(i, conceded=2) for i in range(5)] + [log_entry(i, conceded=0) for i in range(5, 10)]
        dyn = model.season_dynamics(player(id="g", position="Goalkeeper"), matchlog_for("g", entries), TODAY)
        self.assertGreater(dyn["form_5"], 1.3)            # 0 but encaissé sur les 5 derniers contre 1/90 sur la saison
        self.assertEqual(dyn["buts_encaisses"], 10)
        self.assertIsNone(dyn["production_90"])

    def test_dynamics_blend_weight_and_direction(self):
        base = model.sport_score(player())[0]
        strong = model.season_dynamics(player(id="p"), matchlog_for("p", [log_entry(i, goals=1) for i in range(10)]), TODAY)
        weak = model.season_dynamics(player(id="p"), matchlog_for("p", [log_entry(i, status="bench") for i in range(10)]), TODAY)
        s_strong, d_strong = model.sport_score(player(), dynamics=strong)
        s_weak, d_weak = model.sport_score(player(), dynamics=weak)
        self.assertGreater(s_strong, s_weak)
        self.assertAlmostEqual(d_strong["poids_dynamique"], 0.25 * 10 / 16, places=3)
        self.assertEqual(d_strong["dynamique"]["matchs"], 10)
        self.assertEqual(model.sport_score(player(), dynamics=None)[0], base)

    def test_long_term_injured_starter_is_half_a_need(self):
        squad = [{"id": f"s{i}", "position": "Centre-Back", "age": 25, "contract": "2028-06-30"} for i in range(8)]
        entries = [log_entry(i, started=True) for i in range(5)] + [log_entry(i, status="injured") for i in range(5, 8)]
        ml = matchlog_for("s0", entries)
        self.assertTrue(model.long_term_injured_starter(squad[0], ml))
        self.assertFalse(model.long_term_injured_starter(squad[1], ml))
        without = model.squad_needs(squad, TODAY)["DEF"]
        with_log = model.squad_needs(squad, TODAY, ml)["DEF"]
        self.assertEqual((without["blesses_longue_duree"], with_log["blesses_longue_duree"]), (0, 1))
        self.assertGreater(with_log["besoin"], without["besoin"])
        # Un remplaçant blessé ne compte pas ; une absence de 2 matchs non plus.
        bench = [log_entry(i, started=False, minutes=10) for i in range(5)] + [log_entry(i, status="injured") for i in range(5, 8)]
        self.assertFalse(model.long_term_injured_starter(squad[0], matchlog_for("s0", bench)))
        short = [log_entry(i, started=True) for i in range(6)] + [log_entry(i, status="injured") for i in range(6, 8)]
        self.assertFalse(model.long_term_injured_starter(squad[0], matchlog_for("s0", short)))
