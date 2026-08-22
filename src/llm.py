"""Das Sprachmodell — Ollama, lokal oder auf dem Cloud-Runner.

Was es tut: Nachrichten lesen und daraus Stimmung, These, Katalysatoren und
Risiken auf Deutsch formulieren.

Was es ausdruecklich NICHT tut: Kursziele rechnen. Sprachmodelle sind bei
Zahlen unzuverlaessig; die Ziele kommen aus targets.py. Das Modell liefert
nur eine von sieben Score-Komponenten und den lesbaren Text auf der Karte.

Faellt Ollama aus, laeuft der ganze Lauf ohne Sentiment weiter. Die Karten
werden dann entsprechend markiert — es wird nichts erfunden.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

import requests

from . import config, net

log = logging.getLogger(__name__)

# Die Aufgabenstellung ist selbst in richtigem Deutsch geschrieben — nicht
# aus Ordnungsliebe: am 22.08.2026 antwortete das Modell auf den frueheren,
# in ASCII-Umschrift verfassten Text mit "Oelpreis", "Aktienrueckkauf" und
# "waehrungseffektiv". Ein Modell schreibt in der Schreibweise, die es
# vorgesetzt bekommt, und diese Saetze landen unveraendert auf der Seite.
SCHREIBWEISE = """- Schreibe richtiges Deutsch mit den Umlauten ä ö ü \
und dem grossen ÄÖÜ, nie in Umschrift als ae/oe/ue.
- Schweizer Schreibweise: immer "ss", niemals das Zeichen ß."""

ANALYSE_PROMPT = """Du bist ein nüchterner Finanzanalyst. Beurteile die \
Nachrichtenlage zu {symbol} ({name}, Branche {sector}) für die \
nächsten {horizon} Handelstage.

Nachrichten der letzten Stunden:
{news}

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt in genau dieser Form:
{{"sentiment": <Zahl zwischen -1 und 1>,
  "these": "<ein Satz auf Deutsch, höchstens 25 Wörter>",
  "katalysatoren": ["<kurzer Punkt>", "..."],
  "risiken": ["<kurzer Punkt>", "..."]}}

Regeln:
- sentiment: -1 sehr negativ, 0 neutral, +1 sehr positiv.
- Erfinde nichts. Stütze dich nur auf die genannten Nachrichten.
- Nenne keine Kursziele und keine Kurse.
- Höchstens drei Katalysatoren und drei Risiken.
{schreibweise}"""

DIGEST_PROMPT = """Du bist Finanzredaktor. Fasse die wichtigsten \
Marktnachrichten für heute Morgen zusammen.

Schlagzeilen:
{news}

Antworte AUSSCHLIESSLICH mit einem JSON-Objekt:
{{"zusammenfassung": "<zwei bis drei Sätze auf Deutsch>",
  "punkte": ["<Schlagzeile knapp auf Deutsch>", "..."]}}

Höchstens fünf Punkte. Erfinde nichts.
{schreibweise}"""


class Ollama:
    """Duenner Client. Prueft beim Start, ob Modell und Dienst da sind."""

    def __init__(self, url: Optional[str] = None, model: Optional[str] = None,
                 timeout: Optional[float] = None):
        self.url = (url or config.OLLAMA_URL).rstrip("/")
        self.model = model or config.OLLAMA_MODEL
        self.timeout = timeout or config.OLLAMA_TIMEOUT
        self.available = False
        self.installed_models: list[str] = []
        self.stats = {"aufrufe": 0, "fehler": 0, "sekunden": 0.0,
                      "tokens": 0, "reparaturen": 0}

    def probe(self) -> bool:
        """Laeuft Ollama, und ist das Modell geladen?"""
        try:
            r = net.session().get(f"{self.url}/api/tags", timeout=10)
            r.raise_for_status()
            self.installed_models = [m.get("name", "")
                                     for m in r.json().get("models", [])]
        except (requests.RequestException, ValueError) as e:
            log.warning("Ollama nicht erreichbar (%s) — Lauf ohne Sentiment", e)
            self.available = False
            return False

        if any(m == self.model or m.startswith(self.model.split(":")[0] + ":")
               for m in self.installed_models):
            self.available = True
        else:
            log.warning("Modell %s nicht installiert. Vorhanden: %s",
                        self.model, self.installed_models)
            self.available = False
        return self.available

    def generate_json(self, prompt: str,
                      num_predict: Optional[int] = None) -> Optional[dict]:
        """Ein Aufruf, der ein JSON-Objekt erwartet.

        `format: json` zwingt Ollama zu gueltigem JSON — das ist deutlich
        zuverlaessiger als Nachbearbeitung. Der Reparaturweg bleibt trotzdem
        drin, weil aeltere Ollama-Versionen das Feld ignorieren.
        """
        if not self.available:
            return None
        started = time.time()
        self.stats["aufrufe"] += 1
        try:
            r = net.session().post(
                f"{self.url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "keep_alive": "30m",
                    "options": {
                        "temperature": 0.2,
                        "top_p": 0.9,
                        "num_predict": num_predict or config.OLLAMA_NUM_PREDICT,
                    },
                },
                timeout=self.timeout,
            )
            r.raise_for_status()
            payload = r.json()
        except (requests.RequestException, ValueError) as e:
            self.stats["fehler"] += 1
            log.debug("Ollama-Aufruf fehlgeschlagen: %s", e)
            return None
        finally:
            self.stats["sekunden"] += time.time() - started

        self.stats["tokens"] += payload.get("eval_count", 0) or 0
        parsed = parse_json(payload.get("response", ""))
        if parsed is None:
            self.stats["reparaturen"] += 1
        return parsed

    def tokens_per_second(self) -> float:
        s = self.stats["sekunden"]
        return round(self.stats["tokens"] / s, 2) if s > 0 else 0.0


def parse_json(text: str) -> Optional[dict]:
    """JSON aus einer moeglicherweise verrauschten Antwort holen."""
    if not text:
        return None
    text = text.strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    # Codeblock-Zaeune entfernen
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    # Erstes ausbalanciertes Objekt suchen
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def schweizer_schreibweise(text: str) -> str:
    """Das scharfe s gibt es im Schweizer Hochdeutsch nicht.

    Die Regel steht auch im Prompt, aber ein Sprachmodell haelt sich nicht
    zuverlaessig daran — und anders als die Umschrift laesst sich diese eine
    Ersetzung ohne jede Mehrdeutigkeit nachtraeglich machen. Die Umschrift
    (ae/oe/ue) wird bewusst NICHT nachgebessert: "Aussenstaende" waere
    richtig, "Statue" und "Duett" nicht — dieser Unterschied ist ohne
    Woerterbuch nicht zu treffen.
    """
    return text.replace("ß", "ss")


def _clean_list(value, limit: int = 3, max_len: int = 120) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:limit]:
        s = schweizer_schreibweise(str(item).strip())
        if s:
            out.append(s[:max_len])
    return out


def normalise_analysis(raw: Optional[dict], news_count: int) -> Optional[dict]:
    """Modellantwort in eine feste Form bringen. None, wenn unbrauchbar."""
    if not isinstance(raw, dict):
        return None
    s = raw.get("sentiment")
    try:
        sentiment = max(-1.0, min(1.0, float(s)))
    except (TypeError, ValueError):
        return None
    these = schweizer_schreibweise(str(raw.get("these") or "").strip())[:220]
    return {
        "sentiment": round(sentiment, 3),
        "these": these,
        "katalysatoren": _clean_list(raw.get("katalysatoren")),
        "risiken": _clean_list(raw.get("risiken")),
        "news_count": news_count,
    }


def analyse_symbols(client: Ollama, entries: list[dict],
                    news_by_symbol: dict[str, list[dict]]) -> dict[str, dict]:
    """Sentiment und These je Titel. Titel ohne Nachrichten werden
    uebersprungen — ohne Grundlage wird nichts erfunden."""
    out: dict[str, dict] = {}
    if not client.available:
        log.info("Ollama nicht verfuegbar — keine Sentiment-Analyse")
        return out

    for e in entries:
        sym = e["symbol"]
        items = news_by_symbol.get(sym) or []
        if not items:
            continue
        news = "\n".join(
            f"- {it['headline']}" + (f" ({it['summary'][:150]})" if it.get("summary") else "")
            for it in items[:6]
        )
        prompt = ANALYSE_PROMPT.format(
            symbol=sym, name=e.get("name", sym), sector=e.get("sector", "n/a"),
            horizon=config.HORIZON_DAYS, news=news, schreibweise=SCHREIBWEISE,
        )
        result = normalise_analysis(client.generate_json(prompt), len(items))
        if result:
            out[sym] = result
        else:
            log.debug("Keine brauchbare Antwort fuer %s", sym)

    log.info("Sentiment fuer %d von %d Titeln (%.1f Tokens/s, %d Fehler)",
             len(out), len(entries), client.tokens_per_second(),
             client.stats["fehler"])
    return out


def market_digest(client: Ollama, headlines: list[str]) -> Optional[dict]:
    """Kurze Marktzusammenfassung fuer die Push-Nachricht."""
    if not client.available or not headlines:
        return None
    news = "\n".join(f"- {h}" for h in headlines[:20])
    raw = client.generate_json(
        DIGEST_PROMPT.format(news=news, schreibweise=SCHREIBWEISE),
        num_predict=320)
    if not isinstance(raw, dict):
        return None
    text = schweizer_schreibweise(
        str(raw.get("zusammenfassung") or "").strip())[:600]
    return {
        "zusammenfassung": text,
        "punkte": _clean_list(raw.get("punkte"), limit=5, max_len=160),
    } if text else None
