"""Misst, wie weit Kurse wirklich laufen — und schreibt docs/data/calibration.json.

Die Kalibrierung ist die Grundlage fuer Ziel und Stop: statt einen ATR-Faktor
zu raten, wird er aus der gemessenen Bewegung des ganzen Universums gesetzt.
Zusaetzlich entsteht das Erstpassage-Gitter, aus dem jede Ideenkarte ihre
Basisquote bezieht.

    python scripts/kalibrieren.py                # messen und speichern
    python scripts/kalibrieren.py --gegenprobe   # zusaetzlich nachrechnen
    python scripts/kalibrieren.py --nur-anzeigen # bestehende Datei ansehen

Die Gegenprobe laeuft die Tage ein zweites Mal einzeln nach, mit
Eroeffnungsluecken. Weicht sie vom Gitter ab, stimmt eine der beiden
Rechnungen nicht.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import alpaca, calibration, config, universe      # noqa: E402

log = logging.getLogger("kalibrieren")


def tabelle(cal: dict) -> None:
    print("\n" + "=" * 78)
    print(calibration.summary_line(cal))
    print("=" * 78)

    print(f"\n{'Branche':<20}{'n':>9}{'auf Median':>12}{'ab Median':>11}"
          f"{'Ziel k':>9}{'Stop k':>9}{'CRV':>7}")
    print("-" * 78)
    for sektor in sorted(cal["up"], key=lambda s: (s != "_gesamt", s)):
        up = cal["up"][sektor]
        dn = cal["down"].get(sektor, {})
        k_ziel, k_stop = calibration.factors(cal, sektor)
        label = "ALLE" if sektor == "_gesamt" else sektor
        print(f"{label:<20}{up['n']:>9,}{up['median']:>12.2f}"
              f"{dn.get('median', 0):>11.2f}{k_ziel:>9.2f}{k_stop:>9.2f}"
              f"{k_ziel / k_stop:>7.2f}".replace(",", "'"))

    print()
    print("Basisquote bei den kalibrierten Marken - was eine ZUFAELLIGE")
    print("Auswahl unter denselben Regeln erreicht:")
    print(f"\n{'Branche':<20}{'Ziel zuerst':>13}{'Stop zuerst':>13}"
          f"{'Zeitablauf':>12}{'Erwartung R':>13}")
    print("-" * 78)
    for sektor in sorted(cal["first_passage"], key=lambda s: (s != "_gesamt", s)):
        b = calibration.base_rate(cal, sektor)
        if not b:
            continue
        label = "ALLE" if sektor == "_gesamt" else sektor
        print(f"{label:<20}{b['p_ziel']:>12.1%}{b['p_stop']:>13.1%}"
              f"{b['p_zeit']:>12.1%}{b['erwartung_r']:>+13.4f}")

    g = cal["up"]["_gesamt"]["erreichbar"]
    print("\nBeruehrungsquote nach oben (alle Branchen)")
    print("  " + "  ".join(f"{k} ATR: {v:.0%}" for k, v in
                           sorted(g.items(), key=lambda kv: float(kv[0]))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--historie", type=int, default=700,
                    help="Kalendertage Bar-Historie")
    ap.add_argument("--horizont", type=int, default=config.HORIZON_DAYS)
    ap.add_argument("--gegenprobe", action="store_true",
                    help="Basisquote unabhaengig nachrechnen")
    ap.add_argument("--nur-anzeigen", action="store_true",
                    help="bestehende calibration.json anzeigen, nicht messen")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")

    if args.nur_anzeigen:
        cal = calibration.load()
        if not cal:
            print("Keine calibration.json vorhanden.")
            return 1
        tabelle(cal)
        return 0

    gestartet = time.time()
    u = universe.get()
    syms = [m["symbol"] for m in u["symbols"]]
    sektor = {m["symbol"]: m["sector"] for m in u["symbols"]}
    bars = alpaca.daily_bars(syms, dt.date.today() - dt.timedelta(days=args.historie))
    log.info("Bars fuer %d Symbole geladen (%.0f s)", len(bars),
             time.time() - gestartet)

    cal = calibration.measure(bars, sektor, horizon=args.horizont)
    calibration.save(cal)
    tabelle(cal)

    if args.gegenprobe:
        log.info("Gegenprobe laeuft ...")
        ref = calibration.measure_base_rates(bars, sektor, cal,
                                             horizon=args.horizont)
        print("\nGegenprobe: Gitter gegen unabhaengige Nachrechnung")
        print(f"{'Branche':<20}{'Gitter p_ziel':>15}{'Nachrechnung':>14}"
              f"{'Differenz':>12}")
        print("-" * 78)
        groesste = 0.0
        for sektor_name in sorted(ref, key=lambda s: (s != "_gesamt", s)):
            g = calibration.base_rate(cal, sektor_name)
            if not g:
                continue
            r = ref[sektor_name]
            d = g["p_ziel"] - r["p_ziel"]
            groesste = max(groesste, abs(d))
            label = "ALLE" if sektor_name == "_gesamt" else sektor_name
            print(f"{label:<20}{g['p_ziel']:>14.1%}{r['p_ziel']:>14.1%}"
                  f"{d:>+12.1%}")
        print(f"\nGroesste Abweichung: {groesste:.1%}", end="  ")
        if groesste < 0.03:
            print("- beide Rechnungen stimmen ueberein.")
        else:
            print("- ZU GROSS, eine der beiden Rechnungen stimmt nicht.")

    print(f"\nLaufzeit {time.time() - gestartet:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
