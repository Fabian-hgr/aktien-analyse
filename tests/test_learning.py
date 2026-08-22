"""Lernschleife pruefen.

Die entscheidenden Eigenschaften:
  - Gewichte verlassen ihre Grenzen NIE, egal wie extrem die Eingabe
  - die Richtung stimmt: Gewinn belohnt, Verlust bestraft
  - bei kleiner Stichprobe passiert wenig (Daempfung)
  - das Zufallsdepot fliesst nirgends ein
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import calibration, config, learning      # noqa: E402

HEUTE = dt.date(2026, 8, 19)


def trade(depot="ki", r=1.0, mfe=120.0, entry=100.0, atr=5.0,
          sector="Technologie", komponenten=None, methoden=None,
          trend="aufwaerts", vix="ruhig", exit_date="2026-08-19",
          exit_reason="ziel", basis_p_ziel=0.43, stop_atr=1.5):
    return {
        "depot": depot, "r_multiple": r, "mfe_price": mfe,
        "entry_price": entry, "atr_at_entry": atr, "sector": sector,
        "stop_atr": stop_atr,
        "exit_date": exit_date, "exit_reason": exit_reason,
        "basis_p_ziel": basis_p_ziel, "basis_erwartung_r": 0.09,
        "score_components": komponenten or {"trend": 0.8, "setup": 0.6},
        "target_methods": methoden or {"atr": 111.0, "struktur": 130.0},
        "regime_at_entry": {"trend": trend, "vix_level": vix},
    }


class TestKontrollgruppe(unittest.TestCase):

    def test_zufallsdepot_wird_ignoriert(self):
        alle = [trade(depot="ki") for _ in range(5)] + \
               [trade(depot="zufall") for _ in range(50)]
        self.assertEqual(len(learning.ki_trades(alle)), 5)

    def test_lernt_nicht_aus_zufallstrades(self):
        w = learning.default_weights()
        vorher = dict(w["score_weights"])
        learning.update(w, [trade(depot="zufall", r=5.0) for _ in range(100)],
                        HEUTE)
        self.assertEqual(w["score_weights"], vorher)
        self.assertEqual(w["trades_seen"], 0)


class TestSchwelleUndDaempfung(unittest.TestCase):

    def test_unter_der_mindestzahl_passiert_nichts(self):
        w = learning.default_weights()
        vorher = dict(w["target_method_weights"])
        learning.update(w, [trade() for _ in range(config.LEARN_MIN_TRADES - 1)],
                        HEUTE)
        self.assertEqual(w["target_method_weights"], vorher)

    def test_daempfung_waechst_mit_der_stichprobe(self):
        self.assertLess(learning._damping(20), learning._damping(100))
        self.assertLess(learning._damping(100), learning._damping(200))
        self.assertEqual(learning._damping(learning.FULL_CONFIDENCE_AT), 1.0)
        self.assertEqual(learning._damping(10_000), 1.0)


class TestKurszielMethoden(unittest.TestCase):

    def test_trefferquote(self):
        # Ziel 'atr' bei 111 wird von mfe 120 erreicht, 'struktur' bei 130 nicht
        stats = learning.method_hit_rates([trade() for _ in range(10)],
                                          kalibrierung())
        self.assertEqual(stats["atr"]["hit_rate"], 1.0)
        self.assertEqual(stats["struktur"]["hit_rate"], 0.0)

    def test_treffende_methode_wird_belohnt(self):
        w = learning.default_weights()
        alt_atr = w["target_method_weights"]["atr"]
        alt_str = w["target_method_weights"]["struktur"]
        learning.update(w, [trade() for _ in range(60)], HEUTE, kalibrierung())
        self.assertGreater(w["target_method_weights"]["atr"], alt_atr)
        self.assertLess(w["target_method_weights"]["struktur"], alt_str)

    def test_gewichte_bleiben_in_den_grenzen(self):
        w = learning.default_weights()
        # 100 Runden mit immer denselben Extremdaten
        for _ in range(100):
            learning.update(w, [trade() for _ in range(200)], HEUTE)
        for key, v in w["target_method_weights"].items():
            self.assertGreaterEqual(v, config.WEIGHT_MIN, key)
            self.assertLessEqual(v, config.WEIGHT_MAX, key)


class TestScoreKomponenten(unittest.TestCase):

    def _trades_mit_trennschaerfe(self, n=60):
        """Hoher Trend-Wert geht mit hohem R einher, Setup ist Rauschen."""
        out = []
        for i in range(n):
            hoch = i % 2 == 0
            out.append(trade(
                r=2.0 if hoch else -1.0,
                komponenten={"trend": 0.9 if hoch else 0.2,
                             "setup": 0.5},
            ))
        return out

    def test_trennschaerfe_wird_erkannt(self):
        edges = learning.component_edges(self._trades_mit_trennschaerfe())
        self.assertIn("trend", edges)
        self.assertAlmostEqual(edges["trend"]["edge_r"], 3.0, places=2)

    def test_konstante_komponente_wird_als_gebunden_ausgewiesen(self):
        """Eine Komponente mit ueberall demselben Wert kann nicht trennen.

        Frueher fiel sie stillschweigend aus der Auswertung — im Backtest
        vom 21.08.2026 verschwand `volumen` deshalb spurlos aus der Tabelle.
        Jetzt steht sie mit Vorsprung 0 und dem Vermerk `gebunden` da.
        """
        edges = learning.component_edges(self._trades_mit_trennschaerfe())
        self.assertIn("setup", edges)
        self.assertTrue(edges["setup"]["gebunden"])
        self.assertEqual(edges["setup"]["edge_r"], 0.0)

    def test_gebundene_komponente_wird_weder_belohnt_noch_bestraft(self):
        w = learning.default_weights()
        zeilen = learning.update_score_weights(
            w, self._trades_mit_trennschaerfe(200))
        # Das Protokoll nennt die Komponente beim Klartextnamen, nicht beim
        # Schluessel — es wird auf der Seite gelesen, nicht von Code.
        self.assertTrue(any(config.SCORE_LABELS["trend"] in z for z in zeilen))
        self.assertFalse(any(config.SCORE_LABELS["setup"] in z for z in zeilen))

    def test_trennscharfe_komponente_wird_belohnt(self):
        w = learning.default_weights()
        alt = w["score_weights"]["trend"]
        learning.update(w, self._trades_mit_trennschaerfe(), HEUTE)
        self.assertGreater(w["score_weights"]["trend"], alt)

    def test_summe_bleibt_eins(self):
        w = learning.default_weights()
        for _ in range(20):
            learning.update(w, self._trades_mit_trennschaerfe(), HEUTE)
        self.assertAlmostEqual(sum(w["score_weights"].values()), 1.0, places=2)

    def test_einzelgewichte_bleiben_in_den_grenzen(self):
        w = learning.default_weights()
        for _ in range(50):
            learning.update(w, self._trades_mit_trennschaerfe(200), HEUTE)
        for key, v in w["score_weights"].items():
            self.assertGreaterEqual(v, learning.SCORE_WEIGHT_MIN * 0.9, key)
            self.assertLessEqual(v, learning.SCORE_WEIGHT_MAX * 1.1, key)


class TestZielweite(unittest.TestCase):
    """Die Zielweite wird an der BASISQUOTE gemessen, nicht an der MFE.

    Die MFE waere vom Stop abgeschnitten: wer an Tag 2 ausgestoppt wird, hat
    definitionsgemaess eine winzige guenstige Bewegung. Ein daraus gelernter
    Faktor schrumpft sich selbst.
    """

    def test_ueber_der_basisquote_darf_das_ziel_weiter(self):
        # Basisquote 43 %, tatsaechlich 100 % Treffer -> Multiplikator steigt
        w = learning.default_weights()
        learning.update(w, [trade(exit_reason="ziel") for _ in range(60)], HEUTE)
        self.assertGreater(w["sector_k_mult"]["Technologie"], 1.0)

    def test_unter_der_basisquote_rueckt_das_ziel_naeher(self):
        w = learning.default_weights()
        learning.update(w, [trade(exit_reason="stop", r=-1.0)
                            for _ in range(60)], HEUTE)
        self.assertLess(w["sector_k_mult"]["Technologie"], 1.0)

    def test_treffer_genau_auf_der_basisquote_aendert_kaum_etwas(self):
        w = learning.default_weights()
        trades = ([trade(exit_reason="ziel") for _ in range(43)] +
                  [trade(exit_reason="stop", r=-1.0) for _ in range(57)])
        learning.update(w, trades, HEUTE)
        self.assertAlmostEqual(w["sector_k_mult"].get("Technologie", 1.0),
                               1.0, places=2)

    def test_multiplikator_bleibt_in_den_grenzen(self):
        w = learning.default_weights()
        for _ in range(60):
            learning.update(w, [trade(exit_reason="ziel", basis_p_ziel=0.01)
                                for _ in range(60)], HEUTE)
        self.assertLessEqual(w["sector_k_mult"]["Technologie"],
                             config.SECTOR_K_MULT_MAX)
        for _ in range(60):
            learning.update(w, [trade(exit_reason="stop", r=-1.0,
                                      basis_p_ziel=0.99)
                                for _ in range(60)], HEUTE)
        self.assertGreaterEqual(w["sector_k_mult"]["Technologie"],
                                config.SECTOR_K_MULT_MIN)

    def test_ohne_basisquote_wird_nichts_gelernt(self):
        """Alte Trades ohne gemessene Messlatte duerfen nichts bewirken."""
        w = learning.default_weights()
        ohne = [trade() for _ in range(60)]
        for t in ohne:
            t["basis_p_ziel"] = None
        learning.update(w, ohne, HEUTE)
        self.assertEqual(w["sector_k_mult"], {})

    def test_zu_wenige_trades_je_branche(self):
        w = learning.default_weights()
        gemischt = ([trade(sector="Technologie") for _ in range(30)] +
                    [trade(sector="Energie") for _ in range(3)])
        learning.update(w, gemischt, HEUTE)
        self.assertIn("Technologie", w["sector_k_mult"])
        self.assertNotIn("Energie", w["sector_k_mult"])


class TestMultiplikatoren(unittest.TestCase):

    def test_gute_branche_wird_belohnt(self):
        w = learning.default_weights()
        gemischt = ([trade(sector="Technologie", r=3.0) for _ in range(30)] +
                    [trade(sector="Energie", r=-1.0) for _ in range(30)])
        learning.update(w, gemischt, HEUTE)
        self.assertGreater(w["sector_multiplier"]["Technologie"], 1.0)
        self.assertLess(w["sector_multiplier"]["Energie"], 1.0)

    def test_nur_eine_branche_ergibt_keinen_multiplikator(self):
        """Ein Multiplikator misst gegen den Gesamtschnitt. Gibt es nur eine
        Branche, IST sie der Gesamtschnitt — dann gibt es nichts anzupassen."""
        w = learning.default_weights()
        learning.update(w, [trade(sector="Technologie", r=50.0)
                            for _ in range(60)], HEUTE)
        self.assertEqual(w["sector_multiplier"], {})

    def test_multiplikatoren_bleiben_in_den_grenzen(self):
        w = learning.default_weights()
        gemischt = ([trade(sector="Technologie", r=50.0) for _ in range(40)] +
                    [trade(sector="Energie", r=-30.0) for _ in range(40)])
        for _ in range(80):
            learning.update(w, gemischt, HEUTE)
        self.assertLessEqual(w["sector_multiplier"]["Technologie"],
                             config.MULT_MAX)
        self.assertGreaterEqual(w["sector_multiplier"]["Energie"],
                                config.MULT_MIN)

    def test_regime_wird_getrennt_gefuehrt(self):
        w = learning.default_weights()
        gemischt = ([trade(trend="aufwaerts", vix="ruhig", r=3.0) for _ in range(30)] +
                    [trade(trend="abwaerts", vix="gestresst", r=-2.0) for _ in range(30)])
        learning.update(w, gemischt, HEUTE)
        self.assertGreater(w["regime_multiplier"]["aufwaerts/ruhig"], 1.0)
        self.assertLess(w["regime_multiplier"]["abwaerts/gestresst"], 1.0)

    def test_wirksamer_score_wird_begrenzt(self):
        w = learning.default_weights()
        w["sector_multiplier"]["Technologie"] = 1.5
        w["regime_multiplier"]["aufwaerts/ruhig"] = 1.5
        score, notizen = learning.effective_score(
            0.9, "Technologie", {"trend": "aufwaerts", "vix_level": "ruhig"}, w)
        self.assertLessEqual(score, 1.0)
        self.assertEqual(len(notizen), 2)


class TestProtokoll(unittest.TestCase):

    def test_jede_aenderung_wird_protokolliert(self):
        w = learning.default_weights()
        learning.update(w, [trade() for _ in range(60)], HEUTE)
        self.assertTrue(w["history"])
        eintrag = w["history"][-1]
        self.assertEqual(eintrag["date"], HEUTE.isoformat())
        self.assertTrue(eintrag["changes"])
        self.assertIn("mean_r", eintrag)

    def test_protokoll_waechst_nicht_unbegrenzt(self):
        w = learning.default_weights()
        w["history"] = [{"date": "x"} for _ in range(300)]
        learning.update(w, [trade() for _ in range(60)], HEUTE)
        self.assertLessEqual(len(w["history"]), 200)

    def test_zeitstempel_wird_gesetzt(self):
        w = learning.default_weights()
        learning.update(w, [trade() for _ in range(60)], HEUTE)
        self.assertIsNotNone(w["updated_at"])



def kalibrierung(quote_bei_2_atr=0.40):
    """Kuenstliche Kalibrierung: ein Ziel bei 2.0 ATR kommt in `quote` der
    Faelle vor dem Stop — unabhaengig davon, wo der Stop liegt."""
    erreichbar = {str(k): round(max(0.02, quote_bei_2_atr * 2.0 / k), 4)
                  for k in calibration.GITTER}
    eintrag = {"n": 50_000, "median": 2.0, "p75": 3.0, "p90": 4.0,
               "erreichbar": erreichbar}
    tabelle = {}
    for z in calibration.ZIEL_GITTER:
        p_ziel = round(max(0.02, quote_bei_2_atr * 2.0 / z), 4)
        for st in calibration.STOP_GITTER:
            tabelle[f"{z:g}|{st:g}"] = [p_ziel, round(1 - p_ziel, 4), 0.0, 0.0]
    return {
        "measured_at": "2026-08-21T00:00:00+00:00", "horizon_days": 15,
        "observations": 50_000,
        "ziel_gitter": calibration.ZIEL_GITTER,
        "stop_gitter": calibration.STOP_GITTER,
        "up": {"_gesamt": eintrag},
        "down": {"_gesamt": dict(eintrag, median=1.5)},
        "first_passage": {"_gesamt": {"n": 50_000, "tabelle": tabelle}},
    }


class TestEvidenz(unittest.TestCase):
    """Der Schritt richtet sich nach der SICHERHEIT, nicht nach der Groesse.

    Das ist der Kern der Lernschleife. Am 21.08.2026 wurde gemessen: die
    Komponente `trend` ist ueber 130'535 Beobachtungen der beste Trenner
    (+1.25 Prozentpunkte), erschien aber im 100-Trade-Fenster als schaedlich
    und wurde bestraft. Mit der alten, groessenbasierten Regel fiel die
    Backtest-Rendite dadurch von +5.45 % auf +2.97 %.
    """

    def test_kein_signal_ohne_unterschied(self):
        self.assertEqual(learning._evidenz(0.0), 0.0)

    def test_voller_ausschlag_erst_ab_zwei_sigma(self):
        self.assertAlmostEqual(learning._evidenz(2.0), 1.0)
        self.assertAlmostEqual(learning._evidenz(5.0), 1.0)
        self.assertAlmostEqual(learning._evidenz(-2.0), -1.0)

    def test_ein_sigma_gibt_nur_halben_ausschlag(self):
        self.assertAlmostEqual(learning._evidenz(1.0), 0.5)

    def test_fehlende_zahl_bewirkt_nichts(self):
        self.assertEqual(learning._evidenz(None), 0.0)
        self.assertEqual(learning._evidenz(float("nan")), 0.0)

    def test_streuungsfreier_unterschied_ist_volle_evidenz(self):
        self.assertEqual(learning._evidenz(float("inf")), 1.0)
        self.assertEqual(learning._evidenz(float("-inf")), -1.0)

    def test_grosser_aber_verrauschter_vorsprung_zaehlt_weniger(self):
        """Zwei Faelle mit gleichem Mittelwertunterschied, andere Streuung."""
        sauber = learning._mittelwert_t([1.0] * 30 + [1.1] * 30,
                                        [0.0] * 30 + [0.1] * 30)
        verrauscht = learning._mittelwert_t([5.0, -3.0] * 30,
                                            [4.0, -4.0] * 30)
        self.assertGreater(abs(sauber), abs(verrauscht))
        self.assertGreater(learning._evidenz(sauber),
                           learning._evidenz(verrauscht))


class TestKurszielMethodenMessen(unittest.TestCase):
    """Verglichen wird gegen die MESSUNG — und die Methoden gegeneinander.

    Aufbau: Einstieg 100, ATR 5, Stop 1.5 ATR. Methode 'atr' zielt auf 110
    (2.0 ATR, erwartete Erstpassage 40 %), Methode 'struktur' auf 115
    (3.0 ATR, erwartet 26.7 %). Die groesste guenstige Bewegung je Trade
    entscheidet, welche Methode recht behielt.
    """

    def _trades(self, treffer_atr, treffer_struktur, n=100):
        beide = int(round(treffer_struktur * n))
        nur_atr = int(round(treffer_atr * n)) - beide
        out = []
        for i in range(n):
            if i < beide:
                mfe = 116.0
            elif i < beide + nur_atr:
                mfe = 111.0
            else:
                mfe = 100.0
            t = trade(mfe=mfe, entry=100.0, atr=5.0,
                      methoden={"atr": 110.0, "struktur": 115.0})
            t["stop_atr"] = 1.5
            out.append(t)
        return out

    def test_erwartung_kommt_aus_der_entfernung(self):
        stats = learning.method_hit_rates(self._trades(0.40, 0.267),
                                          kalibrierung(0.40))
        self.assertAlmostEqual(stats["atr"]["erwartet_rate"], 0.40, places=2)
        self.assertAlmostEqual(stats["struktur"]["erwartet_rate"], 0.267,
                               places=2)
        self.assertAlmostEqual(stats["atr"]["hit_rate"], 0.40, places=2)
        self.assertAlmostEqual(stats["atr"]["edge"], 0.0, places=2)

    def test_treffer_wie_erwartet_veraendert_praktisch_nichts(self):
        """Der Fehler der ersten Fassung: bei kalibrierten Zielen liegt die
        Trefferquote bauartbedingt bei rund 40 %. Gegen 50 % gemessen wurde
        deshalb JEDE Methode in JEDER Runde bestraft."""
        w = learning.default_weights()
        vorher = dict(w["target_method_weights"])
        learning.update_method_weights(w, self._trades(0.40, 0.267),
                                       kalibrierung(0.40))
        for key, alt in vorher.items():
            self.assertAlmostEqual(w["target_method_weights"][key] / alt, 1.0,
                                   delta=0.01, msg=key)

    def test_die_bessere_methode_bekommt_mehr_gewicht(self):
        w = learning.default_weights()
        vorher = dict(w["target_method_weights"])
        learning.update_method_weights(w, self._trades(0.75, 0.267),
                                       kalibrierung(0.40))
        self.assertGreater(w["target_method_weights"]["atr"], vorher["atr"])
        self.assertLess(w["target_method_weights"]["struktur"],
                        vorher["struktur"])

    def test_die_schlechtere_methode_verliert_gewicht(self):
        # 'atr' trifft 28 % statt der erwarteten 40 %, 'struktur' liegt mit
        # 26.7 % genau auf seiner Erwartung.
        w = learning.default_weights()
        vorher = dict(w["target_method_weights"])
        learning.update_method_weights(w, self._trades(0.28, 0.267),
                                       kalibrierung(0.40))
        self.assertLess(w["target_method_weights"]["atr"], vorher["atr"])
        self.assertGreater(w["target_method_weights"]["struktur"],
                           vorher["struktur"])

    def test_zwei_gleich_schlechte_methoden_bleiben_gleich_gewichtet(self):
        """Beide weit unter der Erwartung: relativ aendert sich nichts."""
        w = learning.default_weights()
        vorher = dict(w["target_method_weights"])
        learning.update_method_weights(w, self._trades(0.05, 0.0),
                                       kalibrierung(0.40))
        self.assertEqual(w["target_method_weights"], vorher)

    def test_gemeinsame_verzerrung_faellt_heraus(self):
        """Beide Methoden gleich weit unter der Erwartung — nichts aendert sich.

        Die eigene Beobachtung endet am Ausstieg, weshalb jede Methode zu
        schlecht aussieht. Fuer das Kursziel zaehlt nur das Verhaeltnis der
        Gewichte; eine Verzerrung, die alle gleich trifft, darf nichts
        bewirken.
        """
        w = learning.default_weights()
        vorher = dict(w["target_method_weights"])
        trades = []
        for i in range(200):
            t = trade(mfe=200.0 if i < 20 else 100.0, entry=100.0, atr=5.0,
                      methoden={"atr": 110.0, "struktur": 110.0})
            t["stop_atr"] = 1.5
            trades.append(t)
        learning.update_method_weights(w, trades, kalibrierung(0.40))
        self.assertEqual(w["target_method_weights"], vorher)

    def test_ohne_kalibrierung_wird_nicht_geraten(self):
        w = learning.default_weights()
        vorher = dict(w["target_method_weights"])
        learning.update_method_weights(w, self._trades(0.90, 0.80), cal={})
        self.assertEqual(w["target_method_weights"], vorher)


class TestProtokollFuerDieSeite(unittest.TestCase):
    """Was weights.json enthalten muss, damit die Seite eine Kurve zeigen kann.

    Ohne diese Felder liesse sich zwar sagen, wo ein Gewicht heute steht,
    aber nicht, ob es dorthin gelernt wurde oder immer schon dort lag.
    """

    def _gelernt(self):
        w = learning.default_weights()
        learning.update(w, [trade(komponenten={"trend": i / 60.0,
                                               "setup": 1 - i / 60.0},
                                  r=2.0 if i > 30 else -1.0)
                            for i in range(60)], HEUTE, calibration.get())
        return w

    def test_startwerte_bleiben_stehen(self):
        w = self._gelernt()
        self.assertEqual(w["start"]["score_weights"], dict(config.SCORE_WEIGHTS))
        self.assertNotEqual(w["score_weights"], w["start"]["score_weights"])

    def test_jeder_eintrag_traegt_den_stand_danach(self):
        w = self._gelernt()
        self.assertTrue(w["history"], "kein Lernschritt protokolliert")
        letzter = w["history"][-1]
        self.assertEqual(letzter["score_weights"],
                         {k: round(v, 4)
                          for k, v in w["score_weights"].items()})
        self.assertEqual(set(letzter["target_method_weights"]),
                         set(config.TARGET_METHOD_WEIGHTS))

    def test_protokoll_nennt_klartextnamen(self):
        w = self._gelernt()
        zeilen = " ".join(z for e in w["history"] for z in e["changes"])
        self.assertIn(config.SCORE_LABELS["trend"], zeilen)
        self.assertNotIn("-> Gewicht", zeilen)   # der Pfeil ist ein Pfeil

    def test_wartende_schleife_zaehlt_die_trades_trotzdem(self):
        """Sonst behauptete die Seite bis zum zwanzigsten Trade, es gebe keine."""
        w = learning.default_weights()
        learning.update(w, [trade() for _ in range(7)], HEUTE)
        self.assertEqual(w["trades_seen"], 7)
        self.assertEqual(w["history"], [])

    def test_regeln_und_namen_kommen_beim_schreiben_aus_config(self):
        """Sie duerfen nicht aus der alten Datei fortgeschrieben werden —
        sonst zeigte die Seite Grenzen an, die im Code laengst andere sind."""
        import json
        import tempfile
        w = learning.default_weights()
        w["regeln"] = {"min_trades": 999}          # veralteter Stand
        w["labels"] = {"score": {"trend": "veraltet"}}
        alt = config.DATA_DIR
        try:
            with tempfile.TemporaryDirectory() as ordner:
                config.DATA_DIR = pathlib.Path(ordner)
                learning.save(w)
                d = json.loads((config.DATA_DIR / "weights.json")
                               .read_text(encoding="utf-8"))
        finally:
            config.DATA_DIR = alt
        self.assertEqual(d["regeln"]["min_trades"], config.LEARN_MIN_TRADES)
        self.assertEqual(d["labels"]["score"], dict(config.SCORE_LABELS))
        self.assertEqual(set(d["labels"]["methode"]),
                         set(config.TARGET_METHOD_WEIGHTS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
