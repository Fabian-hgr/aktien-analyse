"""Etappe 0 — messen statt schaetzen, bevor etwas darauf aufbaut.

Beantwortet vier Fragen auf dem echten GitHub-Runner:

  1  Antwortet Ollama dort, und wie schnell (Tokens je Sekunde)?
  2  Kommt bei einer echten Nachrichten-Aufgabe gueltiges JSON zurueck?
  3  Wie lange braeuchte ein Lauf ueber 25 Titel — passt er ins Budget?
  4  Erreichen wir Alpaca und Yahoo aus der Cloud?

    python scripts/machbarkeit.py               # alles
    python scripts/machbarkeit.py --nur-daten   # nur die Datenquellen
    python scripts/machbarkeit.py --nur-modell  # nur das Sprachmodell

Rueckgabewert 1, wenn eine Bedingung reisst. Der Workflow schlaegt dann fehl —
und zwar bevor irgendetwas Groesseres darauf gebaut wurde.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import alpaca, config, llm, news, yahoo          # noqa: E402

# Das eigentliche Tor ist die LAUFZEIT. Die Tokenrate ist nur ein Symptom:
# gemessen am 22.08.2026 lieferte qwen2.5:7b auf dem Runner 3.8 Tokens/s und
# brauchte 27 s je Titel — hochgerechnet 11.3 Minuten fuer 25 Titel, also ein
# Viertel des Budgets. Eine Tokenschwelle von 4.0 haette dieses vollauf
# brauchbare Ergebnis verworfen. Sie steht jetzt so tief, dass sie nur noch
# echten Stillstand abfaengt.
BUDGET_MINUTEN = 50          # so lange darf der Sprachmodell-Teil hoechstens
MIND_TOKENS_PRO_S = 1.5      # darunter steht der Runner praktisch still
WARNUNG_TOKENS_PRO_S = 4.0   # darunter wird es eng, aber es reisst nichts

BEISPIELE = [
    ("NVDA", "NVIDIA", "Technologie", [
        "NVIDIA meldet Rekordumsatz im Rechenzentrumsgeschaeft",
        "Analysten heben Kursziel nach starkem Ausblick an",
        "Lieferengpaesse bei fortschrittlichen Verpackungstechnologien halten an",
    ]),
    ("XOM", "Exxon Mobil", "Energie", [
        "Oelpreis faellt nach Foerderausweitung der OPEC",
        "Exxon kuendigt Aktienrueckkauf ueber 20 Milliarden USD an",
    ]),
    ("KO", "Coca-Cola", "Konsum defensiv", [
        "Coca-Cola erhoeht Preise, Absatzmenge stagniert",
        "Waehrungseffekte belasten das Auslandsgeschaeft",
    ]),
]


def pruefe_modell(titel: int) -> bool:
    print("=" * 70)
    print("SPRACHMODELL")
    print("=" * 70)
    client = llm.Ollama()
    if not client.probe():
        print(f"FEHLER: Ollama unter {config.OLLAMA_URL} nicht erreichbar.")
        return False
    print(f"Modell: {config.OLLAMA_MODEL}")

    eintraege = [{"symbol": s, "name": n, "sector": b}
                 for s, n, b, _ in BEISPIELE]
    nachrichten = {s: [{"headline": h, "summary": ""} for h in schlagzeilen]
                   for s, _, _, schlagzeilen in BEISPIELE}

    begonnen = time.time()
    ergebnis = llm.analyse_symbols(client, eintraege, nachrichten)
    dauer = time.time() - begonnen

    for sym, r in ergebnis.items():
        print(f"\n  {sym}  Sentiment {r['sentiment']:+.2f}")
        print(f"     These: {r['these']}")
        print(f"     Katalysatoren: {r.get('katalysatoren')}")
        print(f"     Risiken: {r.get('risiken')}")

    fehlend = [s for s, _, _, _ in BEISPIELE if s not in ergebnis]
    tps = client.tokens_per_second()
    je_titel = dauer / max(1, len(BEISPIELE))
    hochrechnung = je_titel * titel / 60

    print()
    print(f"Gueltige Antworten: {len(ergebnis)} von {len(BEISPIELE)}"
          + (f"   fehlend: {fehlend}" if fehlend else ""))
    print(f"Durchsatz:          {tps:.1f} Tokens/s")
    print(f"Je Titel:           {je_titel:.1f} s")
    print(f"Hochrechnung fuer {titel} Titel: {hochrechnung:.1f} min "
          f"(Budget {BUDGET_MINUTEN} min)")
    print(f"Fehler des Clients: {client.stats}")

    ok = True
    if len(ergebnis) < len(BEISPIELE):
        print("REISST: nicht jede Aufgabe lieferte gueltiges JSON.")
        ok = False
    if tps < MIND_TOKENS_PRO_S:
        print(f"REISST: unter {MIND_TOKENS_PRO_S} Tokens/s — der Runner kommt "
              f"nicht vom Fleck, kleineres Modell waehlen (qwen2.5:3b).")
        ok = False
    elif tps < WARNUNG_TOKENS_PRO_S:
        print(f"Hinweis: {tps:.1f} Tokens/s ist langsam. Solange die "
              f"Hochrechnung im Budget bleibt, ist das kein Problem — bei "
              f"laengerer Vorauswahl aber die erste Stellschraube.")
    if hochrechnung > BUDGET_MINUTEN:
        print(f"REISST: {hochrechnung:.0f} min ueber dem Budget — kleineres "
              f"Modell oder kuerzere Vorauswahl.")
        ok = False

    marktlage = llm.market_digest(client, [h for _, _, _, hs in BEISPIELE
                                           for h in hs])
    print(f"\nMarktzusammenfassung: "
          f"{(marktlage or {}).get('zusammenfassung') or 'KEINE'}")
    if not marktlage:
        print("REISST: keine brauchbare Marktzusammenfassung.")
        ok = False
    return ok


def pruefe_daten() -> bool:
    print("=" * 70)
    print("DATENQUELLEN AUS DER CLOUD")
    print("=" * 70)
    config.load_local_secrets()
    ok = True

    try:
        uhr = alpaca.clock()
        print(f"Alpaca-Uhr        offen={uhr.get('is_open')}  "
              f"naechste Oeffnung {uhr.get('next_open')}")
    except Exception as e:                                   # noqa: BLE001
        print(f"REISST: Alpaca-Uhr nicht erreichbar: {type(e).__name__}")
        return False

    t = time.time()
    bars = alpaca.daily_bars(["AAPL", "MSFT", "SPY"],
                             dt.date.today() - dt.timedelta(days=60))
    print(f"Alpaca-Bars       {len(bars)} Symbole, AAPL {len(bars.get('AAPL', []))} "
          f"Tage, {time.time() - t:.1f} s")
    if len(bars) < 3:
        print("REISST: nicht alle Symbole geliefert.")
        ok = False

    f = yahoo.fundamentals("AAPL", max_age_days=0)
    if f:
        print(f"Yahoo             Sektor {f.get('sector')}, Analystenziel "
              f"{f.get('target_mean')} aus {f.get('analyst_count')} Schaetzungen")
    else:
        print("WARNUNG: Yahoo lieferte nichts. Das System laeuft ohne "
              "Fundamentaldaten weiter, die Karten sind dann duenner.")

    schlagzeilen = news.market_headlines()
    quellen = sorted({h["source"] for h in schlagzeilen})
    print(f"Nachrichten       {len(schlagzeilen)} Schlagzeilen aus {quellen}")
    if not schlagzeilen:
        print("REISST: keine einzige Nachrichtenquelle antwortete.")
        ok = False
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--titel", type=int, default=config.SHORTLIST_SIZE)
    ap.add_argument("--nur-daten", action="store_true")
    ap.add_argument("--nur-modell", action="store_true")
    ap.add_argument("--json", type=str, default="")
    args = ap.parse_args()

    ok = True
    if not args.nur_daten:
        ok = pruefe_modell(args.titel) and ok
        print()
    if not args.nur_modell:
        ok = pruefe_daten() and ok

    print()
    print("ERGEBNIS:", "alle Bedingungen erfuellt" if ok else "MINDESTENS EINE BEDINGUNG REISST")
    if args.json:
        Path(args.json).write_text(json.dumps({"ok": ok}, indent=1),
                                   encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
