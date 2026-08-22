"""Alle Stellschrauben an einem Ort.

Geheimnisse stehen NIE hier drin. Sie kommen aus Umgebungsvariablen
(lokal aus einer .env-artigen Datei, in der Cloud aus GitHub Secrets).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ── Pfade ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
# Alles, was das System weiss, liegt unter docs/data — versioniert im Repo und
# gleichzeitig das, was die Seite ausliest. Kein getrennter Zwischenspeicher:
# ein Zustand, der nicht im Git-Verlauf steht, ist spaeter nicht mehr
# nachvollziehbar.
DATA_DIR = ROOT / "docs" / "data"
ARCHIVE_DIR = DATA_DIR / "archive"

# ── Zugangsdaten (nur aus der Umgebung) ────────────────────────────────────
ALPACA_KEY = os.environ.get("ALPACA_KEY", "")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET", "")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")
NTFY_CONTROL_TOPIC = os.environ.get("NTFY_CONTROL_TOPIC", "")

ALPACA_TRADING = "https://paper-api.alpaca.markets/v2"
ALPACA_DATA = "https://data.alpaca.markets"
ALPACA_FEED = "iex"          # Gratis-Tarif: IEX statt SIP

# ── Universum ──────────────────────────────────────────────────────────────
UNIVERSE_SIZE = 550          # S&P 500 + Nasdaq 100 (Überschneidung abgezogen)
MIN_PRICE = 5.0              # harter Ausschluss
# Achtung: Der Gratis-Tarif liefert nur den IEX-Feed. Dessen Volumen ist rund
# 3-5 % des konsolidierten US-Volumens. Die Schwelle ist deshalb in IEX-Dollar
# angegeben. 3 Mio IEX entspricht grob 75-150 Mio Gesamtmarkt und behaelt 498
# von 501 S&P-500-Mitgliedern (gemessen am 2026-08-19).
MIN_DOLLAR_VOLUME = 3_000_000
UNIVERSE_MAX_AGE_DAYS = 7    # danach neu aufbauen
BENCHMARK = "SPY"

# ── Analyse ────────────────────────────────────────────────────────────────
HISTORY_DAYS = 400           # Kalendertage Bar-Historie (≈ 275 Handelstage)
HORIZON_DAYS = 15            # Handelstage bis zum Kursziel
MAX_HOLD_DAYS = 20           # Zwangsausstieg
SHORTLIST_SIZE = 25          # so viele Titel gehen ans Sprachmodell
PICKS_PER_DAY = 6            # Käufe pro Tag und Depot

# Hoechstens drei Kaeufe je Branche und Tag, also die Haelfte. Ohne diese Regel bestand die
# Vorauswahl am 2026-08-19 zu 14 von 25 aus Technologie — drei Halbleiter an
# einem Tag waeren eine Sektorwette, keine Aktienauswahl. Das Zufallsdepot
# streut von sich aus ueber alle Branchen; ohne Deckel waere der Vergleich
# verzerrt.
MAX_PICKS_PER_SECTOR = 3

ATR_PERIOD = 14
# Nur noch Rueckfallwerte. Im Normalbetrieb kommen Ziel- und Stop-Faktor aus
# docs/data/calibration.json, also aus der gemessenen Bewegung des ganzen
# Universums (Median aufwaerts 2.02 ATR, abwaerts 1.65 ATR in 15 Handelstagen,
# 212'871 Beobachtungen). Diese Werte greifen nur, wenn keine Kalibrierung
# vorliegt.
ATR_TARGET_MULT = 2.0
ATR_STOP_MULT = 1.65

# Gelernt wird nur ein Multiplikator auf den kalibrierten Zielfaktor, nie der
# Faktor selbst und nie der Stop: aus den eigenen Trades gelernt wuerde sich
# der Stop selbst nach unten ziehen (ein enger Stop schneidet jede Messung der
# Bewegung ab, was den naechsten Stop noch enger macht).
SECTOR_K_MULT_MIN, SECTOR_K_MULT_MAX = 0.7, 1.4

# Kursziel-Methoden, deren Gewichte gelernt werden. Nur diese beiden liefern
# ein Niveau; Analysten und Bewertung wirken als Neigung (siehe unten).
TARGET_METHOD_WEIGHTS = {"atr": 1.0, "struktur": 1.0}

# Klartextnamen an einer Stelle. Sie stehen sonst dreimal: in targets.py, in
# der Lernschleife und auf der Seite — und liefen dann auseinander.
TARGET_METHOD_LABELS = {
    "atr": "ATR-Projektion",
    "struktur": "Struktur / gemessene Bewegung",
    "analysten": "Analystenkonsens",
    "bewertung": "Bewertungsanker",
}

# Die Neigungen wirken auf den ABSTAND zum Kurs, nicht auf das Kursniveau.
# Der Unterschied ist gross: 3 % vom Kurs sind bei Apple 0.4 ATR, bei einer
# ruhigen Aktie wie XOM aber 1.5 ATR. Am Kursniveau angesetzt haette dieselbe
# Neigung also je nach Volatilitaet voellig verschiedene Wirkung - und bei
# ruhigen Titeln das Ziel ins Unerreichbare geschoben (gemessen am 21.08.2026:
# XOM sprang von 2.75 auf 4.33 ATR, die Trefferquote fiel von 39 % auf 18 %).
#
# Am Abstand angesetzt ist die Wirkung einheitenrein: beide Neigungen zusammen
# verschieben das Ziel um hoechstens +/- 18 % des Abstands.
TILT_STRENGTH_ANALYST = 0.30
TILT_STRENGTH_VALUATION = 0.30
TILT_CAP = 0.30

# Ein Widerstand innerhalb einer ATR ist kein Widerstand, sondern Rauschen.
STRUCTURE_MIN_ATR_DISTANCE = 1.0

# Drei Tore fuer eine Idee. Das Chance-Risiko-Verhaeltnis ist davon das
# schwaechste: es sagt nichts, solange man die Trefferwahrscheinlichkeit nicht
# kennt. Bei den kalibrierten Marken liegt es bauartbedingt bei rund 1.2 (Ziel
# und Stop stehen beide auf dem Median ihrer Richtung) - eine Huerde von 1.5
# haette deshalb fast jede Idee verworfen. Die beiden anderen Tore sind
# gemessen und tragen die eigentliche Last:
#
#   MIN_HIT_PROBABILITY     Wie oft wurde ein Ziel in dieser Entfernung
#                           historisch ueberhaupt beruehrt. Verwirft Ziele,
#                           die die Strukturmethode unerreichbar weit setzt.
#   MIN_BASE_EXPECTANCY_R   Erwartungswert dieser Marken-Geometrie fuer eine
#                           ZUFALLSAUSWAHL. Ist er schon negativ, kann auch
#                           eine gute Auswahl das kaum aufholen.
MIN_REWARD_RISK = 1.0
MIN_HIT_PROBABILITY = 0.25
MIN_BASE_EXPECTANCY_R = 0.0

# Deckel fuer die Strukturmethode. Frueher pauschal 6 ATR - gemessen wird ein
# solches Niveau aber nur in 6 % der Faelle beruehrt. Der Deckel ist jetzt das
# Niveau, das historisch noch in 30 % der Faelle erreicht wurde (branchenweise
# rund 3.1 ATR). Damit kann die Strukturmethode das Ziel weiterhin ueber die
# ATR-Projektion hinausschieben, aber nie ins Unerreichbare.
STRUCTURE_CAP_PROBABILITY = 0.30
STRUCTURE_CAP_ATR_FALLBACK = 3.5    # ohne Kalibrierung

# ── Score-Gewichte (Startwerte, werden gelernt) ────────────────────────────
SCORE_WEIGHTS = {
    "trend": 0.20,
    "setup": 0.15,
    "volumen": 0.10,
    "qualitaet": 0.15,
    "bewertung": 0.10,
    "analysten": 0.15,
    "sentiment": 0.15,
}

# Dieselbe Ueberlegung wie bei den Methodennamen: ein Ort, drei Verwender.
SCORE_LABELS = {
    "trend": "Trend & relative Stärke",
    "setup": "Setup-Qualität",
    "volumen": "Volumenbestätigung",
    "qualitaet": "Fundamentale Qualität",
    "bewertung": "Bewertung vs. Branche",
    "analysten": "Analysten-Rückenwind",
    "sentiment": "News-Sentiment",
}

PENALTY_EARNINGS_SOON = 0.25   # Earnings in <= 5 Handelstagen
PENALTY_HIGH_BETA = 0.05       # Beta > 2
EARNINGS_BLACKOUT_DAYS = 5
HIGH_BETA_THRESHOLD = 2.0

# ── Depots ─────────────────────────────────────────────────────────────────
START_CAPITAL = 100_000.0

# Positionsgroesse und Deckel haengen an PICKS_PER_DAY und muessen bei jeder
# Aenderung nachgerechnet werden.
#
# Bei 3 Kaeufen taeglich und 6.8 Tagen gemessener Haltedauer waren im Backtest
# im Median 17 Positionen offen, bei 2 % je Position also 34 % investiert.
# Sechs Kaeufe taeglich verdoppeln die Belegung auf rund 34. Bliebe es bei
# 2 %, waere das Depot ploetzlich zu 68 % investiert — der Vergleich mit dem
# frueheren Aufbau waere dann ein Vergleich des Kapitaleinsatzes und nicht
# der Auswahl. Deshalb 1 %: mehr Positionen, gleiche Gesamtausrichtung, nur
# breiter gestreut.
#
# Der Deckel steht bei 80, damit er im Normalbetrieb NICHT greift. Ein
# bindender Deckel wuerde die Kaeufe genau dann abschneiden, wenn viele Ideen
# vorliegen — und damit heimlich mitauswaehlen.
POSITION_PCT = 0.01
MAX_CONCURRENT_POSITIONS = 80
SLIPPAGE_BPS = 5             # 5 Basispunkte je Seite

# ── Lernschleife ───────────────────────────────────────────────────────────
LEARN_MIN_TRADES = 20        # vorher wird nichts angepasst
LEARN_RATE = 0.15            # eta
LEARN_WINDOW = 100           # Trades im rollenden Fenster
WEIGHT_MIN, WEIGHT_MAX = 0.2, 2.0
MULT_MIN, MULT_MAX = 0.5, 1.5

# ── Sprachmodell ───────────────────────────────────────────────────────────
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b-instruct-q4_K_M")
OLLAMA_TIMEOUT = 180.0
OLLAMA_NUM_PREDICT = 220

# ── HTTP ───────────────────────────────────────────────────────────────────
HTTP_TIMEOUT = 30.0
HTTP_RETRIES = 3
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AktienAnalyse/1.0"


def konsole_utf8() -> None:
    """Ausgabe auf UTF-8 stellen, bevor irgendetwas geschrieben wird.

    Die Runner in der Cloud sind UTF-8; eine Windows-Konsole ist es nicht.
    Dort bricht schon ein einzelner Pfeil im Lernprotokoll den ganzen Lauf
    mit einem UnicodeEncodeError ab — und zwar erst am Ende, nachdem alles
    gerechnet, aber noch nichts geschrieben ist. Gemessen am 22.08.2026:
    ein 47-Sekunden-Backtest ging so vollstaendig verloren.
    """
    for strom in (sys.stdout, sys.stderr):
        if strom is not None and hasattr(strom, "reconfigure"):
            try:
                strom.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def load_local_secrets() -> None:
    """Lokale Entwicklung: Schlüssel aus dem Trading-Bot-Ordner nachladen.

    In der Cloud passiert hier nichts — dort sind die Variablen schon gesetzt.
    Die Datei wird nie ins Repo kopiert.
    """
    global ALPACA_KEY, ALPACA_SECRET
    if ALPACA_KEY and ALPACA_SECRET:
        return
    candidate = Path.home() / "Desktop" / "Trading Bot" / ".alpaca_credentials"
    if not candidate.exists():
        return
    for line in candidate.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if k == "ALPACA_KEY" and not ALPACA_KEY:
            ALPACA_KEY = v
            os.environ["ALPACA_KEY"] = v
        elif k == "ALPACA_SECRET" and not ALPACA_SECRET:
            ALPACA_SECRET = v
            os.environ["ALPACA_SECRET"] = v
