"""Der Ehrlichkeitstest: schlaegt die Analyse den Zufall?

Es wird Tag fuer Tag nachgespielt. An jedem Tag stehen dem System nur die
Daten bis einschliesslich dieses Tages zur Verfuegung — sichergestellt durch
indicators.snapshot_at(), das ausschliesslich vorwaertsgerichtete Reihen an
einem Index liest.

    python scripts/backtest.py --tage 250
    python scripts/backtest.py --tage 250 --lernen
    python scripts/backtest.py --tage 250 --mit-fundamentals   (verzerrt!)

ZWEI VERZERRUNGEN, die sich mit Gratisdaten nicht beseitigen lassen:

  Fundamentaldaten sind AKTUELL, nicht historisch. Yahoo liefert die
  Analystenziele von heute, nicht die von vor einem Jahr. Sie im Rueckblick
  zu verwenden waere ein Blick in die Zukunft. Deshalb laeuft der Backtest
  standardmaessig NUR technisch. --mit-fundamentals existiert, um den
  Unterschied sichtbar zu machen — das Ergebnis ist dann zu gut.

  Ueberlebensverzerrung: das Universum sind die HEUTIGEN Indexmitglieder.
  Wer in den letzten zwoelf Monaten aus dem Index flog, fehlt. Das faerbt
  den Rueckblick zu freundlich. Der Livebetrieb ab Tag 1 ist davon frei —
  nur er ist die eigentliche Messung.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import (alpaca, analysis, calibration, config,              # noqa: E402
                 indicators as ind, learning, portfolio, scoring, targets,
                 universe, yahoo)

log = logging.getLogger("backtest")

TECHNISCHE_GEWICHTE = {"trend": 0.45, "setup": 0.33, "volumen": 0.22}


def lade_daten(tage_historie: int):
    u = universe.get()
    syms = [m["symbol"] for m in u["symbols"]]
    start = dt.date.today() - dt.timedelta(days=tage_historie)
    bars = alpaca.daily_bars(syms + [config.BENCHMARK], start)
    return u, bars


def baue_index(bars: dict) -> tuple[dict, dict]:
    """Vorberechnete Reihen und Datum-zu-Index je Titel."""
    bench = bars.get(config.BENCHMARK, [])
    pre, idx = {}, {}
    for sym, series in bars.items():
        if len(series) < 60:
            continue
        pre[sym] = ind.precompute(series, benchmark=bench)
        idx[sym] = {b["t"][:10]: i for i, b in enumerate(series)}
    return pre, idx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tage", type=int, default=250,
                    help="Handelstage im Test")
    ap.add_argument("--historie", type=int, default=700,
                    help="Kalendertage Bar-Historie insgesamt")
    ap.add_argument("--lernen", action="store_true",
                    help="Lernschleife waehrend des Backtests laufen lassen")
    ap.add_argument("--mit-fundamentals", action="store_true",
                    help="Fundamentaldaten einbeziehen (verzerrt zugunsten)")
    ap.add_argument("--stop-atr", type=float, default=None,
                    help="Stop in ATR-Einheiten (Standard: kalibriert)")
    ap.add_argument("--ziel-atr", type=float, default=None,
                    help="Ziel in ATR-Einheiten (Standard: kalibriert)")
    ap.add_argument("--ohne-kalibrierung", action="store_true",
                    help="Rueckfallwerte aus config statt gemessener Marken")
    ap.add_argument("--still", action="store_true", help="nur die Kernzahlen")
    ap.add_argument("--json", type=str, default="")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING if args.still else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    if args.stop_atr is not None:
        config.ATR_STOP_MULT = args.stop_atr
    if args.ziel_atr is not None:
        config.ATR_TARGET_MULT = args.ziel_atr
    cal = None if args.ohne_kalibrierung else calibration.get()
    if cal:
        log.info("%s", calibration.summary_line(cal))
    gestartet = time.time()

    u, bars = lade_daten(args.historie)
    log.info("Universum %d Titel, Bars fuer %d Symbole", u["count"], len(bars))

    pre, idx = baue_index(bars)
    bench_bars = bars.get(config.BENCHMARK, [])
    handelstage = [b["t"][:10] for b in bench_bars]
    test_tage = handelstage[-args.tage:] if args.tage < len(handelstage) else handelstage
    log.info("Testfenster: %s bis %s (%d Handelstage)",
             test_tage[0], test_tage[-1], len(test_tage))

    sektor = {m["symbol"]: m["sector"] for m in u["symbols"]}
    name = {m["symbol"]: m.get("name", m["symbol"]) for m in u["symbols"]}

    fundamentals_map = {}
    if args.mit_fundamentals:
        log.warning("Fundamentaldaten sind AKTUELL, nicht historisch — "
                    "das Ergebnis faellt zu gut aus.")
        fundamentals_map = {s: yahoo.fundamentals(s, max_age_days=10 ** 6)
                            for s in sektor}
    gewichte = None if args.mit_fundamentals else TECHNISCHE_GEWICHTE

    pf_ki = portfolio.new_portfolio("ki")
    pf_zufall = portfolio.new_portfolio("zufall")
    weights = learning.default_weights()

    # SPY zum Vergleich: einmal kaufen, halten
    spy_start_idx = idx[config.BENCHMARK][test_tage[0]]
    spy_start = float(bench_bars[spy_start_idx]["c"])
    spy_kurve = []

    belegung, ideen_pro_tag = [], []

    for tag_nr, tag in enumerate(test_tage):
        heute = dt.date.fromisoformat(tag)

        # --- 1. Gestrige Auftraege fuellen, Ausstiege pruefen, bewerten ---
        bars_heute = {}
        for sym, index in idx.items():
            i = index.get(tag)
            if i is not None:
                bars_heute[sym] = bars[sym][i]

        bi = idx[config.BENCHMARK].get(tag)
        regime = analysis.market_regime(bench_bars[:bi + 1]) if bi else {}

        portfolio.settle_day(pf_ki, bars_heute, heute, regime)
        portfolio.settle_day(pf_zufall, bars_heute, heute, regime)
        belegung.append(len(pf_ki["positions"]))

        spy_kurve.append({
            "date": tag,
            "equity": round(config.START_CAPITAL
                            * float(bench_bars[bi]["c"]) / spy_start, 2),
        })

        # --- 2. Analyse mit den Daten bis einschliesslich heute ---
        kandidaten = []
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
            sc = scoring.score(snap, f, None, weights=gewichte, today=heute)
            if sc["score"] is None:
                continue
            tg = targets.build(
                snap["close"], snap, f,
                weights=weights["target_method_weights"],
                sector=sektor.get(sym, "Unbekannt"),
                k_sector=args.ziel_atr, stop_mult=args.stop_atr,
                k_mult=weights["sector_k_mult"].get(sektor.get(sym)),
                cal=cal,
            )
            ok, _ = analysis.tradeable({"scoring": {"eligible": True}, "targets": tg})
            if not ok:
                continue
            bq = tg.get("basisquote") or {}

            score, _ = learning.effective_score(
                sc["score"], sektor.get(sym, "Unbekannt"), regime, weights)
            kandidaten.append({
                "symbol": sym, "name": name.get(sym, sym),
                "sector": sektor.get(sym, "Unbekannt"),
                "score": score,
                "target": tg["target"], "stop": tg["stop"],
                "atr_at_entry": snap["atr"],
                "ziel_atr": tg.get("ziel_atr"),
                "stop_atr": tg.get("stop_atr"),
                "basis_p_ziel": bq.get("p_ziel"),
                "basis_erwartung_r": bq.get("erwartung_r"),
                "regime": {"trend": regime.get("trend"),
                           "vix_level": regime.get("vix_level")},
                "score_at_entry": score,
                "score_components": {c["key"]: c["score"]
                                     for c in sc["components"]
                                     if c["score"] is not None},
                "target_methods": {m["key"]: m["value"] for m in tg["methods"]
                                   if m["role"] == "niveau" and m["value"]},
            })

        ideen_pro_tag.append(len(kandidaten))
        kandidaten.sort(key=lambda c: c["score"], reverse=True)

        # --- 3. Auftraege fuer morgen ---
        portfolio.place_orders(
            pf_ki,
            analysis.select_picks(kandidaten,
                                  belegt=portfolio.offene_titel(pf_ki)),
            heute)
        portfolio.place_orders(
            pf_zufall,
            portfolio.random_picks(kandidaten, config.PICKS_PER_DAY, heute,
                                   belegt=portfolio.offene_titel(pf_zufall)),
            heute)

        # --- 4. Lernschleife ---
        if args.lernen and tag_nr % 10 == 0 and len(pf_ki["closed"]) >= config.LEARN_MIN_TRADES:
            learning.update(weights, pf_ki["closed"], heute, cal)

        if tag_nr % 50 == 0:
            log.info("%s  KI %.0f  Zufall %.0f  SPY %.0f  (offen %d, Ideen %d)",
                     tag, pf_ki["equity_curve"][-1]["equity"],
                     pf_zufall["equity_curve"][-1]["equity"],
                     spy_kurve[-1]["equity"], len(pf_ki["positions"]),
                     len(kandidaten))

    # --- Auswertung ---
    s_ki = portfolio.statistics(pf_ki)
    s_zu = portfolio.statistics(pf_zufall)
    spy_rendite = (spy_kurve[-1]["equity"] / config.START_CAPITAL - 1) * 100

    print("\n" + "=" * 74)
    print(f"BACKTEST  {test_tage[0]} bis {test_tage[-1]}  "
          f"({len(test_tage)} Handelstage)")
    print(f"Modus: {'mit Fundamentaldaten (VERZERRT)' if args.mit_fundamentals else 'nur technisch'}"
          f"{', Lernschleife aktiv' if args.lernen else ''}"
          f" | Marken: {'kalibriert je Branche' if cal else 'config'}"
          + (f", Ziel {args.ziel_atr} ATR" if args.ziel_atr else "")
          + (f", Stop {args.stop_atr} ATR" if args.stop_atr else ""))
    print("=" * 74)
    print(f"{'':22}{'Depot KI':>14}{'Depot Zufall':>14}{'SPY':>12}")
    print("-" * 74)
    zeilen = [
        ("Rendite %", s_ki["return_pct"], s_zu["return_pct"], round(spy_rendite, 2)),
        ("Endwert USD", s_ki["equity"], s_zu["equity"], spy_kurve[-1]["equity"]),
        ("Trades", s_ki["trades"], s_zu["trades"], "-"),
        ("Trefferquote %", s_ki["win_rate"], s_zu["win_rate"], "-"),
        ("Erwartungswert R", s_ki["expectancy_r"], s_zu["expectancy_r"], "-"),
        ("Profitfaktor", s_ki["profit_factor"], s_zu["profit_factor"], "-"),
        ("Haltedauer Tage", s_ki["avg_hold_days"], s_zu["avg_hold_days"], "-"),
        ("Max. Rueckgang %", s_ki["max_drawdown_pct"], s_zu["max_drawdown_pct"],
         portfolio._max_drawdown(spy_kurve)),
    ]
    for label, a, b, c in zeilen:
        print(f"{label:22}{str(a):>14}{str(b):>14}{str(c):>12}")

    print("\nAusstiegsgruende KI:    ", s_ki.get("exit_reasons"))
    print("Ausstiegsgruende Zufall:", s_zu.get("exit_reasons"))
    print(f"\nOffene Positionen: Median {statistics.median(belegung):.0f}, "
          f"Maximum {max(belegung)} (Deckel {config.MAX_CONCURRENT_POSITIONS})")
    print(f"Handelbare Ideen pro Tag: Median {statistics.median(ideen_pro_tag):.0f}, "
          f"Spanne {min(ideen_pro_tag)}-{max(ideen_pro_tag)}")

    for label, pf in (("KI", pf_ki), ("Zufall", pf_zufall)):
        mit_basis = [t for t in pf["closed"] if t.get("basis_p_ziel") is not None]
        if len(mit_basis) >= 20:
            ist = sum(1 for t in mit_basis
                      if (t["exit_reason"] or "").startswith("ziel")) / len(mit_basis)
            soll = statistics.mean(t["basis_p_ziel"] for t in mit_basis)
            soll_r = statistics.mean(t["basis_erwartung_r"] for t in mit_basis
                                     if t.get("basis_erwartung_r") is not None)
            ist_r = statistics.mean(t["r_multiple"] for t in mit_basis)
            print(f"\nDepot {label} gegen die gemessene Basisquote "
                  f"({len(mit_basis)} Trades)")
            print(f"  Ziel zuerst:      {ist:>7.1%} gegen Basisquote {soll:.1%}"
                  f"   ({ist - soll:+.1%})")
            print(f"  Erwartungswert R: {ist_r:>+7.3f} gegen Basisquote "
                  f"{soll_r:+.3f}   ({ist_r - soll_r:+.3f})")

    vorsprung = s_ki["return_pct"] - s_zu["return_pct"]
    print(f"\nVorsprung der Analyse gegen den Zufall: {vorsprung:+.2f} Prozentpunkte")
    if s_ki["trades"] and s_zu["trades"]:
        print(f"Erwartungswert je Trade: KI {s_ki['expectancy_r']:+.3f} R gegen "
              f"Zufall {s_zu['expectancy_r']:+.3f} R")

    # --- Ursachenanalyse: welche Komponente trennt Gewinner von Verlierern? ---
    print()
    print("Beitrag der Score-Komponenten (KI-Depot)")
    print(f"  {'Komponente':<14}{'n':>5}{'obere Haelfte':>15}{'untere':>10}"
          f"{'Vorsprung':>12}")
    edges = learning.component_edges(pf_ki["closed"])
    if edges:
        for key, e in sorted(edges.items(), key=lambda kv: -kv[1]["edge_r"]):
            urteil = ("hilft" if e["edge_r"] > 0.05 else
                      "schadet" if e["edge_r"] < -0.05 else "wirkungslos")
            print(f"  {key:<14}{e['n']:>5}{e['mean_r_oben']:>+15.3f}"
                  f"{e['mean_r_unten']:>+10.3f}{e['edge_r']:>+12.3f}  {urteil}")
    else:
        print("  (zu wenige Trades)")

    print()
    print("Trefferquote der Kursziel-Methoden")
    for key, st in learning.method_hit_rates(pf_ki["closed"]).items():
        print(f"  {key:<14}{st['n']:>5} Trades, Ziel erreichbar in "
              f"{st['hit_rate']:.0%} der Faelle")

    # Sind hohe Scores ueberhaupt besser als niedrige?
    mit_score = [t for t in pf_ki["closed"] if t.get("score") is not None]
    if len(mit_score) >= 20:
        mit_score.sort(key=lambda t: t["score"])
        h = len(mit_score) // 2
        unten = statistics.mean(t["r_multiple"] for t in mit_score[:h])
        oben = statistics.mean(t["r_multiple"] for t in mit_score[h:])
        print()
        print(f"Gesamtscore als Trennschaerfe: obere Haelfte {oben:+.3f} R "
              f"gegen untere {unten:+.3f} R  ->  Vorsprung {oben - unten:+.3f} R")

    if args.lernen and weights["history"]:
        print(f"\nLernschritte: {len(weights['history'])}")
        for h in weights["history"][-3:]:
            print(f"  {h['date']} (n={h['trades_total']}):")
            for c in h["changes"][:4]:
                print(f"     {c}")

    print(f"\nLaufzeit {time.time() - gestartet:.1f} s")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "fenster": [test_tage[0], test_tage[-1]],
            "modus": "mit_fundamentals" if args.mit_fundamentals else "technisch",
            "lernen": args.lernen,
            "ki": s_ki, "zufall": s_zu,
            "spy_return_pct": round(spy_rendite, 2),
            "equity_ki": pf_ki["equity_curve"],
            "equity_zufall": pf_zufall["equity_curve"],
            "equity_spy": spy_kurve,
            "trades_ki": pf_ki["closed"],
            "trades_zufall": pf_zufall["closed"],
            "weights": weights,
        }, indent=1, default=str), encoding="utf-8")
        print(f"JSON: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
