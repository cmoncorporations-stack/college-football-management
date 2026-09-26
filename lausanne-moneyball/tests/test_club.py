"""Configuration de club, replis de l'ADN fan, import des cockpits, exclusions du vivier."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from moneyball import model, tm_direct
from moneyball.club import CLUB, is_club
from moneyball.fan_dna import derive_fan_dna, extract_payload, from_fbm_payload
from moneyball.scout import owned_by_club
from tests.test_model import fan_weights, player

LS = {"club_needles": ["lausanne-sport", "lausanne sport", "fc lausanne", "team vaud"], "club_exclude": ["ouchy"]}


class ClubConfigTest(unittest.TestCase):
    def test_needles_and_exclusions(self):
        self.assertTrue(is_club("FC Lausanne-Sport U21", LS))
        self.assertTrue(is_club("Team Vaud U17", LS))
        self.assertFalse(is_club("FC Stade Lausanne-Ouchy", LS))
        self.assertFalse(is_club("Stade Lausanne Ouchy U21", LS))
        self.assertFalse(is_club("Servette FC", LS))
        self.assertTrue(is_club(CLUB["name"]))

    def test_home_league_is_reference(self):
        self.assertEqual(model.league_coef(model.HOME_LEAGUE), 1.0)
        # La renormalisation garde les écarts entre ligues : Super League = 1,45 × Challenge League.
        self.assertAlmostEqual(model.league_coef("C1") / model.league_coef("C2"), 1.45)
        self.assertEqual(model.SQUAD_TARGET, CLUB["squad_target"])

    def test_local_tag_needs_birth_in_bassin_or_canton(self):
        formed_abroad = player(id="a", birth_city="Porto", birth_country="Portugal", citizenship=["Portugal"],
                               youth_clubs=[CLUB["name"]])
        canton_born = player(id="b", birth_city=CLUB["canton"][0].title())
        fa, fb = model.fan_components(formed_abroad), model.fan_components(canton_born)
        self.assertEqual((fa["ancrage"], fa["_origine"]), (1.0, model.ORIGINE_CLUB))
        self.assertEqual(fb["_origine"], model.ORIGINE_CANTON)
        ranked = {r["id"]: r for r in model.score_pool([formed_abroad, canton_born], fan_weights(), {}, date(2026, 9, 25))}
        self.assertNotIn("Enfant du pays", ranked["a"]["tags"])
        self.assertIn("Retour au club", ranked["a"]["tags"])
        self.assertIn("Enfant du pays", ranked["b"]["tags"])


def cockpit(**over):
    base = {"genere": "2026-09-16",
            "penetration": {"indice": 21.4, "bassin": "Canton", "population": 1, "coeur": {"indice": 40.0, "nom": "cœur"}},
            "brevo": {"exploitables": 10, "categories": [{"nom": "New Lead", "pct": 70.0}, {"nom": "Super Fan", "pct": 5.0}]},
            "game": {"engIGclub": 5.0, "audienceTotale": 100},
            "campagnes": [{"nom": "NEWS", "delivered": 500, "ouverture": 40.0, "date": "2026-08-01"},
                          {"nom": "RECRUES_ETE", "delivered": 500, "ouverture": 60.0, "date": "2026-07-01"}],
            "series": {"igM": [["a", 100], ["b", 110]], "igF": []}}
    base.update(over)
    return base


class FanDnaFallbackTest(unittest.TestCase):
    def test_signals_move_weights(self):
        dna = derive_fan_dna(cockpit(), {**CLUB, "recruit_campaign_keywords": ["RECRU"]})
        self.assertGreater(dna["multiplicateurs"]["media"], 1)
        self.assertGreater(dna["multiplicateurs"]["fidelite"], 1)
        self.assertIsNone(dna["signals"]["croissance_ig_feminin_180j"])

    def test_missing_recruit_campaign_falls_back_to_one(self):
        dna = derive_fan_dna(cockpit(campagnes=[{"nom": "NEWS", "delivered": 500, "ouverture": 40.0, "date": "x"}]),
                             {**CLUB, "recruit_campaign_keywords": ["RECRU"]})
        self.assertEqual(dna["multiplicateurs"]["media"], 1.0)
        self.assertIn("multiplicateur 1,0", dna["rationale"]["media"])
        self.assertIsNone(dna["signals"]["ouverture_recrues"])

    def test_uncalibrated_scoring_falls_back_to_one(self):
        c = cockpit()
        c["brevo"] = {"exploitables": 10, "scoringExploitable": False, "score": {"max": 12, "pctScores": 2.5},
                      "categories": [{"nom": "New Lead", "pct": 100.0}]}
        dna = derive_fan_dna(c)
        self.assertEqual(dna["multiplicateurs"]["fidelite"], 1.0)
        self.assertIn("pas encore calibré", dna["rationale"]["fidelite"])
        self.assertFalse(dna["signals"]["scoring_exploitable"])


FBM = {"meta": {"club": "Club X", "genere": "2026-09-16"}, "ovr": 80, "attrs": {"DATA": 60},
       "penetration": {"numerateur": 1000, "baseNumerateur": "contacts exploitables", "fanRate": 0.34, "cible": 24,
                       "canton": {"nom": "Canton X", "pop": 10000, "active": 3400, "indice": 29.4, "niveau": "FORTE"},
                       "agglo": {"nom": "Agglo X", "pop": 5000, "active": 1700, "indice": 58.8, "depasseGardeFou": True},
                       "panel": [{"c": "Autre club", "i": 12.0}]},
       "brevo": {"total": 1500, "exploitables": 1000, "pilotables": 900, "cats": {"NL": 900, "W": 0, "SF": 0},
                 "scoringExploitable": False, "scoreMax": 10, "pctScores": 2.0, "base12m": 800, "croissance12m": 25.0},
       "emailing": {"openRateMoyen": 50.0, "top": [{"d": "2026-07-31", "n": "NL_JOUEURS_A_B", "del": 900, "ouv": 55.0, "cli": 2.0}]},
       "reseauxData": {"IG": {"label": "Instagram", "abonnes": 110, "cible": 150, "serie": [["2026-06-18", 100, 5], ["2026-09-15", 110, 5]]},
                       "TT": {"label": "TikTok", "sansDonnees": True}},
       "posts": {"engIG": 4.2, "ig": [{"d": "2026-07-30", "inter": 10, "reach": 100}]},
       "funnel": [{"k": "Audience sociale", "v": 110}]}


class CockpitImportTest(unittest.TestCase):
    def test_fbm_payload_maps_to_engine_schema(self):
        html = "<script>window.__FBM__=" + json.dumps(FBM) + ";window.__HIST__=[];</script>"
        c = from_fbm_payload(extract_payload(html))
        for key in ("genere", "series", "game", "penetration", "brevo", "croissance12m", "campagnes", "postsM", "postsF"):
            self.assertIn(key, c)
        self.assertNotIn("panel", c["penetration"])                  # indices des autres clubs : confidentiels
        self.assertEqual(c["penetration"]["indice"], 29.4)
        self.assertEqual(c["penetration"]["coeur"]["nom"], "Agglo X")
        self.assertEqual(c["brevo"]["categories"][0], {"nom": "New Lead", "n": 900, "pct": 100.0})
        self.assertEqual(c["game"]["sansDonnees"], ["TikTok"])
        dna = derive_fan_dna(c, {**CLUB, "recruit_campaign_keywords": ["NL_JOUEURS"]})
        self.assertAlmostEqual(dna["multiplicateurs"]["media"], 1.1)   # 55 % contre 50 % de moyenne du cockpit
        self.assertEqual(dna["multiplicateurs"]["fidelite"], 1.0)


class OwnedByClubTest(unittest.TestCase):
    def test_loanee_and_future_signing_are_excluded(self):
        loanee = player(transfers=[{"date": "2019-07-01", "from": "X", "to": CLUB["name"], "fee_label": "€100k"},
                                   {"date": "2026-08-01", "from": CLUB["name"], "to": "Y", "fee_label": "Loan transfer"}])
        signed = player(transfers=[{"date": "2027-07-01", "from": "Y", "to": CLUB["name"], "upcoming": True}])
        former = player(transfers=[{"date": "2024-07-01", "from": CLUB["name"], "to": "Y", "fee_label": "€300k"}])
        self.assertTrue(owned_by_club(loanee))
        self.assertTrue(owned_by_club(signed))
        self.assertFalse(owned_by_club(former))


class _Resp:
    def __init__(self, status, body):
        self.status, self._body = status, body.encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class WafChallengeTest(unittest.TestCase):
    def test_detection(self):
        self.assertTrue(tm_direct.is_waf_challenge(202, ""))
        self.assertTrue(tm_direct.is_waf_challenge(200, "<script>window.gokuProps = {}</script>"))
        self.assertFalse(tm_direct.is_waf_challenge(200, "<html><table></table></html>"))

    def test_challenge_is_never_cached_and_aborts_clearly(self):
        with tempfile.TemporaryDirectory() as d:
            client = tm_direct.TransfermarktDirect(cache_dir=Path(d), min_interval_s=0, retries=1)
            client._opener = mock.Mock(open=mock.Mock(return_value=_Resp(202, "<script>gokuProps</script>")))
            with mock.patch.object(tm_direct.time, "sleep"):
                with self.assertRaises(RuntimeError) as err:
                    client.fetch("/x")
            self.assertIn("défi anti-robot", str(err.exception))
            self.assertEqual(list(Path(d).iterdir()), [])
            self.assertEqual(client.captchas, 2)


if __name__ == "__main__":
    unittest.main()
