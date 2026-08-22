"""Einmalige Tagesanalyse ohne Depot und ohne Push — zum Pruefen.

    python scripts/screen.py            volles Universum
    python scripts/screen.py --schnell  ohne Fundamentaldaten-Nachladen
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import (alpaca, analysis, calibration, config,      # noqa: E402
                 universe, yahoo)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--schnell", action="store_true",
                    help="keine neuen Fundamentaldaten holen")
    ap.add_argument("--budget", type=int, default=600,
                    help="hoechstens so viele Fundamentaldaten pro Lauf")
    ap.add_argument("--json", type=str, default="",
                    help="Ergebnis als JSON hierhin schreiben")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    log = logging.getLogger("screen")

    started = time.time()
    u = universe.get()
    log.info("Universum: %d Titel (gebaut %s)", u["count"], u["built_at"])

    syms = [m["symbol"] for m in u["symbols"]]
    t = time.time()
    bars = alpaca.daily_bars(syms + [config.BENCHMARK],
                             dt.date.today() - dt.timedelta(days=config.HISTORY_DAYS))
    log.info("Tagesbars fuer %d Symbole in %.1f s", len(bars), time.time() - t)

    vix = yahoo.chart_closes("^VIX", "1mo")
    res = analysis.run(
        u, bars, bars.get(config.BENCHMARK, []), dt.date.today(),
        vix_closes=vix,
        fundamentals_budget=0 if args.schnell else args.budget,
    )

    r = res["regime"]
    print()
    print(f"Regime: {r['trend']} | {config.BENCHMARK} {r['benchmark_close']} "
          f"vs 200-Tage {r['benchmark_sma200']} | VIX {r['vix']} ({r['vix_level']})")
    print(f"Bewertet {res['scored']} | ausgeschlossen {res['excluded']} | "
          f"Fundamentaldaten {res['fundamentals']}")
    print(f"Branchenmediane Forward-KGV: "
          f"{dict(sorted(res['sector_median_pe'].items()))}")
    print(calibration.summary_line(calibration.get()))
    print()
    print(f"{'#':>2} {'Titel':<7}{'Branche':<17}{'Score':>6} {'Abd':>5} "
          f"{'Kurs':>9}{'Ziel':>9}{'Auf':>7}{'Stop':>9}{'CRV':>6}"
          f"{'P(Ziel)':>9}{'Basis':>8}  Begruendung")
    print("-" * 132)
    for i, e in enumerate(res["shortlist"][:25], 1):
        sc, tg = e["scoring"], e.get("targets") or {}
        mark = "" if e.get("tradeable") else "  x " + (e.get("reject_reason") or "")
        bq = tg.get("basisquote") or {}
        p_ziel = tg.get("p_ziel_beruehrt")
        p_text = "-" if p_ziel is None else f"{p_ziel:.0%}"
        b_text = "-" if not bq else f"{bq['erwartung_r']:+.2f}"
        print(f"{i:>2} {e['symbol']:<7}{e['sector'][:16]:<17}"
              f"{sc['score']:>6.3f} {sc['coverage']:>4.0%} "
              f"{tg.get('price', 0):>9.2f}{tg.get('target') or 0:>9.2f}"
              f"{tg.get('upside_pct') or 0:>6.1f}%{tg.get('stop') or 0:>9.2f}"
              f"{tg.get('reward_risk') or 0:>6.2f}"
              f"{p_text:>9}{b_text:>8}{mark}")

    print()
    print(f"Handelbare Ideen: {len(res['ideas'])} von {len(res['shortlist'])} "
          f"in der Vorauswahl")
    for e in res["ideas"][:config.PICKS_PER_DAY]:
        tg = e["targets"]
        print(f"\n  {e['symbol']} — {e['name']} ({e['sector']})")
        print(f"    Score {e['scoring']['score']:.3f}, Abdeckung "
              f"{e['scoring']['coverage']:.0%}")
        for c in e["scoring"]["components"]:
            if c["score"] is not None:
                print(f"      {c['label']:<28} {c['score']:.2f} "
                      f"(Gewicht {c['weight']:.2f}) — {'; '.join(c['reasons'][:2])}")
        if e["scoring"]["penalties"]:
            print(f"      Abzuege: {'; '.join(e['scoring']['penalties'])}")
        print(f"    Ziel {tg['target']} ({tg['upside_pct']:+.1f} %), "
              f"Stop {tg['stop']}, CRV {tg['reward_risk']}, "
              f"Bereich {tg['band_low']}-{tg['band_high']}")
        for zeile in tg.get("probability_steps", []):
            print(f"      {zeile}")

    print(f"\nGesamtlaufzeit {time.time() - started:.1f} s "
          f"(davon Analyse {res['seconds']} s)")

    if args.json:
        schlank = {k: v for k, v in res.items() if k != "all_scored"}
        Path(args.json).write_text(
            json.dumps(schlank, indent=1, default=str), encoding="utf-8")
        print(f"JSON geschrieben: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
