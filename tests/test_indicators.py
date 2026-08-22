"""Indikatoren gegen Handrechnungen pruefen.

Jede erwartete Zahl in dieser Datei ist von Hand ausgerechnet und im
Kommentar hergeleitet. Wer die Formel aendert, muss die Herleitung mit
aendern — genau das ist der Zweck.
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import indicators as ind          # noqa: E402


def bar(o, h, l, c, v=1000, t="2026-01-01T05:00:00Z"):
    return {"o": o, "h": h, "l": l, "c": c, "v": v, "t": t}


def series(closes, highs=None, lows=None):
    """Bars aus Schlusskursen; Hoch/Tief default +-1."""
    highs = highs or [c + 1 for c in closes]
    lows = lows or [c - 1 for c in closes]
    return [bar(c, h, l, c) for c, h, l in zip(closes, highs, lows)]


class TestGrundbausteine(unittest.TestCase):

    def test_sma(self):
        # [1,2,3,4,5], Periode 3: (1+2+3)/3=2, (2+3+4)/3=3, (3+4+5)/3=4
        self.assertEqual(ind.sma([1, 2, 3, 4, 5], 3), [None, None, 2.0, 3.0, 4.0])

    def test_sma_kuerzer_als_periode(self):
        self.assertEqual(ind.sma([1, 2], 5), [None, None])

    def test_ema(self):
        # Periode 3 -> alpha = 2/(3+1) = 0.5, Start = SMA(1,2,3) = 2 bei Index 2
        #   i=3: 4*0.5 + 2*0.5 = 3.0
        #   i=4: 5*0.5 + 3*0.5 = 4.0
        self.assertEqual(ind.ema([1, 2, 3, 4, 5], 3), [None, None, 2.0, 3.0, 4.0])

    def test_stdev(self):
        # [2,4,4,4,5,5,7,9]: Mittel 5, Abweichungen^2 = 9+1+1+1+0+0+4+16 = 32
        # Varianz = 32/8 = 4  ->  sd = 2
        self.assertAlmostEqual(ind.stdev([2, 4, 4, 4, 5, 5, 7, 9], 8)[7], 2.0)


class TestTrueRangeUndATR(unittest.TestCase):

    def setUp(self):
        #      o   h   l   c
        self.bars = [
            bar(9, 10, 8, 9),
            bar(11, 12, 9, 11),
            bar(12, 13, 11, 12),
            bar(14, 15, 12, 14),
            bar(15, 16, 14, 15),
        ]

    def test_true_range(self):
        # i=1: max(12-9=3, |12-9|=3, |9-9|=0)   = 3
        # i=2: max(13-11=2, |13-11|=2, |11-11|=0) = 2
        # i=3: max(15-12=3, |15-12|=3, |12-12|=0) = 3
        # i=4: max(16-14=2, |16-14|=2, |14-14|=0) = 2
        self.assertEqual(ind.true_range(self.bars), [None, 3.0, 2.0, 3.0, 2.0])

    def test_atr_wilder(self):
        # ATR(3): Start = (3+2+3)/3 = 8/3 bei Index 3
        #         i=4: (8/3 * 2 + 2) / 3 = (16/3 + 6/3)/3 = 22/9
        a = ind.atr(self.bars, 3)
        self.assertIsNone(a[2])
        self.assertAlmostEqual(a[3], 8 / 3)
        self.assertAlmostEqual(a[4], 22 / 9)

    def test_atr_zu_wenig_daten(self):
        self.assertTrue(all(v is None for v in ind.atr(self.bars[:2], 14)))


class TestRSI(unittest.TestCase):

    def test_nur_gewinne_ergibt_100(self):
        # Kein einziger Verlust -> durchschnittlicher Verlust 0 -> RSI 100
        bars = series(list(range(1, 40)))
        self.assertEqual(ind.rsi(bars, 14)[-1], 100.0)

    def test_nur_verluste_ergibt_0(self):
        bars = series(list(range(200, 160, -1)))
        self.assertEqual(ind.rsi(bars, 14)[-1], 0.0)

    def test_abwechselnd_konvergiert_auf_wilder_fixpunkt(self):
        # Streng abwechselnd +1 / -1 ergibt unter Wilder-Glaettung NICHT 50.
        # Im Zweierzyklus gilt mit n = 14:
        #     a = (13b + 1)/14        (Schritt mit Gewinn 1)
        #     b = (13a + 0)/14        (Schritt mit Gewinn 0)
        # einsetzen:  a * 27/196 = 1/14  ->  a = 14/27,  b = 13/27
        # Der Verlustdurchschnitt ist spiegelbildlich, also RS = a/b = 14/13:
        #     RSI = 100 - 100/(1 + 14/13) = 100 * (1 - 13/27) = 51.8518...
        closes = [100.0 + (1.0 if i % 2 else 0.0) for i in range(1000)]
        self.assertAlmostEqual(ind.rsi(series(closes), 14)[-1],
                               100 * (1 - 13 / 27), places=7)

    def test_bereich_immer_0_bis_100(self):
        import random
        random.seed(7)
        closes = [100.0]
        for _ in range(200):
            closes.append(max(1.0, closes[-1] * (1 + random.uniform(-0.05, 0.05))))
        for v in ind.rsi(series(closes), 14):
            if v is not None:
                self.assertGreaterEqual(v, 0.0)
                self.assertLessEqual(v, 100.0)


class TestADX(unittest.TestCase):

    def test_starker_aufwaertstrend(self):
        # Sauber steigende Hochs und Tiefs -> +DI deutlich ueber -DI,
        # ADX hoch (Trendstaerke)
        bars = [bar(i, i + 1, i - 1, i) for i in range(10, 80)]
        adx_s, plus_di, minus_di = ind.adx(bars, 14)
        self.assertGreater(plus_di[-1], minus_di[-1])
        self.assertGreater(adx_s[-1], 50.0)

    def test_seitwaerts_hat_schwachen_adx(self):
        closes = [100.0 + (1.0 if i % 2 else -1.0) for i in range(80)]
        adx_s, _, _ = ind.adx(series(closes), 14)
        self.assertLess(adx_s[-1], 30.0)


class TestBollingerUndDonchian(unittest.TestCase):

    def test_bollinger(self):
        # Mittel 5, sd 2 -> oben 5+2*2 = 9, unten 5-2*2 = 1
        bars = series([2, 4, 4, 4, 5, 5, 7, 9])
        mid, up, low = ind.bollinger(bars, 8, 2.0)
        self.assertAlmostEqual(mid[-1], 5.0)
        self.assertAlmostEqual(up[-1], 9.0)
        self.assertAlmostEqual(low[-1], 1.0)

    def test_donchian_schliesst_aktuellen_bar_ein(self):
        bars = series([10, 12, 11, 15, 13])       # Hochs = Close+1
        high, low = ind.donchian(bars, 3)
        self.assertEqual(high[3], 16.0)           # max(13,12,16) ueber i=1..3
        self.assertEqual(high[4], 16.0)           # max(12,16,14) ueber i=2..4
        self.assertEqual(low[4], 10.0)            # min(10,14,12)


class TestRelativeStaerke(unittest.TestCase):

    def test_mehrrendite(self):
        # Aktie +20 %, Vergleich +10 % ueber 5 Tage -> Mehrrendite +10 Punkte
        aktie = series([100, 100, 100, 100, 100, 120])
        index = series([100, 100, 100, 100, 100, 110])
        self.assertAlmostEqual(ind.relative_strength(aktie, index, 5), 0.10)

    def test_zu_kurze_reihe_gibt_none(self):
        self.assertIsNone(ind.relative_strength(series([1, 2]), series([1, 2]), 63))


class TestVolatilitaet(unittest.TestCase):

    def test_konstante_kurse_haben_null_volatilitaet(self):
        self.assertAlmostEqual(ind.realised_volatility(series([100.0] * 40), 20), 0.0)

    def test_bekannte_tagesschwankung(self):
        # Kurse wechseln zwischen *1.01 und /1.01, die Log-Renditen also
        # zwischen +ln(1.01) und -ln(1.01), Mittelwert 0.
        # Ueber 20 Renditen mit Bessel-Korrektur (Nenner n-1 = 19):
        #     Varianz = 20 * ln(1.01)^2 / 19
        #     sd      = ln(1.01) * sqrt(20/19)
        # annualisiert mit sqrt(252).  Der Nenner n-1 ist die Stelle, an der
        # eine naive Handrechnung danebenliegt.
        closes = [100.0]
        for i in range(40):
            closes.append(closes[-1] * (1.01 if i % 2 == 0 else 1 / 1.01))
        vol = ind.realised_volatility(series(closes), 20)
        erwartet = math.log(1.01) * math.sqrt(20 / 19) * math.sqrt(252)
        self.assertAlmostEqual(vol, erwartet, places=12)


class TestSnapshot(unittest.TestCase):

    def test_liefert_alle_schluessel_ohne_ausnahme(self):
        import random
        random.seed(3)
        closes = [100.0]
        for _ in range(300):
            closes.append(max(1.0, closes[-1] * (1 + random.uniform(-0.03, 0.03))))
        bars = series(closes)
        snap = ind.snapshot(bars, benchmark=bars)
        for key in ("close", "atr", "rsi", "adx", "ema21", "sma200",
                    "donchian_high55", "vol_20d", "dollar_volume_20d"):
            self.assertIn(key, snap)
            self.assertIsNotNone(snap[key], f"{key} sollte berechnet sein")

    def test_kurze_historie_wirft_nicht(self):
        snap = ind.snapshot(series([100, 101, 102]))
        self.assertEqual(snap["bars"], 3)
        self.assertIsNone(snap["sma200"])

    def test_leere_bars(self):
        self.assertEqual(ind.snapshot([]), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
