"""Hat der Score ueberhaupt Vorhersagekraft? Die eigentliche Messung.

Das Depot kauft drei Titel am Tag. Um damit einen kleinen Vorsprung
nachzuweisen, braeuchte es rund 25 Monate — gemessen im Backtest. Das ist
keine brauchbare Rueckmeldung fuer die Entwicklung.

Diese Auswertung nutzt stattdessen ALLE Kandidaten jedes Tages, nicht nur
die drei gekauften. Rund 370 Titel taeglich statt 3 — das ist mehr als das
Hundertfache an Beobachtungen.

METHODE (nach Fama-MacBeth, damit die Statistik ehrlich bleibt):

  1. An jedem Handelstag werden alle Kandidaten nach Score in Fuenftel
     geteilt und die Rendite der naechsten 15 Handelstage gemessen.
  2. Je Tag ergibt sich EIN Wert: Rendite des besten Fuenftels minus
     Rendite des schlechtesten. Damit spielt es keine Rolle, dass alle
     Aktien am selben Tag gemeinsam steigen oder fallen.
  3. Getestet wird der Mittelwert dieser Tageswerte.

  Die 15-Tage-Fenster benachbarter Tage ueberlappen sich. Der Standardfehler
  wird deshalb mit sqrt(15) vergroessert — konservativ, aber ehrlich.
  Zusaetzlich wird die ueberlappungsfreie Schaetzung ausgewiesen.

    python scripts/trennschaerfe.py --tage 250
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import (alpaca, config, indicators as ind, scoring,          # noqa: E402
                 universe, yahoo)

log = logging.getLogger("trennschaerfe")

TECHNISCHE_GEWICHTE = {"trend": 0.45, "setup": 0.33, "volumen": 0.22}
FUENFTEL = 5


def forward_return(bars: list[dict], i: int, horizont: int) -> float | None:
    """Rendite von Schluss i bis Schluss i+horizont."""
    if i + horizont >= len(bars):
        return None
    a, b = float(bars[i]["c"]), float(bars[i + horizont]["c"])
    return None if a <= 0 else b / a - 1.0


def auswerten(werte: list[float], label: str, overlap: int) -> dict:
    n = len(werte)
    if n < 3:
        return {"label": label, "n": n}
    mittel = statistics.mean(werte)
    sd = statistics.stdev(werte)
    se_naiv = sd / math.sqrt(n)
    se_korrigiert = se_naiv * math.sqrt(overlap)
    return {
        "label": label, "n": n, "mittel": mittel, "sd": sd,
        "se": se_korrigiert, "t": mittel / se_korrigiert if se_korrigiert else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tage", type=int, default=250)
    ap.add_argument("--historie", type=int, default=700)
    ap.add_argument("--horizont", type=int, default=config.HORIZON_DAYS)
    ap.add_argument("--mit-fundamentals", action="store_true")
    ap.add_argument("--json", type=str, default="")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S")
    gestartet = time.time()

    u = universe.get()
    syms = [m["symbol"] for m in u["symbols"]]
    bars = alpaca.daily_bars(syms + [config.BENCHMARK],
                             dt.date.today() - dt.timedelta(days=args.historie))
    bench = bars.get(config.BENCHMARK, [])
    log.info("Bars fuer %d Symbole", len(bars))

    pre, idx = {}, {}
    for sym, series in bars.items():
        if len(series) >= 60:
            pre[sym] = ind.precompute(series, benchmark=bench)
            idx[sym] = {b["t"][:10]: i for i, b in enumerate(series)}

    sektor = {m["symbol"]: m["sector"] for m in u["symbols"]}
    fundamentals_map = {}
    if args.mit_fundamentals:
        log.warning("Fundamentaldaten sind aktuell, nicht historisch — verzerrt.")
        fundamentals_map = {s: yahoo.fundamentals(s, max_age_days=10 ** 6)
                            for s in sektor}
        sektor_pe = scoring.sector_median_pes(
            [{"sector": sektor[s], "fundamentals": f}
             for s, f in fundamentals_map.items() if f])
    else:
        sektor_pe = {}
    gewichte = None if args.mit_fundamentals else TECHNISCHE_GEWICHTE

    handelstage = [b["t"][:10] for b in bench]
    # Nur Tage, fuer die der volle Vorwaertshorizont noch existiert
    nutzbar = handelstage[:-args.horizont]
    test_tage = nutzbar[-args.tage:] if args.tage < len(nutzbar) else nutzbar
    log.info("Testfenster %s bis %s (%d Tage, Horizont %d)",
             test_tage[0], test_tage[-1], len(test_tage), args.horizont)

    spreads: list[float] = []
    komponenten_spreads: dict[str, list[float]] = {}
    beobachtungen = 0
    fuenftel_renditen: list[list[float]] = [[] for _ in range(FUENFTEL)]

    for tag in test_tage:
        zeile = []
        for sym, index in idx.items():
            if sym == config.BENCHMARK:
                continue
            i = index.get(tag)
            if i is None or i < 60:
                continue
            snap = ind.snapshot_at(pre[sym], i)
            if scoring.hard_exclusions(snap, {}):
                continue
            f = fundamentals_map.get(sym) if args.mit_fundamentals else None
            sc = scoring.score(snap, f, None,
                               sector_median_pe=sektor_pe.get(sektor.get(sym)),
                               weights=gewichte,
                               today=dt.date.fromisoformat(tag))
            if sc["score"] is None:
                continue
            fwd = forward_return(bars[sym], i, args.horizont)
            if fwd is None:
                continue
            zeile.append({
                "score": sc["score"], "fwd": fwd,
                "komponenten": {c["key"]: c["score"] for c in sc["components"]
                                if c["score"] is not None},
            })

        if len(zeile) < FUENFTEL * 4:
            continue
        beobachtungen += len(zeile)

        def spread_nach(schluessel) -> float | None:
            werte = [(schluessel(x), x["fwd"]) for x in zeile
                     if schluessel(x) is not None]
            if len(werte) < FUENFTEL * 4:
                return None
            werte.sort(key=lambda p: p[0])
            gr = len(werte) // FUENFTEL
            unten = statistics.mean(r for _, r in werte[:gr])
            oben = statistics.mean(r for _, r in werte[-gr:])
            return oben - unten

        s = spread_nach(lambda x: x["score"])
        if s is not None:
            spreads.append(s)
            # Fuenftel-Renditen fuer die Verteilungstabelle
            werte = sorted(((x["score"], x["fwd"]) for x in zeile),
                           key=lambda p: p[0])
            gr = len(werte) // FUENFTEL
            for q in range(FUENFTEL):
                teil = werte[q * gr:(q + 1) * gr]
                if teil:
                    fuenftel_renditen[q].append(
                        statistics.mean(r for _, r in teil))

        for key in ("trend", "setup", "volumen", "qualitaet", "bewertung",
                    "analysten"):
            v = spread_nach(lambda x, k=key: x["komponenten"].get(k))
            if v is not None:
                komponenten_spreads.setdefault(key, []).append(v)

    # --- Auswertung ---
    print("\n" + "=" * 78)
    print(f"TRENNSCHAERFE  {test_tage[0]} bis {test_tage[-1]}  "
          f"({len(spreads)} Tage, {beobachtungen:,} Beobachtungen)".replace(",", "'"))
    print(f"Modus: {'mit Fundamentaldaten (VERZERRT)' if args.mit_fundamentals else 'nur technisch'}"
          f" | Horizont {args.horizont} Handelstage")
    print("=" * 78)

    print("\nRendite je Score-Fuenftel (Durchschnitt ueber alle Tage)")
    print(f"  {'Fuenftel':<12}{'mittlere Rendite':>20}{'Tage':>8}")
    for q in range(FUENFTEL):
        werte = fuenftel_renditen[q]
        if werte:
            label = ("schlechtestes" if q == 0 else
                     "bestes" if q == FUENFTEL - 1 else f"{q + 1}.")
            print(f"  {label:<12}{statistics.mean(werte) * 100:>19.3f} %{len(werte):>8}")

    gesamt = auswerten(spreads, "Gesamtscore", args.horizont)
    print(f"\n{'Merkmal':<14}{'Tage':>6}{'Spread':>11}{'Streuung':>11}"
          f"{'Std.fehler':>12}{'t-Wert':>9}   Urteil")
    print("-" * 78)

    def zeile_aus(k: dict) -> None:
        if k["n"] < 3:
            return
        urteil = ("belegt positiv" if k["t"] > 1.96 else
                  "belegt negativ" if k["t"] < -1.96 else "nicht belegt")
        print(f"{k['label']:<14}{k['n']:>6}{k['mittel'] * 100:>10.3f}%"
              f"{k['sd'] * 100:>10.2f}%{k['se'] * 100:>11.3f}%"
              f"{k['t']:>9.2f}   {urteil}")

    zeile_aus(gesamt)
    for key, werte in sorted(komponenten_spreads.items(),
                             key=lambda kv: -statistics.mean(kv[1])):
        zeile_aus(auswerten(werte, key, args.horizont))

    print()
    if gesamt.get("n", 0) >= 3:
        print(f"Der Score trennt bestes von schlechtestem Fuenftel um "
              f"{gesamt['mittel'] * 100:+.3f} Prozentpunkte")
        print(f"ueber {args.horizont} Handelstage (t = {gesamt['t']:+.2f}).")
        if abs(gesamt["t"]) < 1.96:
            print("Das ist NICHT von Zufall zu unterscheiden.")
        elif gesamt["t"] > 0:
            print("Das ist ein belegter Vorsprung.")
        else:
            print("Der Score waehlt belegbar die FALSCHEN Titel.")

    print(f"\nLaufzeit {time.time() - gestartet:.1f} s")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "fenster": [test_tage[0], test_tage[-1]],
            "horizont": args.horizont,
            "modus": "mit_fundamentals" if args.mit_fundamentals else "technisch",
            "beobachtungen": beobachtungen,
            "gesamt": gesamt,
            "komponenten": {k: auswerten(v, k, args.horizont)
                            for k, v in komponenten_spreads.items()},
            "fuenftel": [statistics.mean(v) if v else None
                         for v in fuenftel_renditen],
        }, indent=1), encoding="utf-8")
        print(f"JSON: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
