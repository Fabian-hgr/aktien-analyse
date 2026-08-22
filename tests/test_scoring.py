"""Scoring gegen Handrechnungen und Grenzfaelle pruefen."""
from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, scoring                     # noqa: E402


def snap(**kw):
    base = {
        "bars": 250, "close": 100.0, "atr": 5.0, "rsi": 55.0, "adx": 30.0,
        "ema9": 99.0, "ema21": 98.0, "ema50": 95.0, "sma200": 90.0,
        "donchian_high55": 112.0, "donchian_high20": 105.0,
        "donchian_low20": 92.0, "vol_20d": 0.25, "rel_strength_63d": 0.05,
        "volume": 1_500_000.0, "avg_volume_20d": 1_000_000.0,
        "dollar_volume_20d": 150_000_000.0,
    }
    base.update(kw)
    return base


FUND_GUT = {
    "profit_margin": 0.30, "return_on_equity": 0.35, "revenue_growth": 0.30,
    "debt_to_equity": 40.0, "forward_pe": 15.0, "peg_ratio": 0.8,
    "target_mean": 130.0, "recommendation": "strong_buy", "analyst_count": 30,
    "beta": 1.0, "next_earnings": None,
}


class TestKomponenten(unittest.TestCase):

    def test_trend_perfekt(self):
        c = scoring.trend(snap(adx=40.0, rel_strength_63d=0.10))
        self.assertAlmostEqual(c.score, 1.0)

    def test_trend_komplett_negativ(self):
        c = scoring.trend(snap(ema9=90.0, ema21=95.0, ema50=99.0,
                               sma200=110.0, adx=0.0, rel_strength_63d=-0.20))
        self.assertAlmostEqual(c.score, 0.0)

    def test_trend_ohne_sma200_nutzt_uebrige_teile(self):
        c = scoring.trend(snap(sma200=None, adx=40.0, rel_strength_63d=0.10))
        self.assertAlmostEqual(c.score, 1.0)
        self.assertTrue(c.available)

    def test_setup_ueberdehnt_wird_abgewertet(self):
        # Kurs 4 ATR ueber der EMA21 -> ueberdehnt
        eng = scoring.setup(snap(close=100.0, ema21=80.0, atr=5.0))
        normal = scoring.setup(snap(close=100.0, ema21=98.0, atr=5.0))
        self.assertLess(eng.score, normal.score)

    def test_setup_rsi_ueberkauft(self):
        hoch = scoring.setup(snap(rsi=85.0))
        mittel = scoring.setup(snap(rsi=55.0))
        self.assertLess(hoch.score, mittel.score)

    def test_volumen_rampe(self):
        # ratio 1.8 und darueber -> 1.0 ; 0.7 und darunter -> 0.0
        self.assertAlmostEqual(
            scoring.volume(snap(volume=1_800_000.0)).score, 1.0)
        self.assertAlmostEqual(
            scoring.volume(snap(volume=700_000.0)).score, 0.0)
        # Mitte: (1.25 - 0.7) / (1.8 - 0.7) = 0.5
        self.assertAlmostEqual(
            scoring.volume(snap(volume=1_250_000.0)).score, 0.5)

    def test_qualitaet_gewichteter_mittelwert(self):
        # Marge 0.25 -> 1.0 (Gewicht .30) | ROE 0.30 -> 1.0 (.25)
        # Wachstum 0.25 -> 1.0 (.25)      | Schulden 0 -> 1.0 (.20)
        c = scoring.quality({"profit_margin": 0.25, "return_on_equity": 0.30,
                             "revenue_growth": 0.25, "debt_to_equity": 0.0})
        self.assertAlmostEqual(c.score, 1.0)

    def test_qualitaet_ohne_daten_nicht_verfuegbar(self):
        self.assertFalse(scoring.quality(None).available)
        self.assertFalse(scoring.quality({}).available)

    def test_bewertung_guenstiger_ist_besser(self):
        billig = scoring.valuation({"forward_pe": 10.0}, 20.0)
        teuer = scoring.valuation({"forward_pe": 30.0}, 20.0)
        self.assertGreater(billig.score, teuer.score)

    def test_analysten_upside_rampe(self):
        # +30 % Abstand -> 1.0 auf der Rampe (-5 % bis +30 %)
        c = scoring.analysts({"target_mean": 130.0}, 100.0)
        self.assertAlmostEqual(c.score, 1.0)

    def test_analysten_negativer_abstand(self):
        c = scoring.analysts({"target_mean": 90.0, "recommendation": "sell",
                              "analyst_count": 3}, 100.0)
        self.assertAlmostEqual(c.score, 0.0)

    def test_sentiment_abbildung(self):
        self.assertAlmostEqual(scoring.sentiment({"sentiment": 1.0}).score, 1.0)
        self.assertAlmostEqual(scoring.sentiment({"sentiment": 0.0}).score, 0.5)
        self.assertAlmostEqual(scoring.sentiment({"sentiment": -1.0}).score, 0.0)
        self.assertFalse(scoring.sentiment(None).available)


class TestAusschluesseUndAbzuege(unittest.TestCase):

    def test_billige_aktie_ausgeschlossen(self):
        gruende = scoring.hard_exclusions(snap(close=3.0), {})
        self.assertTrue(any("unter" in g for g in gruende))

    def test_illiquide_aktie_ausgeschlossen(self):
        gruende = scoring.hard_exclusions(snap(dollar_volume_20d=500_000.0), {})
        self.assertTrue(any("Dollarvolumen" in g for g in gruende))

    def test_kurze_historie_ausgeschlossen(self):
        self.assertTrue(scoring.hard_exclusions(snap(bars=20), {}))

    def test_sauberer_titel_ohne_ausschluss(self):
        self.assertEqual(scoring.hard_exclusions(snap(), {}), [])

    def test_earnings_abzug(self):
        heute = dt.date(2026, 8, 19)
        # 4 Kalendertage voraus -> rund 3 Handelstage -> innerhalb der Sperre
        total, gruende = scoring.penalties(
            {"next_earnings": "2026-08-23"}, heute)
        self.assertAlmostEqual(total, config.PENALTY_EARNINGS_SOON)
        self.assertTrue(gruende)

    def test_kein_earnings_abzug_wenn_weit_weg(self):
        total, _ = scoring.penalties({"next_earnings": "2026-11-01"},
                                     dt.date(2026, 8, 19))
        self.assertEqual(total, 0.0)

    def test_beta_abzug(self):
        total, _ = scoring.penalties({"beta": 2.5}, dt.date(2026, 8, 19))
        self.assertAlmostEqual(total, config.PENALTY_HIGH_BETA)

    def test_kaputtes_datum_wirft_nicht(self):
        total, _ = scoring.penalties({"next_earnings": "nicht-ein-datum"},
                                     dt.date(2026, 8, 19))
        self.assertEqual(total, 0.0)


class TestGesamtscore(unittest.TestCase):

    def test_bester_fall_nahe_eins(self):
        s = scoring.score(snap(adx=40.0, rel_strength_63d=0.15,
                               volume=2_000_000.0),
                          FUND_GUT, {"sentiment": 1.0}, sector_median_pe=25.0)
        self.assertGreater(s["score"], 0.9)
        self.assertEqual(s["coverage"], 1.0)
        self.assertTrue(s["eligible"])

    def test_fehlende_fundamentaldaten_werden_nicht_bestraft(self):
        """Ohne Yahoo faellt die Abdeckung, aber der Score sinkt nicht."""
        voll = scoring.score(snap(), FUND_GUT, {"sentiment": 1.0},
                             sector_median_pe=25.0)
        nur_technik = scoring.score(snap(), None, {"sentiment": 1.0})
        self.assertLess(nur_technik["coverage"], voll["coverage"])
        # Die technischen Komponenten liefern denselben Beitrag wie zuvor
        tech_voll = {c["key"]: c["score"] for c in voll["components"]}
        tech_ohne = {c["key"]: c["score"] for c in nur_technik["components"]}
        for k in ("trend", "setup", "volumen"):
            self.assertAlmostEqual(tech_voll[k], tech_ohne[k])

    def test_zu_duenne_datenlage_ist_nicht_vorschlagsfaehig(self):
        # Nur Trend + Setup + Volumen = 0.45 Gewicht < 0.60 Mindestabdeckung
        s = scoring.score(snap(), None, None)
        self.assertLess(s["coverage"], scoring.MIN_DATA_COVERAGE)
        self.assertFalse(s["eligible"])

    def test_abzug_wirkt_auf_den_gesamtscore(self):
        ohne = scoring.score(snap(), FUND_GUT, {"sentiment": 1.0},
                             sector_median_pe=25.0, today=dt.date(2026, 8, 19))
        mit = scoring.score(snap(), {**FUND_GUT, "next_earnings": "2026-08-21"},
                            {"sentiment": 1.0}, sector_median_pe=25.0,
                            today=dt.date(2026, 8, 19))
        self.assertAlmostEqual(ohne["score"] - mit["score"],
                               config.PENALTY_EARNINGS_SOON, places=3)

    def test_score_bleibt_zwischen_null_und_eins(self):
        s = scoring.score(snap(ema9=80.0, ema21=90.0, ema50=95.0, sma200=120.0,
                               adx=1.0, rsi=15.0, rel_strength_63d=-0.5,
                               volume=100.0),
                          {"beta": 3.0, "next_earnings": "2026-08-20",
                           "profit_margin": -0.5},
                          {"sentiment": -1.0}, sector_median_pe=25.0,
                          today=dt.date(2026, 8, 19))
        self.assertGreaterEqual(s["score"], 0.0)
        self.assertLessEqual(s["score"], 1.0)

    def test_gar_keine_daten(self):
        s = scoring.score({}, None, None)
        self.assertIsNone(s["score"])
        self.assertFalse(s["eligible"])


class TestBranchenmedian(unittest.TestCase):

    def test_median_je_branche(self):
        eintraege = [
            {"sector": "Technologie", "fundamentals": {"forward_pe": p}}
            for p in (10.0, 20.0, 30.0)
        ] + [
            {"sector": "Energie", "fundamentals": {"forward_pe": p}}
            for p in (10.0, 12.0)          # nur zwei -> zu duenn
        ]
        med = scoring.sector_median_pes(eintraege)
        self.assertAlmostEqual(med["Technologie"], 20.0)
        self.assertNotIn("Energie", med)

    def test_ausreisser_werden_ignoriert(self):
        eintraege = [
            {"sector": "Tech", "fundamentals": {"forward_pe": p}}
            for p in (10.0, 20.0, 30.0, 5000.0, -8.0)
        ]
        self.assertAlmostEqual(scoring.sector_median_pes(eintraege)["Tech"], 20.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
