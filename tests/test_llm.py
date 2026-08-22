"""Robustheit gegen unsaubere Modellantworten.

Sprachmodelle liefern regelmaessig JSON mit Codeblock-Zaeunen, Vorrede oder
Nachgeplapper. Der Lauf darf daran nicht scheitern — und er darf erst recht
nichts erfinden, wenn die Antwort unbrauchbar ist.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import llm                                # noqa: E402


class TestJsonParsen(unittest.TestCase):

    def test_sauberes_json(self):
        self.assertEqual(llm.parse_json('{"a": 1}'), {"a": 1})

    def test_codeblock_zaun(self):
        self.assertEqual(
            llm.parse_json('```json\n{"sentiment": 0.5}\n```'),
            {"sentiment": 0.5})

    def test_vorrede_und_nachrede(self):
        text = 'Gerne! Hier das Ergebnis:\n{"sentiment": -0.2}\nHoffe das hilft.'
        self.assertEqual(llm.parse_json(text), {"sentiment": -0.2})

    def test_verschachteltes_objekt(self):
        text = 'Antwort: {"a": {"b": 2}, "c": [1,2]} — fertig'
        self.assertEqual(llm.parse_json(text), {"a": {"b": 2}, "c": [1, 2]})

    def test_abgeschnittenes_json_gibt_none(self):
        self.assertIsNone(llm.parse_json('{"sentiment": 0.5, "these": "ab'))

    def test_liste_statt_objekt_gibt_none(self):
        self.assertIsNone(llm.parse_json('[1, 2, 3]'))

    def test_leer_und_muell(self):
        self.assertIsNone(llm.parse_json(""))
        self.assertIsNone(llm.parse_json("Kein JSON hier."))
        self.assertIsNone(llm.parse_json(None))


class TestNormalisieren(unittest.TestCase):

    def test_vollstaendige_antwort(self):
        r = llm.normalise_analysis({
            "sentiment": 0.65,
            "these": "Starke Nachfrage nach Rechenzentrums-Chips.",
            "katalysatoren": ["Neuer Grossauftrag", "Analysten-Hochstufung"],
            "risiken": ["Bewertung hoch"],
        }, news_count=4)
        self.assertAlmostEqual(r["sentiment"], 0.65)
        self.assertEqual(len(r["katalysatoren"]), 2)
        self.assertEqual(r["news_count"], 4)

    def test_sentiment_wird_gekappt(self):
        self.assertEqual(llm.normalise_analysis({"sentiment": 5}, 1)["sentiment"], 1.0)
        self.assertEqual(llm.normalise_analysis({"sentiment": -9}, 1)["sentiment"], -1.0)

    def test_sentiment_als_text(self):
        self.assertAlmostEqual(
            llm.normalise_analysis({"sentiment": "0.4"}, 1)["sentiment"], 0.4)

    def test_ohne_sentiment_unbrauchbar(self):
        self.assertIsNone(llm.normalise_analysis({"these": "Nur Text"}, 1))
        self.assertIsNone(llm.normalise_analysis({"sentiment": "positiv"}, 1))
        self.assertIsNone(llm.normalise_analysis(None, 1))

    def test_listen_werden_begrenzt(self):
        r = llm.normalise_analysis({
            "sentiment": 0.0,
            "katalysatoren": ["a", "b", "c", "d", "e"],
        }, 1)
        self.assertEqual(len(r["katalysatoren"]), 3)

    def test_einzelner_string_statt_liste(self):
        r = llm.normalise_analysis({"sentiment": 0.0, "risiken": "Nur eines"}, 1)
        self.assertEqual(r["risiken"], ["Nur eines"])

    def test_zu_langer_text_wird_gekuerzt(self):
        r = llm.normalise_analysis({"sentiment": 0.0, "these": "x" * 500}, 1)
        self.assertLessEqual(len(r["these"]), 220)


class TestAusfallverhalten(unittest.TestCase):

    def test_ohne_ollama_keine_ergebnisse(self):
        client = llm.Ollama(url="http://127.0.0.1:1")     # garantiert tot
        client.available = False
        out = llm.analyse_symbols(client, [{"symbol": "AAPL"}],
                                  {"AAPL": [{"headline": "Test"}]})
        self.assertEqual(out, {})

    def test_probe_auf_totem_port_meldet_nicht_verfuegbar(self):
        client = llm.Ollama(url="http://127.0.0.1:1")
        self.assertFalse(client.probe())
        self.assertFalse(client.available)

    def test_titel_ohne_nachrichten_werden_uebersprungen(self):
        """Ohne Grundlage wird nichts erfunden."""
        class FakeOllama(llm.Ollama):
            def __init__(self):
                super().__init__()
                self.available = True
                self.gefragt = []

            def generate_json(self, prompt, num_predict=None):
                self.gefragt.append(prompt)
                return {"sentiment": 0.5, "these": "Test"}

        client = FakeOllama()
        out = llm.analyse_symbols(
            client,
            [{"symbol": "AAPL", "name": "Apple"},
             {"symbol": "LEER", "name": "Ohne News"}],
            {"AAPL": [{"headline": "Apple meldet Rekordquartal"}]},
        )
        self.assertIn("AAPL", out)
        self.assertNotIn("LEER", out)
        self.assertEqual(len(client.gefragt), 1)

    def test_kaputte_antwort_erzeugt_keinen_eintrag(self):
        class KaputtOllama(llm.Ollama):
            def __init__(self):
                super().__init__()
                self.available = True

            def generate_json(self, prompt, num_predict=None):
                return None

        out = llm.analyse_symbols(KaputtOllama(), [{"symbol": "AAPL"}],
                                  {"AAPL": [{"headline": "Irgendwas"}]})
        self.assertEqual(out, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
