"""Tests du lecteur direct Transfermarkt sur des extraits HTML/JSON représentatifs
(structure relevée sur www.transfermarkt.com le 26.09.2026)."""
import json
import tempfile
import unittest
from pathlib import Path

from moneyball import tm_direct as td

SQUAD_HTML = """
<div id="yw1"><table class="items"><thead><tr>
<th>#</th><th>Player</th><th>Date of birth/Age</th><th>Nat.</th><th>Height</th><th>Foot</th>
<th>Joined</th><th>Signed from</th><th>Contract</th><th>Market value</th></tr></thead><tbody>
<tr class="odd"><td class="zentriert rueckennummer bg_Torwart" title="Goalkeeper"><div class=rn_nummer>1</div></td>
<td class="posrela"><table class="inline-table"><tr><td rowspan="2"><img title="Simon Enzler" /></td>
<td class="hauptlink"><a href="/simon-enzler/profil/spieler/237664"> Simon Enzler </a></td></tr>
<tr><td> Goalkeeper </td></tr></table></td>
<td class="zentriert">16/10/1997 (28)</td>
<td class="zentriert"><img title="Switzerland" class="flaggenrahmen" /><img title="Kosovo" class="flaggenrahmen" /></td>
<td class="zentriert">1,87m</td><td class="zentriert">right</td><td class="zentriert">02/07/2025</td>
<td class="zentriert"><a href="/ac-bellinzona/startseite/verein/2047/saison_id/2025"><img title="AC Bellinzona" /></a></td>
<td class="zentriert">30/06/2027</td><td class="rechts hauptlink"><a href="#">€150k</a></td></tr>
<tr class="even"><td class="zentriert">9</td>
<td class="posrela"><table class="inline-table"><tr><td rowspan="2"></td>
<td class="hauptlink"><a href="/john-doe/profil/spieler/1">John Doe</a></td></tr><tr><td>Centre-Forward</td></tr></table></td>
<td class="zentriert">01/01/2001 (25)</td><td class="zentriert"><img title="France" class="flaggenrahmen" /></td>
<td class="zentriert">-</td><td class="zentriert">-</td><td class="zentriert">-</td><td class="zentriert">-</td>
<td class="zentriert">-</td><td class="rechts hauptlink"><a href="#">€1.20m</a></td></tr>
</tbody></table></div>
"""

STATS_HTML = """
<select name="reldata"><option value="&2026">Total 26/27</option>
<option value="C2&2026" selected="selected">Challenge League 26/27</option>
<option value="SCC&2026">Swiss Cup 26/27</option><option value="C1&2025">Super League 25/26</option></select>
<div id="yw1"><table class="items"><thead><tr><th>#</th><th>Player</th><th>Age</th><th>Nat.</th><th>In squad</th>
<th><span title="Appearances" class="icon"></span></th><th><span title="Goals"></span></th><th><span title="Assists"></span></th>
<th><span title="Yellow cards"></span></th><th><span title="Second yellow cards"></span></th><th><span title="Red cards"></span></th>
<th><span title="Substitutions on"></span></th><th><span title="Substitutions off"></span></th><th>PPG</th><th><span title="Minutes played"></span></th>
</tr></thead><tbody>
<tr class="odd"><td class="zentriert">10</td>
<td class="posrela"><table class="inline-table"><tr><td rowspan="2"></td><td class="hauptlink"><div><span>
<a title="Antonio Marchesano" href="/antonio-marchesano/profil/spieler/150963">Antonio Marchesano</a></span></div></td></tr>
<tr><td>Attacking Midfield</td></tr></table></td>
<td class="zentriert">35</td><td class="zentriert"><img title="Switzerland" /></td><td class="zentriert">40</td>
<td colspan="1" class="zentriert ">40</td><td class="zentriert ">15</td><td class="zentriert ">5</td><td class="zentriert ">2</td>
<td class="zentriert ">-</td><td class="zentriert ">1</td><td class="zentriert ">3</td><td class="zentriert ">12</td>
<td class="zentriert ">1.93</td><td class="rechts ">2.899'</td></tr>
</tbody></table></div>
"""


class FakeClient(td.TransfermarktDirect):
    """Client dont fetch() renvoie des pages figées au lieu d'appeler le réseau."""

    def __init__(self, pages: dict):
        super().__init__(cache_dir=Path(tempfile.mkdtemp()))
        self.pages = pages

    def fetch(self, path, as_json=False):
        body = self.pages[path]
        return json.loads(body) if as_json else body


class ConversionTests(unittest.TestCase):
    def test_money(self):
        self.assertEqual(td.parse_money("€150k"), 150_000)
        self.assertEqual(td.parse_money("€1.20m"), 1_200_000)
        self.assertEqual(td.parse_money("€2.5bn"), 2_500_000_000)
        self.assertIsNone(td.parse_money("-"))
        self.assertIsNone(td.parse_money(None))

    def test_dates(self):
        self.assertEqual(td.parse_date("16/10/1997 (28)"), "1997-10-16")
        self.assertEqual(td.parse_date("Oct 16, 1997"), "1997-10-16")
        self.assertEqual(td.parse_date("2025-07-02"), "2025-07-02")
        self.assertIsNone(td.parse_date("-"))

    def test_misc(self):
        self.assertEqual(td.parse_int("2.340'"), 2340)
        self.assertEqual(td.parse_int("-"), 0)
        self.assertEqual(td.parse_height("1,87 m"), 187)
        self.assertEqual(td.id_from_href("/simon-enzler/profil/spieler/237664"), "237664")
        self.assertEqual(td.slug_from_href("/simon-enzler/profil/spieler/237664"), "simon-enzler")


class SquadTests(unittest.TestCase):
    def test_club_players(self):
        client = FakeClient({"/-/kader/verein/322/saison_id/2026/plus/1": "<h1>Yverdon Sport FC</h1>" + SQUAD_HTML})
        rows = client.club_players("322", 2026)
        self.assertEqual(len(rows), 2)
        enzler = rows[0]
        self.assertEqual(enzler["id"], "237664")
        self.assertEqual(enzler["name"], "Simon Enzler")
        self.assertEqual(enzler["position"], "Goalkeeper")
        self.assertEqual(enzler["date_of_birth"], "1997-10-16")
        self.assertEqual(enzler["age"], 28)
        self.assertEqual(enzler["nationality"], ["Switzerland", "Kosovo"])
        self.assertEqual(enzler["height"], 187)
        self.assertEqual(enzler["joined_on"], "2025-07-02")
        self.assertEqual(enzler["signed_from"], "AC Bellinzona")
        self.assertEqual(enzler["signed_from_id"], "2047")
        self.assertEqual(enzler["contract"], "2027-06-30")
        self.assertEqual(enzler["market_value"], 150_000)
        doe = rows[1]
        self.assertEqual(doe["position"], "Centre-Forward")
        self.assertIsNone(doe["contract"])
        self.assertIsNone(doe["height"])
        self.assertEqual(doe["market_value"], 1_200_000)


class StatsTests(unittest.TestCase):
    def test_club_stats_selected_competition(self):
        client = FakeClient({"/-/leistungsdaten/verein/322/plus/1?reldata=C2%262026": STATS_HTML})
        block = client.club_stats("322", "C2", 2026)
        self.assertEqual(block["competition_name"], "Challenge League")
        self.assertEqual(len(block["rows"]), 1)
        row = block["rows"][0]
        self.assertEqual(row["id"], "150963")
        self.assertEqual(row["appearances"], 40)
        self.assertEqual(row["goals"], 15)
        self.assertEqual(row["assists"], 5)
        self.assertEqual(row["yellow_cards"], 2)
        self.assertEqual(row["red_cards"], 1)          # rouge direct + second avertissement
        self.assertEqual(row["minutes_played"], 2899)
        self.assertEqual(row["position"], "Attacking Midfield")
        self.assertIn({"code": "C1", "season": "2025", "label": "Super League 25/26"}, block["options"])

    def test_club_stats_wrong_competition_returns_no_rows(self):
        # La page sert la compétition sélectionnée (C2 26/27) : demander C1 25/26 ne doit pas la confondre.
        client = FakeClient({"/-/leistungsdaten/verein/322/plus/1?reldata=C1%262025": STATS_HTML})
        block = client.club_stats("322", "C1", 2025)
        self.assertEqual(block["rows"], [])
        self.assertEqual(len(block["options"]), 4)


class JsonEndpointTests(unittest.TestCase):
    def test_market_value_and_transfers(self):
        mv = {"list": [{"x": 1, "y": 25000, "mw": "€25k", "datum_mw": "04/01/2016", "verein": "FC Luzern U21", "age": "18",
                        "wappen": "https://img/wappen/profil/5494.png"}], "current": "€150k"}
        tr = {"transfers": [{"url": "/x/transfers/spieler/237664/transfer_id/5746020", "date": "02/07/2025",
                             "dateUnformatted": "2025-07-02", "upcoming": False, "season": "25/26", "marketValue": "€300k",
                             "fee": "free transfer", "from": {"href": "/ac-bellinzona/transfers/verein/2047/saison_id/2025",
                                                              "clubName": "AC Bellinzona"},
                             "to": {"href": "/yverdon-sport/transfers/verein/322/saison_id/2025", "clubName": "Yverdon Sport"}}]}
        client = FakeClient({"/ceapi/marketValueDevelopment/graph/237664": json.dumps(mv),
                             "/ceapi/transferHistory/list/237664": json.dumps(tr)})
        m = client.player_market_value("237664")
        self.assertEqual(m["market_value"], 150_000)
        self.assertEqual(m["marketValueHistory"][0]["date"], "2016-01-04")
        self.assertEqual(m["marketValueHistory"][0]["market_value"], 25_000)
        t = client.player_transfers("237664")["transfers"][0]
        self.assertEqual(t["id"], "5746020")
        self.assertEqual(t["club_from"], {"id": "2047", "name": "AC Bellinzona"})
        self.assertEqual(t["club_to"]["id"], "322")
        self.assertEqual(t["date"], "2025-07-02")
        self.assertEqual(t["fee"], 0)
        self.assertEqual(t["market_value"], 300_000)


if __name__ == "__main__":
    unittest.main()
