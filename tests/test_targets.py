"""Kursziel-Methoden gegen Handrechnungen pruefen.

Drei Tests halten Konstruktionsfehler fest, die beim Bauen wirklich passiert
sind und erst durch Messung auffielen:

  test_analysten_ziehen_ziel_nicht_zum_kurs
      Analystenziele als vierte gemittelte Methode druecken das
      Chance-Risiko-Verhaeltnis systematisch unter 1.

  test_neigung_wirkt_in_atr_einheiten_gleich
      Eine Neigung am Kursniveau angesetzt trifft ruhige Aktien viel haerter
      als schwankende. Gemessen am 21.08.2026 sprang XOM dadurch von 2.75 auf
      4.33 ATR, die Trefferquote fiel von 39 % auf 18 %.

  test_ziel_bleibt_erreichbar
      Ein Ziel, das nur in 8 % der Faelle erreicht wird, sieht im
      Chance-Risiko-Verhaeltnis grossartig aus und ist trotzdem wertlos.
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import calibration, config, targets       # noqa: E402


def snap(price, atr=5.0, high55=115.0, high20=110.0, low20=90.0, vol=0.20):
    return {
        "close": price, "atr": atr, "vol_20d": vol,
        "donchian_high55": high55, "donchian_high20": high20,
        "donchian_low20": low20,
    }


def kalibrierung(median_auf=2.0, median_ab=1.5):
    """Kuenstliche Kalibrierung mit bekannten Zahlen.

    Die Beruehrungsquoten fallen linear von 80 % bei 1 ATR auf 10 % bei
    6 ATR — das erlaubt Handrechnungen fuer Interpolation und Umkehrung.
    """
    quoten = {}
    for k in calibration.GITTER:
        quoten[str(k)] = round(0.80 - (k - 1.0) * (0.70 / 5.0), 4)
    eintrag = {"n": 50_000, "median": median_auf, "p75": 3.0, "p90": 4.0,
               "erreichbar": dict(quoten)}
    ab = dict(eintrag, median=median_ab)
    tabelle = {}
    for z in calibration.ZIEL_GITTER:
        for st in calibration.STOP_GITTER:
            p_ziel = round(0.80 - (z - 1.0) * 0.10, 4)
            p_stop = round(min(0.9, 0.20 + st * 0.10), 4)
            rest = round(max(0.0, 1 - p_ziel - p_stop), 4)
            tabelle[f"{z:g}|{st:g}"] = [
                p_ziel, p_stop, rest, round(p_ziel * z / st - p_stop, 4)]
    return {
        "measured_at": "2026-08-21T00:00:00+00:00",
        "horizon_days": 15, "observations": 50_000,
        "ziel_gitter": calibration.ZIEL_GITTER,
        "stop_gitter": calibration.STOP_GITTER,
        "up": {"_gesamt": eintrag}, "down": {"_gesamt": ab},
        "first_passage": {"_gesamt": {"n": 50_000, "tabelle": tabelle}},
    }


class TestATRProjektion(unittest.TestCase):

    def test_formel(self):
        # Ziel = 100 + 2.0 * 5 = 110
        m = targets.atr_projection(100.0, 5.0, k=2.0)
        self.assertAlmostEqual(m.value, 110.0)
        self.assertIn("100.00 + 2.00 x 5.00 = 110.00", " ".join(m.steps))

    def test_standardfaktor_aus_config(self):
        m = targets.atr_projection(100.0, 5.0)
        self.assertAlmostEqual(m.value, 100 + config.ATR_TARGET_MULT * 5)

    def test_ohne_atr_nicht_verfuegbar(self):
        m = targets.atr_projection(100.0, None)
        self.assertFalse(m.available)
        self.assertTrue(m.note)

    def test_stop(self):
        stop, steps = targets.atr_stop(100.0, 5.0, 1.0)
        self.assertAlmostEqual(stop, 95.0)
        self.assertTrue(steps)


class TestStruktur(unittest.TestCase):

    def test_echter_widerstand_wird_ziel(self):
        # Abstand 112 - 100 = 12 USD > 1 x ATR (5) -> Widerstand zaehlt.
        # Deckel 3.5 x 5 = 17.5 USD greift nicht.
        m = targets.structure_target(100.0, 112.0, 110.0, 90.0, 5.0)
        self.assertAlmostEqual(m.value, 112.0)

    def test_widerstand_in_rauschweite_wird_zu_ausbruch(self):
        # Abstand 102 - 100 = 2 USD < 1 x ATR (5) -> kein Widerstand.
        # Basishoehe 105 - 95 = 10  ->  Ziel = 102 + 10 = 112
        m = targets.structure_target(100.0, 102.0, 105.0, 95.0, 5.0)
        self.assertAlmostEqual(m.value, 112.0)

    def test_ausbruch_ueber_hoch(self):
        # Kurs 100 >= Hoch 100 -> Ziel = 100 + (105-95) = 110
        m = targets.structure_target(100.0, 100.0, 105.0, 95.0, 5.0)
        self.assertAlmostEqual(m.value, 110.0)

    def test_rueckfalldeckel_ohne_kalibrierung(self):
        # Rohziel = 100 + (140-90) = 150, Deckel = 100 + 3.5*5 = 117.5
        m = targets.structure_target(100.0, 100.0, 140.0, 90.0, 5.0)
        self.assertAlmostEqual(m.value, config.STRUCTURE_CAP_ATR_FALLBACK * 5 + 100)
        self.assertIn("Gekappt", " ".join(m.steps))

    def test_gemessener_deckel_schlaegt_den_rueckfallwert(self):
        m = targets.structure_target(100.0, 100.0, 140.0, 90.0, 5.0,
                                     cap_atr=2.0, cap_grund=" - gemessen")
        self.assertAlmostEqual(m.value, 110.0)
        self.assertIn("gemessen", " ".join(m.steps))

    def test_ohne_historie(self):
        self.assertFalse(
            targets.structure_target(100.0, None, None, None, 5.0).available)


class TestNeigungen(unittest.TestCase):

    def test_analysten_neigung(self):
        # 110/100 - 1 = +10 %, unter der Kappe von 30 %
        tilt, m = targets.analyst_tilt(100.0, 110.0, 20)
        self.assertAlmostEqual(tilt, 0.10)
        self.assertEqual(m.role, "neigung")

    def test_analysten_neigung_gekappt(self):
        tilt, _ = targets.analyst_tilt(100.0, 200.0, 20)
        self.assertAlmostEqual(tilt, config.TILT_CAP)

    def test_zu_wenige_analysten_zaehlen_nicht(self):
        tilt, m = targets.analyst_tilt(100.0, 150.0, 2)
        self.assertEqual(tilt, 0.0)
        self.assertIn("Analysten", m.note)

    def test_negative_analysten_neigung(self):
        tilt, _ = targets.analyst_tilt(100.0, 90.0, 20)
        self.assertAlmostEqual(tilt, -0.10)

    def test_bewertung(self):
        # Fair = 5 * 24 = 120  ->  120/100 - 1 = +20 %
        tilt, m = targets.valuation_tilt(100.0, 5.0, 24.0, "Technologie")
        self.assertAlmostEqual(tilt, 0.20)
        self.assertAlmostEqual(m.value, 120.0)

    def test_bewertung_ohne_daten(self):
        tilt, m = targets.valuation_tilt(100.0, None, 24.0)
        self.assertEqual(tilt, 0.0)
        self.assertFalse(m.available)


class TestMarken(unittest.TestCase):
    """Woher Ziel- und Stop-Faktor kommen — und was NICHT gelernt wird."""

    def test_ohne_kalibrierung_greifen_die_rueckfallwerte(self):
        mk = targets.marks("Technologie", None)
        self.assertEqual(mk["ziel_k"], config.ATR_TARGET_MULT)
        self.assertEqual(mk["stop_k"], config.ATR_STOP_MULT)
        self.assertEqual(mk["quelle"], "config")

    def test_kalibrierung_setzt_beide_faktoren(self):
        mk = targets.marks("Technologie", kalibrierung(2.4, 1.8))
        self.assertAlmostEqual(mk["ziel_k"], 2.4)
        self.assertAlmostEqual(mk["stop_k"], 1.8)
        self.assertEqual(mk["quelle"], "kalibriert")

    def test_gelernter_multiplikator_wirkt_nur_aufs_ziel(self):
        cal = kalibrierung(2.0, 1.5)
        mk = targets.marks("Technologie", cal, k_mult=1.2)
        self.assertAlmostEqual(mk["ziel_k"], 2.4)
        self.assertAlmostEqual(mk["stop_k"], 1.5, msg="Stop wird nie gelernt")

    def test_multiplikator_bleibt_in_den_grenzen(self):
        cal = kalibrierung(2.0, 1.5)
        self.assertAlmostEqual(
            targets.marks("T", cal, k_mult=99.0)["k_mult"],
            config.SECTOR_K_MULT_MAX)
        self.assertAlmostEqual(
            targets.marks("T", cal, k_mult=0.0)["k_mult"],
            config.SECTOR_K_MULT_MIN)

    def test_ausdrueckliche_vorgabe_schlaegt_alles(self):
        mk = targets.marks("T", kalibrierung(), k_sector=3.0, stop_mult=0.9)
        self.assertAlmostEqual(mk["ziel_k"], 3.0)
        self.assertAlmostEqual(mk["stop_k"], 0.9)
        self.assertEqual(mk["quelle"], "vorgegeben")


class TestZusammenfuehrung(unittest.TestCase):

    def test_gewichteter_mittelwert_ohne_neigung(self):
        # ATR-Ziel = 100 + 2.0*5 = 110.0 ; Struktur = 115 (echter Widerstand)
        # Mittel bei gleichen Gewichten = 112.5
        r = targets.build(100.0, snap(100.0), fundamentals=None)
        self.assertAlmostEqual(r["target"], 112.5, places=2)

    def test_stop_und_chance_risiko(self):
        # Stop = 100 - 1.65*5 = 91.75 ; Risiko 8.25 ; Chance 12.5
        # CRV = 12.5 / 8.25 = 1.515...
        r = targets.build(100.0, snap(100.0), fundamentals=None)
        self.assertAlmostEqual(r["stop"], 91.75, places=2)
        self.assertAlmostEqual(r["reward_risk"], round(12.5 / 8.25, 2), places=2)

    def test_neigungen_verschieben_den_abstand(self):
        # Analysten +30 % (gekappt) x 0.30 = +9 %
        # Bewertung: Fair = 5*24 = 120 -> +20 % x 0.30 = +6 %
        # Faktor = 1.15 auf den ABSTAND 12.5  ->  100 + 14.375 = 114.375
        f = {"target_mean": 200.0, "analyst_count": 20, "forward_eps": 5.0}
        r = targets.build(100.0, snap(100.0), f, sector_median_pe=24.0)
        self.assertAlmostEqual(r["target"], 114.38, places=2)

    def test_erwartungsbereich_kommt_aus_volatilitaet(self):
        # Sigma = 100 * 0.20 * sqrt(15/252) = 1.5430...
        r = targets.build(100.0, snap(100.0, vol=0.20), fundamentals=None)
        erwartet = 100 * 0.20 * math.sqrt(15 / 252)
        self.assertAlmostEqual(r["sigma"], round(erwartet, 2), places=2)
        self.assertAlmostEqual(r["band_low"], round(r["target"] - erwartet, 2),
                               places=1)

    def test_ohne_fundamentaldaten_bleibt_rechenbar(self):
        r = targets.build(100.0, snap(100.0), fundamentals=None)
        self.assertIsNotNone(r["target"])
        self.assertEqual(r["analyst_tilt"], 0.0)
        self.assertEqual(r["valuation_tilt"], 0.0)

    def test_ohne_jede_methode_gibt_kein_ziel(self):
        leer = {"close": 100.0, "atr": None, "vol_20d": None,
                "donchian_high55": None, "donchian_high20": None,
                "donchian_low20": None}
        r = targets.build(100.0, leer, None)
        self.assertIsNone(r["target"])
        self.assertIsNone(r["reward_risk"])
        self.assertIn("Keine Kurszielmethode", r["reason"])


class TestWahrscheinlichkeiten(unittest.TestCase):
    """Ohne Trefferwahrscheinlichkeit sagt ein Chance-Risiko-Verhaeltnis nichts."""

    def test_ohne_kalibrierung_keine_erfundenen_zahlen(self):
        r = targets.build(100.0, snap(100.0), None)
        self.assertIsNone(r["p_ziel_beruehrt"])
        self.assertIsNone(r["basisquote"])
        self.assertNotIn("probability_steps", r)

    def test_mit_kalibrierung_stehen_alle_zahlen_auf_der_karte(self):
        r = targets.build(100.0, snap(100.0), None, cal=kalibrierung())
        self.assertIsNotNone(r["p_ziel_beruehrt"])
        self.assertIsNotNone(r["p_stop_beruehrt"])
        self.assertIsNotNone(r["basisquote"])
        self.assertTrue(r["probability_steps"])

    def test_abstaende_werden_in_atr_gerechnet(self):
        r = targets.build(100.0, snap(100.0), None, cal=kalibrierung(2.0, 1.5))
        # ATR-Ziel 110, Struktur 115 -> 112.5 -> 2.5 ATR; Stop 1.5 ATR
        self.assertAlmostEqual(r["ziel_atr"], 2.5, places=2)
        self.assertAlmostEqual(r["stop_atr"], 1.5, places=2)

    def test_beruehrungsquote_wird_interpoliert(self):
        # Kunstliche Quoten: 80 % bei 1 ATR, linear fallend auf 10 % bei 6 ATR
        # -> bei 2.5 ATR sind das 80 - 1.5*14 = 59 %
        r = targets.build(100.0, snap(100.0), None, cal=kalibrierung(2.0, 1.5))
        self.assertAlmostEqual(r["p_ziel_beruehrt"], 0.59, places=2)

    def test_ziel_bleibt_erreichbar(self):
        """Der Deckel kommt aus der Messung, nicht aus einer Schaetzung."""
        cal = kalibrierung(2.0, 1.5)
        # Struktur will weit hinaus: Basishoehe 60 USD
        s = snap(100.0, high55=100.0, high20=160.0, low20=100.0)
        r = targets.build(100.0, s, None, sector="Technologie", cal=cal)
        deckel = calibration.distance_for_probability(
            cal, "Technologie", "up", config.STRUCTURE_CAP_PROBABILITY)
        self.assertLessEqual(r["ziel_atr"], deckel + 0.01)
        self.assertGreaterEqual(r["p_ziel_beruehrt"],
                                config.STRUCTURE_CAP_PROBABILITY - 0.01)


class TestKonstruktionsfehlerBleibenBehoben(unittest.TestCase):
    """Regressionstests fuer Fehler, die beim Bauen wirklich passiert sind."""

    def test_analysten_ziehen_ziel_nicht_zum_kurs(self):
        # Reales Apple-Beispiel vom 2026-08-19: Kurs 316.90, ATR 7.58,
        # 55-Tage-Hoch 344.26, Konsensziel 325.70 bei 41 Analysten,
        # Forward-EPS 9.54 gegen ein Branchen-KGV von 28.
        #
        # Als vierte GEMITTELTE Methode ergaeben die vier Niveaus
        #   ATR 332.06, Struktur 343.43, Analysten zeitanteilig 317.42,
        #   Bewertung 267.12
        # einen Mittelwert UNTER dem heutigen Kurs. Als Neigung bleibt das
        # Ziel darueber und das Chance-Risiko-Verhaeltnis brauchbar.
        s = snap(316.90, atr=7.58, high55=344.26, high20=330.0, low20=300.0,
                 vol=0.356)
        f = {"target_mean": 325.70, "analyst_count": 41, "forward_eps": 9.54}
        r = targets.build(316.90, s, f, sector="Technologie",
                          sector_median_pe=28.0)

        naiv = (332.06 + 343.43 + 317.42 + 267.12) / 4
        self.assertLess(naiv, r["price"], "Der alte Entwurf lag unter dem Kurs")
        self.assertGreater(r["target"], r["price"])
        self.assertGreater(r["reward_risk"], 1.5)

    def test_neigung_wirkt_in_atr_einheiten_gleich(self):
        """Eine Neigung darf ruhige Titel nicht haerter treffen als wilde.

        Am Kursniveau angesetzt waeren 3 % bei einer Aktie mit 2 % ATR
        anderthalb ATR, bei einer mit 8 % ATR nur ein Drittel davon.
        """
        f = {"target_mean": 200.0, "analyst_count": 20}   # +30 %, gekappt
        verschiebungen = []
        for atr in (2.0, 8.0):
            s = snap(100.0, atr=atr, high55=100.0 + 3 * atr,
                     high20=100.0, low20=100.0 - 2 * atr)
            ohne = targets.build(100.0, s, None)
            mit = targets.build(100.0, s, f)
            verschiebungen.append((mit["target"] - ohne["target"]) / atr)
        self.assertAlmostEqual(verschiebungen[0], verschiebungen[1], places=6)
        self.assertGreater(verschiebungen[0], 0.0)

    def test_analystenmethode_hat_rolle_neigung(self):
        r = targets.build(100.0, snap(100.0), {"target_mean": 110.0,
                                               "analyst_count": 20})
        rollen = {m["key"]: m["role"] for m in r["methods"]}
        self.assertEqual(rollen["atr"], "niveau")
        self.assertEqual(rollen["struktur"], "niveau")
        self.assertEqual(rollen["analysten"], "neigung")
        self.assertEqual(rollen["bewertung"], "neigung")
        self.assertEqual(sorted(r["methods_used"]), ["atr", "struktur"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
