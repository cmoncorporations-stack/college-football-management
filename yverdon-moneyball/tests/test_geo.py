import unittest

from moneyball import geo


class GeoTest(unittest.TestCase):
    def test_same_place_different_spellings(self):
        for variants in (("Genf", "Genève", "Geneva", "Gèneve", "Ginevra"),
                         ("Biel/Bienne", "Biel-Bienne", "Bienne", "Biel"),
                         ("Freiburg", "Fribourg"), ("Sitten", "Sion"), ("Neuenburg", "Neuchâtel"),
                         ("Delsberg", "Delémont"), ("Pruntrut", "Porrentruy"), ("Murten", "Morat"),
                         ("Yverdon", "Yverdon-les-Bains"), ("St. Gallen", "Sankt Gallen", "Saint-Gall")):
            zones = {geo.zone(v, "Switzerland") for v in variants}
            self.assertEqual(len(zones), 1, variants)
            cantons = {geo.canton(v, "Switzerland") for v in variants}
            self.assertEqual(len(cantons), 1, variants)

    def test_zones_and_anchor(self):
        self.assertEqual(geo.zone("Genf", "Switzerland"), ("Romandie", 0.7))
        self.assertEqual(geo.zone("Montagny-près-Yverdon", "Switzerland"), ("Bassin nord-vaudois", 1.0))
        self.assertEqual(geo.zone("Le Mont-sur-Lausanne", "Switzerland"), ("Vaud", 0.85))
        self.assertEqual(geo.zone("Grabs SG", "Switzerland"), ("Suisse", 0.45))
        self.assertEqual(geo.zone("Pontarlier", "France"), ("Frontalier", 0.4))
        self.assertEqual(geo.zone("Paris", "France"), ("International", 0.1))
        self.assertEqual(geo.zone("Freiburg", "Germany"), ("International", 0.1))   # pas Fribourg
        self.assertEqual(geo.zone("Brig-Glis", "Switzerland"), ("Suisse", 0.45))   # Haut-Valais germanophone
        self.assertEqual(geo.zone(None, None, ["Switzerland"]), ("Suisse", 0.45))

    def test_youth_club_rule_same_for_everyone(self):
        self.assertEqual(geo.zone("Zürich", "Switzerland", youth_clubs=["FC Sion"]), ("Romandie", 0.7))
        self.assertEqual(geo.zone("Genève", "Switzerland", youth_clubs=["Yverdon-Sport FC"]), ("Bassin nord-vaudois", 1.0))
        self.assertEqual(geo.zone("Orbe", "Switzerland", youth_clubs=["FC Basel"]), ("Bassin nord-vaudois", 1.0))
        self.assertEqual(geo.zone("Bielefeld", "Germany", youth_clubs=["Arminia Bielefeld"]), ("International", 0.1))
        self.assertEqual(geo.zone("Poitiers", "France", youth_clubs=["ES Buxerolles"]), ("International", 0.1))

    def test_unrecognized_lists_swiss_places_only(self):
        players = [{"birth_city": "Nulle-Part", "birth_country": "Switzerland"},
                   {"birth_city": "Nulle-Part", "birth_country": "Switzerland"},
                   {"birth_city": "Paris", "birth_country": "France"},
                   {"birth_city": "Lausanne", "birth_country": "Switzerland"}]
        self.assertEqual(geo.unrecognized(players), [("Nulle-Part", 2)])


if __name__ == "__main__":
    unittest.main()
