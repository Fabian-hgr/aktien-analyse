"""Push und Aus-Schalter.

Der wichtigste Test hier: die Pause muss den Cache von ntfy ueberleben.
ntfy haelt Nachrichten nur rund zwoelf Stunden vor — laege der Zustand nur
dort, liefe das System nach einem Wochenende von selbst wieder an.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, notify                    # noqa: E402


class TestSteuerung(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._alt = config.DATA_DIR
        config.DATA_DIR = Path(self._tmp.name)

    def tearDown(self):
        config.DATA_DIR = self._alt
        self._tmp.cleanup()

    def test_standard_ist_laufend(self):
        self.assertFalse(notify.load_control()["paused"])

    def test_zustand_ueberlebt_im_repo(self):
        """Genau das, was ntfys 12-Stunden-Cache nicht kann."""
        notify.save_control({"paused": True, "changed_at": "2026-08-19T12:00:00",
                             "changed_by": "Seite"})
        self.assertTrue(notify.load_control()["paused"])

    def test_kaputte_datei_wird_als_laufend_gelesen(self):
        (config.DATA_DIR / "control.json").write_text("{kaputt", encoding="utf-8")
        self.assertFalse(notify.load_control()["paused"])

    def test_pause_befehl_wird_uebernommen(self):
        notify.poll_control_topic = lambda topic=None, since="48h": "PAUSE"
        state = notify.apply_control("egal")
        self.assertTrue(state["paused"])
        self.assertEqual(state["changed_by"], "Seite")
        self.assertTrue((config.DATA_DIR / "control.json").exists())

    def test_resume_hebt_die_pause_auf(self):
        notify.save_control({"paused": True})
        notify.poll_control_topic = lambda topic=None, since="48h": "RESUME"
        self.assertFalse(notify.apply_control("egal")["paused"])

    def test_ohne_befehl_bleibt_alles_wie_es_war(self):
        notify.save_control({"paused": True, "changed_by": "Seite"})
        notify.poll_control_topic = lambda topic=None, since="48h": None
        self.assertTrue(notify.apply_control("egal")["paused"])

    def test_unbekannter_text_ist_kein_befehl(self):
        notify.poll_control_topic = notify.__dict__["poll_control_topic"]
        # BEFEHLE enthaelt nur die vier bekannten Woerter
        self.assertNotIn("STOPP ALLES", notify.BEFEHLE)
        self.assertIn("PAUSE", notify.BEFEHLE)
        self.assertIn("RESUME", notify.BEFEHLE)


class TestNachrichtenaufbau(unittest.TestCase):

    def _idee(self, symbol="NU", these=""):
        return {
            "symbol": symbol, "sector": "Finanzen",
            "targets": {"price": 14.58, "target": 16.52, "upside_pct": 13.3,
                        "stop": 13.90, "reward_risk": 2.85},
            "llm": {"these": these} if these else None,
        }

    def test_titel_nennt_datum_und_anzahl(self):
        titel, _ = notify.format_morning(
            dt.date(2026, 8, 20), [self._idee(), self._idee("MU")],
            None, {})
        self.assertIn("20.08.", titel)
        self.assertIn("2 neue Ideen", titel)

    def test_einzahl_bei_einer_idee(self):
        titel, _ = notify.format_morning(dt.date(2026, 8, 20), [self._idee()],
                                         None, {})
        self.assertIn("1 neue Idee", titel)
        self.assertNotIn("Ideen", titel)

    def test_text_enthaelt_kurs_ziel_stop_crv(self):
        _, text = notify.format_morning(dt.date(2026, 8, 20), [self._idee()],
                                        None, {})
        for teil in ("NU", "14.58", "16.52", "13.90", "2.9"):
            self.assertIn(teil, text)

    def test_these_wird_uebernommen(self):
        _, text = notify.format_morning(
            dt.date(2026, 8, 20),
            [self._idee(these="Starkes Wachstum in Lateinamerika.")], None, {})
        self.assertIn("Lateinamerika", text)

    def test_ohne_ideen_wird_das_gesagt(self):
        titel, text = notify.format_morning(dt.date(2026, 8, 20), [], None, {})
        self.assertIn("0 neue Ideen", titel)
        self.assertIn("keine Idee", text)

    def test_marktlage_und_depotstand(self):
        _, text = notify.format_morning(
            dt.date(2026, 8, 20), [self._idee()],
            {"zusammenfassung": "Ruhiger Handel.", "punkte": ["Fed haelt still"]},
            {"ki": {"return_pct": 3.2, "trades": 40, "win_rate": 45.0},
             "zufall": {"return_pct": 1.1}})
        self.assertIn("Ruhiger Handel", text)
        self.assertIn("Fed haelt still", text)
        self.assertIn("+3.20", text)
        self.assertIn("+1.10", text)

    def test_schlagzeilen_ersetzen_die_zusammenfassung(self):
        """Faellt Ollama aus, muessen die Nachrichten trotzdem ankommen.

        Sie sind der Kern der Morgenmeldung — sie duerfen nicht mit dem
        Sprachmodell zusammen ausfallen.
        """
        _, text = notify.format_morning(
            dt.date(2026, 8, 20), [self._idee()], None, {},
            headlines=[{"headline": "Fed senkt den Leitzins",
                        "source": "CNBC"}])
        self.assertIn("Fed senkt den Leitzins", text)
        self.assertIn("CNBC", text)

    def test_zusammenfassung_hat_vorrang_vor_schlagzeilen(self):
        _, text = notify.format_morning(
            dt.date(2026, 8, 20), [self._idee()],
            {"zusammenfassung": "Ruhiger Handel."}, {},
            headlines=[{"headline": "Fed senkt den Leitzins",
                        "source": "CNBC"}])
        self.assertIn("Ruhiger Handel", text)
        self.assertNotIn("Fed senkt", text)

    def test_dashboard_link(self):
        _, text = notify.format_morning(dt.date(2026, 8, 20), [self._idee()],
                                        None, {}, "https://example.github.io/x")
        self.assertIn("https://example.github.io/x", text)


class TestAusfallverhalten(unittest.TestCase):

    def test_ohne_thema_wird_nicht_gesendet(self):
        alt = config.NTFY_TOPIC
        config.NTFY_TOPIC = ""
        try:
            self.assertFalse(notify.send("Titel", "Text"))
        finally:
            config.NTFY_TOPIC = alt

    def test_ohne_steuerthema_kein_abruf(self):
        alt = config.NTFY_CONTROL_TOPIC
        config.NTFY_CONTROL_TOPIC = ""
        try:
            self.assertIsNone(notify.poll_control_topic())
        finally:
            config.NTFY_CONTROL_TOPIC = alt


if __name__ == "__main__":
    unittest.main(verbosity=2)
