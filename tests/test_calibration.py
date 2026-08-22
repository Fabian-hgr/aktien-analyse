"""Die Kalibrierung gegen Handrechnungen pruefen.

Sie ist die Messlatte des ganzen Systems: aus ihr kommen Ziel- und
Stop-Faktor und die Basisquote, an der sich die Analyse messen lassen muss.
Stimmt sie nicht, stimmt keine einzige Zahl auf einer Karte.

Geprueft wird an kuenstlichen Kursreihen, deren Zahlen von Hand nachrechenbar
sind — nicht an echten Daten, die morgen anders aussehen.

Der Aufbau: 36 Bars, alle mit Kurs 100 und Spanne 99-101. Die True Range ist
damit konstant 2, also auch die ATR. Bei min_index 30 und Horizont 5 bleibt
GENAU EINE Beobachtung uebrig — Einstieg zu 100, Vorwaertsfenster Bar 31 bis
35. Jede gemessene Wahrscheinlichkeit ist dadurch 0 oder 1 und ohne
Statistik nachvollziehbar.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import calibration, config, indicators as ind    # noqa: E402

MIN_INDEX = 30
HORIZONT = 5
ATR = 2.0                       # Spanne 99-101


def bar(o, h, l, c):
    return {"t": "2026-01-01", "o": o, "h": h, "l": l, "c": c, "v": 1_000_000}


def ruhig(n=36):
    return [bar(100.0, 101.0, 99.0, 100.0) for _ in range(n)]


def messen(reihe, min_index=MIN_INDEX):
    return calibration.measure({"X": reihe}, {"X": "Test"}, horizon=HORIZONT,
                               min_index=min_index, min_observations=1)


def tabelle(reihe):
    return messen(reihe)["first_passage"]["_gesamt"]["tabelle"]


def mit_tag(hoch, tief, tag=1, n=36):
    """Ruhige Reihe mit einem Ereignis an Tag `tag` des Vorwaertsfensters."""
    reihe = ruhig(n)
    reihe[MIN_INDEX + tag] = bar(100.0, hoch, tief, 100.0)
    return reihe


class TestGrundrechnung(unittest.TestCase):

    def test_atr_ist_wie_erwartet_zwei(self):
        self.assertAlmostEqual(ind.precompute(ruhig(60))["atr"][40], ATR,
                               places=6)

    def test_genau_eine_beobachtung(self):
        self.assertEqual(messen(ruhig())["observations"], 1)

    def test_beobachtungen_werden_vollstaendig_gezaehlt(self):
        # min_index 30, 60 Bars, Horizont 5 -> i von 30 bis 54 = 25 Stueck
        self.assertEqual(messen(ruhig(60))["observations"], 25)

    def test_bewegung_wird_in_atr_gemessen(self):
        # Hoch 106 sind 6 USD ueber dem Kurs, bei ATR 2 also 3.0 ATR
        cal = messen(mit_tag(106.0, 99.0))
        self.assertAlmostEqual(cal["up"]["_gesamt"]["median"], 3.0, places=6)
        # Tief 94 sind 6 USD darunter, ebenfalls 3.0 ATR
        self.assertAlmostEqual(messen(mit_tag(101.0, 94.0))
                               ["down"]["_gesamt"]["median"], 3.0, places=6)

    def test_ruhige_reihe_bewegt_sich_eine_halbe_atr(self):
        cal = messen(ruhig())
        self.assertAlmostEqual(cal["up"]["_gesamt"]["median"], 0.5, places=6)

    def test_wahrscheinlichkeiten_ergeben_immer_eins(self):
        for reihe in (ruhig(60), mit_tag(106.0, 94.0, n=60),
                      mit_tag(104.5, 99.0, n=60)):
            for schluessel, werte in tabelle(reihe).items():
                self.assertAlmostEqual(sum(werte[:3]), 1.0, places=3,
                                       msg=schluessel)


class TestReihenfolge(unittest.TestCase):
    """Was zuerst kam, entscheidet — und bei Gleichstand gewinnt der Stop.

    Marken im Test: Ziel 2 ATR = 104, Stop 1.5 ATR = 97.
    """

    def test_ziel_zuerst(self):
        p_ziel, p_stop, p_zeit, _ = tabelle(mit_tag(104.5, 99.0))["2|1.5"]
        self.assertEqual((p_ziel, p_stop, p_zeit), (1.0, 0.0, 0.0))

    def test_stop_zuerst(self):
        p_ziel, p_stop, p_zeit, _ = tabelle(mit_tag(101.0, 96.5))["2|1.5"]
        self.assertEqual((p_ziel, p_stop, p_zeit), (0.0, 1.0, 0.0))

    def test_bei_gleichstand_zaehlt_der_stop(self):
        """Tagesbars loesen die Reihenfolge innerhalb eines Tages nicht auf.

        Die pessimistische Annahme ist fuer einen Ehrlichkeitstest die
        richtige — sie macht das Ergebnis eher zu schlecht als zu gut.
        """
        p_ziel, p_stop, _, _ = tabelle(mit_tag(105.0, 96.0))["2|1.5"]
        self.assertEqual((p_ziel, p_stop), (0.0, 1.0))

    def test_der_fruehere_tag_gewinnt(self):
        reihe = ruhig()
        reihe[MIN_INDEX + 1] = bar(100.0, 104.5, 99.0, 100.0)   # Ziel Tag 1
        reihe[MIN_INDEX + 3] = bar(100.0, 101.0, 96.0, 100.0)   # Stop Tag 3
        self.assertEqual(tabelle(reihe)["2|1.5"][0], 1.0)

        reihe = ruhig()
        reihe[MIN_INDEX + 1] = bar(100.0, 101.0, 96.0, 100.0)   # Stop Tag 1
        reihe[MIN_INDEX + 3] = bar(100.0, 104.5, 99.0, 100.0)   # Ziel Tag 3
        self.assertEqual(tabelle(reihe)["2|1.5"][1], 1.0)

    def test_nichts_beruehrt_ist_zeitablauf(self):
        self.assertEqual(tabelle(ruhig())["2|1.5"][2], 1.0)

    def test_r_bei_stop_ist_minus_eins(self):
        self.assertAlmostEqual(tabelle(mit_tag(101.0, 96.5))["2|1.5"][3], -1.0,
                               places=6)

    def test_r_bei_ziel_ist_das_chance_risiko_verhaeltnis(self):
        self.assertAlmostEqual(tabelle(mit_tag(104.5, 99.0))["2|1.5"][3],
                               2.0 / 1.5, places=3)

    def test_r_bei_zeitablauf_kommt_aus_dem_schlusskurs(self):
        reihe = ruhig()
        reihe[MIN_INDEX + HORIZONT] = bar(100.0, 101.0, 99.0, 101.5)
        # (101.5 - 100) / (1.5 * 2) = 0.5
        self.assertAlmostEqual(tabelle(reihe)["2|1.5"][3], 0.5, places=3)

    def test_engerer_stop_wird_haeufiger_getroffen(self):
        # Tief 98.3 sind 1.7 USD unter dem Kurs, bei ATR 2 also 0.85 ATR:
        # der Stop bei 0.8 ATR (98.4) wird beruehrt, der bei 1.0 ATR (98.0)
        # nicht.
        tab = tabelle(mit_tag(101.0, 98.3))
        self.assertEqual(tab["2|0.8"][1], 1.0, "Stop bei 0.8 ATR wird beruehrt")
        self.assertEqual(tab["2|1"][1], 0.0, "Stop bei 1.0 ATR nicht")


class TestKeinZukunftsblick(unittest.TestCase):

    def test_nur_bars_nach_dem_einstieg_zaehlen(self):
        """Ein Ereignis VOR dem Einstieg darf den AUSGANG nicht beeinflussen.

        Auf die ATR wirkt es sehr wohl — sie ist eine Rueckschau und soll
        vergangene Ausschlaege kennen. Verboten waere nur, Kurse NACH dem
        Einstiegstag in die Kennzahl einzurechnen.
        """
        reihe = ruhig()
        reihe[25] = bar(100.0, 130.0, 99.0, 100.0)
        cal = messen(reihe)
        self.assertLess(cal["up"]["_gesamt"]["median"], 1.0)
        self.assertEqual(tabelle(reihe)["2|1.5"][2], 1.0, "Zeitablauf")

    def test_der_einstiegsbar_selbst_zaehlt_nicht(self):
        reihe = ruhig()
        reihe[MIN_INDEX] = bar(100.0, 130.0, 99.0, 100.0)
        self.assertEqual(tabelle(reihe)["2|1.5"][2], 1.0)


class TestAbfragen(unittest.TestCase):

    def setUp(self):
        # Kuenstliche Quoten: 80 % bei 1 ATR, linear fallend auf 10 % bei 6 ATR
        quoten = {str(k): round(0.80 - (k - 1.0) * 0.14, 4)
                  for k in calibration.GITTER}
        eintrag = {"n": 50_000, "median": 2.0, "p75": 3.0, "p90": 4.0,
                   "erreichbar": quoten}
        klein = dict(eintrag, n=100, median=9.9)
        self.cal = {
            "measured_at": "2026-08-21T00:00:00+00:00",
            "horizon_days": 15, "observations": 50_000,
            "ziel_gitter": calibration.ZIEL_GITTER,
            "stop_gitter": calibration.STOP_GITTER,
            "up": {"_gesamt": eintrag, "Klein": klein},
            "down": {"_gesamt": dict(eintrag, median=1.5),
                     "Klein": dict(klein, median=9.9)},
            "first_passage": {"_gesamt": {"n": 50_000, "tabelle": {
                f"{z:g}|{s:g}": [0.4, 0.45, 0.15, 0.1]
                for z in calibration.ZIEL_GITTER
                for s in calibration.STOP_GITTER}}},
        }

    def test_beruehrungsquote_auf_der_stuetzstelle(self):
        self.assertAlmostEqual(
            calibration.hit_probability(self.cal, "X", "up", 2.0), 0.66,
            places=4)

    def test_beruehrungsquote_dazwischen_wird_interpoliert(self):
        # zwischen 2.0 (66 %) und 2.5 (59 %) liegt 2.25 bei 62.5 %
        self.assertAlmostEqual(
            calibration.hit_probability(self.cal, "X", "up", 2.25), 0.625,
            places=4)

    def test_ausserhalb_des_gitters_wird_geklemmt(self):
        self.assertAlmostEqual(
            calibration.hit_probability(self.cal, "X", "up", 99.0), 0.10,
            places=4)
        self.assertAlmostEqual(
            calibration.hit_probability(self.cal, "X", "up", 0.01), 0.80,
            places=4)

    def test_umkehrung_trifft_wieder_dieselbe_quote(self):
        for p in (0.75, 0.66, 0.50, 0.30, 0.20):
            d = calibration.distance_for_probability(self.cal, "X", "up", p)
            self.assertAlmostEqual(
                calibration.hit_probability(self.cal, "X", "up", d), p,
                places=3, msg=f"p={p}")

    def test_faktoren_ohne_kalibrierung(self):
        self.assertEqual(calibration.factors(None, "X"),
                         (config.ATR_TARGET_MULT, config.ATR_STOP_MULT))

    def test_faktoren_kommen_aus_den_medianen(self):
        self.assertEqual(calibration.factors(self.cal, "X"), (2.0, 1.5))

    def test_duenne_branche_faellt_auf_den_gesamtwert_zurueck(self):
        """100 Beobachtungen sind zu wenig fuer eine eigene Branchenzahl."""
        self.assertEqual(calibration.factors(self.cal, "Klein"), (2.0, 1.5))

    def test_faktoren_werden_geklemmt(self):
        wild = dict(self.cal)
        wild["up"] = {"_gesamt": dict(self.cal["up"]["_gesamt"], median=99.0)}
        wild["down"] = {"_gesamt": dict(self.cal["down"]["_gesamt"], median=0.1)}
        ziel, stop = calibration.factors(wild, "X")
        self.assertLessEqual(ziel, 4.0)
        self.assertGreaterEqual(stop, 0.8)

    def test_basisquote_kommt_aus_dem_gitter(self):
        b = calibration.base_rate(self.cal, "X")
        self.assertAlmostEqual(b["p_ziel"], 0.40, places=3)
        self.assertEqual(b["quelle"], "_gesamt")

    def test_ohne_kalibrierung_keine_erfundene_basisquote(self):
        self.assertIsNone(calibration.base_rate(None, "X"))
        self.assertIsNone(calibration.outcome(None, "X", 2.0, 1.5))
        self.assertIsNone(calibration.hit_probability(None, "X", "up", 2.0))
        self.assertIsNone(
            calibration.distance_for_probability(None, "X", "up", 0.3))

    def test_geklemmt_wird_ausgewiesen(self):
        self.assertTrue(calibration.outcome(self.cal, "X", 99.0, 1.5)["geklemmt"])
        self.assertFalse(calibration.outcome(self.cal, "X", 2.0, 1.5)["geklemmt"])


class TestGegenprobe(unittest.TestCase):
    """Gitter und unabhaengige Nachrechnung muessen dasselbe sagen."""

    def test_beide_wege_stimmen_ueberein(self):
        reihen = {}
        for nr, (hoch, tief) in enumerate(
                [(104.5, 99.0), (101.0, 96.5), (105.0, 96.0), (101.0, 99.0)]):
            reihen[f"S{nr}"] = mit_tag(hoch, tief, n=60)
        sektoren = {s: "Test" for s in reihen}
        cal = calibration.measure(reihen, sektoren, horizon=HORIZONT,
                                  min_index=MIN_INDEX, min_observations=1)
        # Faktoren des Gitters gegen die Tag-fuer-Tag-Rechnung
        ref = calibration.measure_base_rates(reihen, sektoren, cal,
                                             horizon=HORIZONT,
                                             min_index=MIN_INDEX,
                                             min_observations=1)
        k_ziel, k_stop = calibration.factors(cal, "Test")
        gitter = calibration.outcome(cal, "Test", k_ziel, k_stop)
        self.assertLess(abs(gitter["p_ziel"] - ref["_gesamt"]["p_ziel"]), 0.05)
        self.assertLess(abs(gitter["p_stop"] - ref["_gesamt"]["p_stop"]), 0.05)


if __name__ == "__main__":
    unittest.main(verbosity=2)
