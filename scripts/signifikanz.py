"""Ist der Unterschied zwischen den Depots ueberhaupt belastbar?

Ein Renditeunterschied von ein paar Prozentpunkten sagt nichts, solange man
nicht weiss, wie stark die Einzeltrades streuen. Dieses Skript rechnet den
Standardfehler und den t-Wert aus — und beantwortet damit die einzige Frage,
die im Backtest wirklich zaehlt: koennte das reiner Zufall sein?

    python scripts/signifikanz.py bt.json
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path


def kennzahlen(trades: list[dict], label: str) -> dict:
    r = [t["r_multiple"] for t in trades if t.get("r_multiple") is not None]
    n = len(r)
    if n < 2:
        return {"label": label, "n": n}
    mittel = statistics.mean(r)
    sd = statistics.stdev(r)
    se = sd / math.sqrt(n)
    return {"label": label, "n": n, "mittel_r": mittel, "sd": sd, "se": se,
            "ci_low": mittel - 1.96 * se, "ci_high": mittel + 1.96 * se}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    daten = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))

    a = kennzahlen(daten["trades_ki"], "Depot KI")
    b = kennzahlen(daten["trades_zufall"], "Depot Zufall")
    if a["n"] < 2 or b["n"] < 2:
        print("Zu wenige Trades.")
        return 1

    print(f"Zeitfenster {daten['fenster'][0]} bis {daten['fenster'][1]} "
          f"({daten['modus']})")
    print()
    print(f"{'':14}{'Trades':>8}{'Mittel R':>11}{'Streuung':>11}"
          f"{'Standardfehler':>16}{'95%-Bereich':>22}")
    print("-" * 82)
    for k in (a, b):
        print(f"{k['label']:14}{k['n']:>8}{k['mittel_r']:>+11.4f}"
              f"{k['sd']:>11.3f}{k['se']:>16.4f}"
              f"   {k['ci_low']:+.3f} bis {k['ci_high']:+.3f}")

    diff = a["mittel_r"] - b["mittel_r"]
    se_diff = math.sqrt(a["se"] ** 2 + b["se"] ** 2)
    t = diff / se_diff if se_diff else 0.0

    print()
    print(f"Unterschied KI minus Zufall: {diff:+.4f} R")
    print(f"Standardfehler des Unterschieds: {se_diff:.4f} R")
    print(f"t-Wert: {t:+.2f}")
    print(f"95%-Bereich des Unterschieds: {diff - 1.96 * se_diff:+.3f} "
          f"bis {diff + 1.96 * se_diff:+.3f} R")
    print()

    if abs(t) < 1.96:
        print("URTEIL: Der Unterschied ist mit dieser Stichprobe NICHT von")
        print("        Zufall zu unterscheiden (|t| < 1.96). Weder ein Vorsprung")
        print("        noch ein Rueckstand ist belegt.")
    elif t > 0:
        print("URTEIL: Die Analyse ist statistisch besser als der Zufall.")
    else:
        print("URTEIL: Die Analyse ist statistisch SCHLECHTER als der Zufall.")

    # Wie viele Trades braeuchte es, um einen Unterschied dieser Groesse
    # ueberhaupt nachweisen zu koennen?
    gemeinsame_sd = (a["sd"] + b["sd"]) / 2
    if abs(diff) > 1e-9:
        noetig = 2 * (1.96 * gemeinsame_sd / diff) ** 2
        print()
        print(f"Um einen Unterschied von {abs(diff):.3f} R nachzuweisen, braeuchte")
        print(f"es rund {noetig:.0f} Trades je Depot "
              f"(vorhanden: {a['n']} bzw. {b['n']}).")
        print(f"Bei 3 Trades pro Handelstag waeren das etwa "
              f"{noetig / 3 / 21:.0f} Monate Livebetrieb.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
