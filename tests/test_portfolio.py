"""Depot-Mechanik pruefen — hier entscheidet sich, ob die Messung ehrlich ist.

Die wichtigsten Tests sind die, die Schoenrechnen verhindern:
  - kein Blick in die Zukunft beim Einstieg
  - Kursluecken zaehlen zum Eroeffnungskurs, nicht zur Marke
  - bei doppelter Beruehrung gewinnt der Stop
"""
from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, portfolio                  # noqa: E402

TAG1 = dt.date(2026, 8, 20)
TAG2 = dt.date(2026, 8, 21)
TAG3 = dt.date(2026, 8, 24)


def bar(o, h, l, c, v=1_000_000):
    return {"o": o, "h": h, "l": l, "c": c, "v": v}


def pick(symbol="AAPL", target=110.0, stop=95.0, **kw):
    return {"symbol": symbol, "name": symbol, "sector": "Technologie",
            "target": target, "stop": stop, "score": 0.8, **kw}


def frisch():
    return portfolio.new_portfolio("ki")


class TestAuftraege(unittest.TestCase):

    def test_auftrag_fuellt_erst_am_naechsten_tag(self):
        """Kein Blick in die Zukunft: heute bestellt, morgen gekauft."""
        pf = frisch()
        portfolio.place_orders(pf, [pick()], TAG1)
        self.assertEqual(len(pf["pending"]), 1)
        self.assertEqual(len(pf["positions"]), 0)

        portfolio.settle_day(pf, {"AAPL": bar(100, 101, 99, 100)}, TAG2)
        self.assertEqual(len(pf["positions"]), 1)
        self.assertEqual(pf["positions"][0]["entry_date"], TAG2.isoformat())

    def test_einstiegskurs_enthaelt_schlupf(self):
        # Eroeffnung 100, Schlupf 5 Basispunkte -> 100 * 1.0005 = 100.05
        pf = frisch()
        portfolio.place_orders(pf, [pick()], TAG1)
        portfolio.settle_day(pf, {"AAPL": bar(100, 101, 99, 100)}, TAG2)
        self.assertAlmostEqual(pf["positions"][0]["entry_price"], 100.05, places=4)

    def test_positionsgroesse_und_barmittel(self):
        """Gegenwert je Position ist POSITION_PCT des Startkapitals.

        Bewusst aus der Konfiguration gerechnet und nicht fest verdrahtet:
        die Positionsgroesse haengt an PICKS_PER_DAY und wird mitgezogen,
        wenn die Anzahl Kaeufe sich aendert. Ein Test mit eingebauter Zahl
        wuerde dann eine richtige Aenderung als Fehler melden.
        """
        einsatz = config.START_CAPITAL * config.POSITION_PCT
        pf = frisch()
        portfolio.place_orders(pf, [pick()], TAG1)
        portfolio.settle_day(pf, {"AAPL": bar(100, 101, 99, 100)}, TAG2)
        self.assertAlmostEqual(pf["cash"], config.START_CAPITAL - einsatz,
                               places=2)
        self.assertAlmostEqual(
            pf["positions"][0]["shares"], einsatz / 100.05, places=4)

    def test_kein_doppelkauf_desselben_titels(self):
        pf = frisch()
        portfolio.place_orders(pf, [pick()], TAG1)
        portfolio.settle_day(pf, {"AAPL": bar(100, 101, 99, 100)}, TAG2)
        portfolio.place_orders(pf, [pick()], TAG2)
        self.assertEqual(len(pf["pending"]), 0)
        self.assertTrue(any("bereits offen" in s["reason"] for s in pf["skipped"]))

    def test_auftrag_ohne_ziel_wird_verworfen(self):
        pf = frisch()
        portfolio.place_orders(pf, [pick(target=None)], TAG1)
        self.assertEqual(len(pf["pending"]), 0)

    def test_obergrenze_offener_positionen(self):
        pf = frisch()
        pf["positions"] = [{"symbol": f"X{i}", "shares": 1.0,
                            "entry_price": 1.0, "stop": 0.5, "target": 2.0,
                            "days_held": 0}
                           for i in range(config.MAX_CONCURRENT_POSITIONS)]
        portfolio.place_orders(pf, [pick()], TAG1)
        portfolio.fill_pending(pf, {"AAPL": bar(100, 101, 99, 100)}, TAG2)
        self.assertTrue(any("Depot voll" in s["reason"] for s in pf["skipped"]))


class TestAusstiege(unittest.TestCase):

    def _mit_position(self):
        pf = frisch()
        portfolio.place_orders(pf, [pick()], TAG1)
        portfolio.settle_day(pf, {"AAPL": bar(100, 101, 99, 100)}, TAG2)
        return pf

    def test_ziel_erreicht(self):
        # Hoch 112 >= Ziel 110 -> Verkauf zu 110 * 0.9995 = 109.945
        pf = self._mit_position()
        portfolio.settle_day(pf, {"AAPL": bar(105, 112, 104, 111)}, TAG3)
        t = pf["closed"][0]
        self.assertEqual(t["exit_reason"], "ziel")
        self.assertAlmostEqual(t["exit_price"], 109.945, places=3)

    def test_stop_erreicht(self):
        # Tief 94 <= Stop 95 -> Verkauf zu 95 * 0.9995 = 94.9525
        pf = self._mit_position()
        portfolio.settle_day(pf, {"AAPL": bar(99, 100, 94, 96)}, TAG3)
        t = pf["closed"][0]
        self.assertEqual(t["exit_reason"], "stop")
        self.assertAlmostEqual(t["exit_price"], 94.9525, places=3)

    def test_beide_beruehrt_stop_gewinnt(self):
        """Tagesbars koennen die Reihenfolge nicht aufloesen — konservativ."""
        pf = self._mit_position()
        portfolio.settle_day(pf, {"AAPL": bar(100, 115, 90, 105)}, TAG3)
        t = pf["closed"][0]
        self.assertEqual(t["exit_reason"], "stop_und_ziel")
        self.assertLess(t["r_multiple"], 0)

    def test_luecke_unter_den_stop_zaehlt_zur_eroeffnung(self):
        """Nicht zur Stop-Marke — sonst waeren Ausreisser wegretuschiert."""
        pf = self._mit_position()
        portfolio.settle_day(pf, {"AAPL": bar(90, 92, 88, 91)}, TAG3)
        t = pf["closed"][0]
        self.assertEqual(t["exit_reason"], "stop_luecke")
        self.assertAlmostEqual(t["exit_price"], 90 * 0.9995, places=4)
        # Schlechter als -1R, weil die Luecke unter dem Stop lag
        self.assertLess(t["r_multiple"], -1.0)

    def test_luecke_ueber_das_ziel_zaehlt_zur_eroeffnung(self):
        pf = self._mit_position()
        portfolio.settle_day(pf, {"AAPL": bar(120, 125, 119, 124)}, TAG3)
        t = pf["closed"][0]
        self.assertEqual(t["exit_reason"], "ziel_luecke")
        self.assertAlmostEqual(t["exit_price"], 120 * 0.9995, places=4)
        self.assertGreater(t["r_multiple"], 2.0)

    def test_zeitausstieg(self):
        pf = self._mit_position()
        tag = TAG2
        for _ in range(config.MAX_HOLD_DAYS):
            tag += dt.timedelta(days=1)
            portfolio.settle_day(pf, {"AAPL": bar(100, 101, 99, 100)}, tag)
        self.assertEqual(len(pf["closed"]), 1)
        self.assertEqual(pf["closed"][0]["exit_reason"], "zeit")

    def test_ausstieg_am_einstiegstag_moeglich(self):
        """Eroeffnung, dann Absturz — das muss am selben Tag ausstoppen."""
        pf = frisch()
        portfolio.place_orders(pf, [pick()], TAG1)
        portfolio.settle_day(pf, {"AAPL": bar(100, 100, 90, 91)}, TAG2)
        self.assertEqual(len(pf["closed"]), 1)
        self.assertEqual(pf["closed"][0]["exit_reason"], "stop")

    def test_r_multiple_rechnung(self):
        # Einstieg 100.05, Stop 95 -> Risiko 5.05
        # Ausstieg 109.945 -> R = 9.895 / 5.05 = 1.9594...
        pf = self._mit_position()
        portfolio.settle_day(pf, {"AAPL": bar(105, 112, 104, 111)}, TAG3)
        t = pf["closed"][0]
        self.assertAlmostEqual(t["r_multiple"], (109.945 - 100.05) / 5.05,
                               places=3)


class TestBewertung(unittest.TestCase):

    def test_equity_ist_bar_plus_positionen(self):
        pf = frisch()
        portfolio.place_orders(pf, [pick()], TAG1)
        portfolio.settle_day(pf, {"AAPL": bar(100, 101, 99, 100)}, TAG2)
        einsatz = config.START_CAPITAL * config.POSITION_PCT
        erwartet = (config.START_CAPITAL - einsatz) + (einsatz / 100.05) * 100
        self.assertAlmostEqual(
            portfolio.equity_value(pf, {"AAPL": bar(100, 101, 99, 100)}),
            erwartet, places=2)

    def test_schlupf_kostet_beim_kauf(self):
        pf = frisch()
        portfolio.place_orders(pf, [pick()], TAG1)
        p = portfolio.settle_day(pf, {"AAPL": bar(100, 101, 99, 100)}, TAG2)
        self.assertLess(p["equity"]["equity"], 100_000.0)

    def test_equity_kurve_waechst_pro_tag(self):
        pf = frisch()
        portfolio.settle_day(pf, {}, TAG1)
        portfolio.settle_day(pf, {}, TAG2)
        self.assertEqual(len(pf["equity_curve"]), 2)
        self.assertEqual(pf["equity_curve"][0]["equity"], 100_000.0)


class TestZufallsauswahl(unittest.TestCase):

    def test_reproduzierbar(self):
        kandidaten = [pick(f"SYM{i}") for i in range(50)]
        a = portfolio.random_picks(kandidaten, 3, TAG1)
        b = portfolio.random_picks(kandidaten, 3, TAG1)
        self.assertEqual([x["symbol"] for x in a], [x["symbol"] for x in b])

    def test_anderer_tag_andere_auswahl(self):
        kandidaten = [pick(f"SYM{i}") for i in range(50)]
        a = portfolio.random_picks(kandidaten, 3, TAG1)
        b = portfolio.random_picks(kandidaten, 3, TAG2)
        self.assertNotEqual([x["symbol"] for x in a], [x["symbol"] for x in b])

    def test_titel_ohne_ziel_kommen_nicht_vor(self):
        kandidaten = [pick("GUT")] + [pick(f"X{i}", target=None) for i in range(20)]
        gewaehlt = portfolio.random_picks(kandidaten, 3, TAG1)
        self.assertEqual([x["symbol"] for x in gewaehlt], ["GUT"])


class TestKennzahlen(unittest.TestCase):

    def test_leeres_depot(self):
        s = portfolio.statistics(frisch())
        self.assertEqual(s["trades"], 0)
        self.assertIsNone(s["win_rate"])
        self.assertEqual(s["return_pct"], 0.0)

    def test_trefferquote_und_profitfaktor(self):
        pf = frisch()
        pf["closed"] = [
            {"pnl": 200.0, "r_multiple": 2.0, "days_held": 5, "exit_reason": "ziel"},
            {"pnl": 200.0, "r_multiple": 2.0, "days_held": 5, "exit_reason": "ziel"},
            {"pnl": -100.0, "r_multiple": -1.0, "days_held": 3, "exit_reason": "stop"},
            {"pnl": -100.0, "r_multiple": -1.0, "days_held": 3, "exit_reason": "stop"},
        ]
        pf["equity_curve"] = [{"date": "2026-08-20", "equity": 100_200.0}]
        s = portfolio.statistics(pf)
        self.assertEqual(s["win_rate"], 50.0)
        self.assertEqual(s["profit_factor"], 2.0)          # 400 / 200
        self.assertAlmostEqual(s["expectancy_r"], 0.5)     # (2+2-1-1)/4
        self.assertEqual(s["exit_reasons"], {"ziel": 2, "stop": 2})

    def test_maximaler_rueckgang(self):
        pf = frisch()
        pf["equity_curve"] = [
            {"date": "a", "equity": 100_000.0},
            {"date": "b", "equity": 120_000.0},
            {"date": "c", "equity": 90_000.0},     # -25 % vom Hoch
            {"date": "d", "equity": 110_000.0},
        ]
        self.assertAlmostEqual(portfolio.statistics(pf)["max_drawdown_pct"], -25.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
