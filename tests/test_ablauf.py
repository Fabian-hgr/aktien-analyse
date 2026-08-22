"""Der ganze Weg an kuenstlichen Kursen: Analyse -> Auftrag -> Ausstieg -> Lernen.

Die Einzelteile haben ihre eigenen Tests. Hier geht es um die Verdrahtung:
Kommt aus einer Kursreihe wirklich ein Auftrag, wird er am Folgetag gefuellt,
loest der Stop aus, landet der Trade mit seiner Messlatte in der Lernschleife?

Ohne Netz, ohne Sprachmodell, mit von Hand gebauten Bars. Dieser Test ist die
Zusicherung, dass die Cloud-Laeufe nicht an einem vergessenen Feldnamen
scheitern — ein Fehler, den kein Modultest findet.
"""
from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import (analysis, calibration, config, learning,      # noqa: E402
                 portfolio, run_settle, targets)

START = dt.date(2026, 1, 5)


def handelstage(n: int) -> list[dt.date]:
    tage, tag = [], START
    while len(tage) < n:
        if tag.weekday() < 5:
            tage.append(tag)
        tag += dt.timedelta(days=1)
    return tage


def reihe(tage: list[dt.date], start: float, steigung: float,
          spanne: float = 0.02) -> list[dict]:
    """Gleichmaessig steigende Kursreihe mit fester Tagesspanne."""
    bars = []
    kurs = start
    for tag in tage:
        o = kurs
        c = kurs * (1 + steigung)
        h = max(o, c) * (1 + spanne / 2)
        l = min(o, c) * (1 - spanne / 2)
        bars.append({"t": tag.isoformat() + "T05:00:00Z", "o": round(o, 4),
                     "h": round(h, 4), "l": round(l, 4), "c": round(c, 4),
                     "v": 5_000_000})
        kurs = c
    return bars


def universum(symbole: dict[str, str]) -> dict:
    return {
        "built_at": START.isoformat(),
        "count": len(symbole),
        "symbols": [{"symbol": s, "name": s, "sector": b, "source": "test"}
                    for s, b in symbole.items()],
    }


class TestGanzerAblauf(unittest.TestCase):

    def setUp(self):
        self.tage = handelstage(160)
        self.bars = {
            "STARK": reihe(self.tage, 50.0, 0.004),
            "MITTEL": reihe(self.tage, 80.0, 0.001),
            "SCHWACH": reihe(self.tage, 30.0, -0.002),
            config.BENCHMARK: reihe(self.tage, 400.0, 0.0008),
        }
        self.u = universum({"STARK": "Technologie", "MITTEL": "Industrie",
                            "SCHWACH": "Energie"})

    def _analyse(self, bis: int, cal=None):
        teil = {s: b[:bis] for s, b in self.bars.items()}
        return analysis.run(
            self.u, teil, teil[config.BENCHMARK], self.tage[bis - 1],
            weights={"trend": 0.5, "setup": 0.3, "volumen": 0.2},
            fundamentals_budget=0,
        )

    def test_analyse_liefert_ideen_mit_ziel_und_stop(self):
        res = self._analyse(140)
        self.assertGreater(len(res["kandidaten"]), 0,
                           "kein einziger handelbarer Titel")
        for e in res["kandidaten"]:
            tg = e["targets"]
            self.assertIsNotNone(tg["target"])
            self.assertIsNotNone(tg["stop"])
            self.assertGreater(tg["target"], tg["price"])
            self.assertLess(tg["stop"], tg["price"])

    def test_der_staerkste_titel_steht_vorn(self):
        res = self._analyse(140)
        self.assertEqual(res["kandidaten"][0]["symbol"], "STARK")

    def test_auftrag_wird_erst_am_folgetag_gefuellt(self):
        """Kein Blick in die Zukunft: der Einstieg ist die naechste Eroeffnung."""
        res = self._analyse(140)
        pf = portfolio.new_portfolio("ki")
        pick = res["kandidaten"][0]
        auftrag = {"symbol": pick["symbol"], "sector": pick["sector"],
                   "target": pick["targets"]["target"],
                   "stop": pick["targets"]["stop"]}
        portfolio.place_orders(pf, [auftrag], self.tage[139])
        self.assertEqual(len(pf["pending"]), 1)
        self.assertEqual(len(pf["positions"]), 0)

        morgen = self.tage[140]
        bar = self.bars["STARK"][140]
        portfolio.settle_day(pf, {"STARK": bar}, morgen)
        self.assertEqual(len(pf["positions"]), 1)
        pos = pf["positions"][0]
        # Eroeffnungskurs plus Schlupf
        erwartet = bar["o"] * (1 + config.SLIPPAGE_BPS / 10_000)
        self.assertAlmostEqual(pos["entry_price"], round(erwartet, 4), places=3)

    def test_stop_schliesst_die_position_mit_minus_einem_r(self):
        pf = portfolio.new_portfolio("ki")
        portfolio.place_orders(pf, [{"symbol": "STARK", "sector": "Technologie",
                                     "target": 100.0, "stop": 40.0}],
                               self.tage[139])
        bar = dict(self.bars["STARK"][140])
        portfolio.settle_day(pf, {"STARK": bar}, self.tage[140])
        einstieg = pf["positions"][0]["entry_price"]

        # Am naechsten Tag faellt der Kurs unter den Stop
        pf["positions"][0]["stop"] = round(einstieg * 0.95, 4)
        absturz = dict(bar, o=einstieg, h=einstieg,
                       l=round(einstieg * 0.90, 4), c=round(einstieg * 0.90, 4))
        portfolio.settle_day(pf, {"STARK": absturz}, self.tage[141])

        self.assertEqual(len(pf["closed"]), 1)
        t = pf["closed"][0]
        self.assertEqual(t["exit_reason"], "stop")
        self.assertAlmostEqual(t["r_multiple"], -1.0, delta=0.05)

    def test_ziel_schliesst_mit_positivem_r(self):
        pf = portfolio.new_portfolio("ki")
        portfolio.place_orders(pf, [{"symbol": "STARK", "sector": "Technologie",
                                     "target": 100.0, "stop": 40.0}],
                               self.tage[139])
        bar = dict(self.bars["STARK"][140])
        portfolio.settle_day(pf, {"STARK": bar}, self.tage[140])
        einstieg = pf["positions"][0]["entry_price"]
        pf["positions"][0]["stop"] = round(einstieg * 0.95, 4)
        pf["positions"][0]["target"] = round(einstieg * 1.10, 4)

        hoch = dict(bar, o=einstieg, h=round(einstieg * 1.15, 4),
                    l=einstieg, c=round(einstieg * 1.12, 4))
        portfolio.settle_day(pf, {"STARK": hoch}, self.tage[141])
        t = pf["closed"][0]
        self.assertIn(t["exit_reason"], ("ziel", "ziel_luecke"))
        self.assertGreater(t["r_multiple"], 0)

    def test_zeitausstieg_nach_der_hoechstdauer(self):
        pf = portfolio.new_portfolio("ki")
        portfolio.place_orders(pf, [{"symbol": "MITTEL", "sector": "Industrie",
                                     "target": 1e6, "stop": 1.0}],
                               self.tage[100])
        for i in range(101, 101 + config.MAX_HOLD_DAYS + 2):
            portfolio.settle_day(pf, {"MITTEL": self.bars["MITTEL"][i]},
                                 self.tage[i])
        self.assertEqual(len(pf["closed"]), 1)
        self.assertEqual(pf["closed"][0]["exit_reason"], "zeit")
        self.assertLessEqual(pf["closed"][0]["days_held"],
                             config.MAX_HOLD_DAYS + 1)

    def test_messlatte_landet_im_trade(self):
        """Ohne basis_p_ziel kann die Lernschleife spaeter nichts beurteilen."""
        pf = portfolio.new_portfolio("ki")
        portfolio.place_orders(pf, [{
            "symbol": "STARK", "sector": "Technologie",
            "target": 100.0, "stop": 40.0, "atr_at_entry": 1.5,
            "ziel_atr": 2.0, "stop_atr": 1.5, "basis_p_ziel": 0.43,
            "basis_erwartung_r": 0.09}], self.tage[139])
        portfolio.settle_day(pf, {"STARK": self.bars["STARK"][140]},
                             self.tage[140])
        pf["positions"][0]["stop"] = 1e6         # sofort ausstoppen
        portfolio.settle_day(pf, {"STARK": self.bars["STARK"][141]},
                             self.tage[141])
        t = pf["closed"][0]
        for feld in ("basis_p_ziel", "basis_erwartung_r", "ziel_atr",
                     "stop_atr", "atr_at_entry"):
            self.assertIsNotNone(t.get(feld), feld)

    def test_beide_depots_kaufen_gleich_viel(self):
        """Sonst vergleicht das Spiel Kapitaleinsatz statt Auswahl."""
        res = self._analyse(140)
        pool = [{"symbol": e["symbol"], "sector": e["sector"],
                 "target": e["targets"]["target"], "stop": e["targets"]["stop"]}
                for e in res["kandidaten"]]
        ki = analysis.select_picks(pool, n=2, belegt=set())
        zufall = portfolio.random_picks(pool, 2, self.tage[139])
        self.assertEqual(len(ki), len(zufall))

    def test_belegte_titel_werden_uebersprungen_nicht_verworfen(self):
        pool = [{"symbol": s, "sector": "Technologie", "target": 10.0,
                 "stop": 5.0} for s in ("A", "B", "C", "D")]
        picks = analysis.select_picks(pool, n=2, max_per_sector=99,
                                      belegt={"A"})
        self.assertEqual([p["symbol"] for p in picks], ["B", "C"])

    def test_lernschleife_laeuft_am_ende_durch(self):
        gewichte = learning.default_weights()
        trades = []
        for i in range(60):
            trades.append({
                "depot": "ki", "sector": "Technologie",
                "r_multiple": 1.5 if i % 2 else -1.0,
                "exit_reason": "ziel" if i % 2 else "stop",
                "exit_date": self.tage[100 + i % 40].isoformat(),
                "entry_price": 100.0, "atr_at_entry": 5.0, "stop_atr": 1.5,
                "mfe_price": 115.0 if i % 2 else 101.0,
                "basis_p_ziel": 0.43, "basis_erwartung_r": 0.09,
                "score_components": {"trend": 0.9 if i % 2 else 0.2,
                                     "setup": 0.5},
                "target_methods": {"atr": 110.0, "struktur": 118.0},
                "regime_at_entry": {"trend": "aufwaerts", "vix_level": "ruhig"},
            })
        learning.update(gewichte, trades, self.tage[150])
        self.assertEqual(gewichte["trades_seen"], 60)
        self.assertAlmostEqual(sum(gewichte["score_weights"].values()), 1.0,
                               places=3)
        for w in gewichte["target_method_weights"].values():
            self.assertGreaterEqual(w, config.WEIGHT_MIN)
            self.assertLessEqual(w, config.WEIGHT_MAX)

    def test_gelernte_gewichte_wirken_auf_die_naechste_analyse(self):
        """Die Schleife muss sich schliessen: gelernt -> angewandt."""
        eng = targets.build(100.0, {"close": 100.0, "atr": 5.0,
                                    "vol_20d": 0.2,
                                    "donchian_high55": 130.0,
                                    "donchian_high20": 120.0,
                                    "donchian_low20": 90.0},
                            None, k_mult=0.7)
        weit = targets.build(100.0, {"close": 100.0, "atr": 5.0,
                                     "vol_20d": 0.2,
                                     "donchian_high55": 130.0,
                                     "donchian_high20": 120.0,
                                     "donchian_low20": 90.0},
                             None, k_mult=1.4)
        self.assertLess(eng["target"], weit["target"])

    def test_ohne_kalibrierung_bleibt_alles_rechenbar(self):
        """Faellt calibration.json aus, laeuft der Lauf ohne die Zahlen weiter."""
        merk = calibration._zwischenspeicher.get("cal")
        calibration._zwischenspeicher["cal"] = None
        try:
            res = self._analyse(140)
            self.assertGreater(len(res["kandidaten"]), 0)
            self.assertIsNone(res["kandidaten"][0]["targets"]["basisquote"])
        finally:
            calibration._zwischenspeicher["cal"] = merk


class TestVergleichskurve(unittest.TestCase):
    """Der Index muss am selben Tag starten wie die Depots.

    Die Bars reichen 400 Kalendertage zurueck. Ohne Begrenzung begaenne die
    SPY-Kurve ein Jahr vor den Depots und stuende schon am ersten
    Abrechnungstag scheinbar zwanzig Prozent vorn — der Vergleich waere
    genau dort kaputt, wo er zaehlt.
    """

    BARS = [{"t": "2025-06-02T00:00:00Z", "c": 500.0},
            {"t": "2026-08-24T00:00:00Z", "c": 700.0},
            {"t": "2026-08-25T00:00:00Z", "c": 721.0}]

    def test_kurve_beginnt_am_depotstart(self):
        kurve = run_settle.spy_kurve(self.BARS, [], ab="2026-08-24")
        self.assertEqual(len(kurve), 2)
        self.assertEqual(kurve[0]["date"], "2026-08-24")
        self.assertEqual(kurve[0]["return_pct"], 0.0)
        self.assertEqual(kurve[1]["return_pct"], 3.0)

    def test_ohne_grenze_waere_die_kurve_verzerrt(self):
        kurve = run_settle.spy_kurve(self.BARS, [])
        self.assertEqual(kurve[0]["date"], "2025-06-02")
        self.assertGreater(kurve[-1]["return_pct"], 40)

    def test_basis_bleibt_ueber_laeufe_hinweg_stehen(self):
        erste = run_settle.spy_kurve(self.BARS, [], ab="2026-08-24")
        zweite = run_settle.spy_kurve(self.BARS, erste, ab="2026-08-24")
        self.assertEqual(zweite[0]["basis"], erste[0]["basis"])
        self.assertEqual(zweite[-1]["return_pct"], 3.0)

    def test_keine_bars_im_fenster_laesst_die_alte_kurve_stehen(self):
        erste = run_settle.spy_kurve(self.BARS, [], ab="2026-08-24")
        gleich = run_settle.spy_kurve(self.BARS, erste, ab="2027-01-01")
        self.assertEqual(gleich, erste)


if __name__ == "__main__":
    unittest.main(verbosity=2)
