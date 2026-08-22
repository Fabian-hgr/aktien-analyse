"""Lauf B — Abrechnung: fuellen, ausstiegen, lernen.

Laeuft werktags um 22:00 UTC, also nach dem US-Handelsschluss.

    python -m src.run_settle                    # scharf
    python -m src.run_settle --probelauf        # rechnen, nichts schreiben
    python -m src.run_settle --tag 2026-08-20   # einen bestimmten Tag nachholen

Reihenfolge, und warum sie so ist:

    1  Gestrige Auftraege zur HEUTIGEN Eroeffnung fuellen.
       Nie zum Kurs von gestern — das waere ein Blick in die Zukunft.
    2  Ausstiege pruefen. Ein heute gefuellter Auftrag kann noch am selben
       Tag ausgestoppt werden.
    3  Depots bewerten, Equity-Kurve fortschreiben.
    4  Lernen — ausschliesslich aus den Trades des KI-Depots. Das
       Zufallsdepot ist die Kontrollgruppe und bleibt unberuehrt.

Wird ein Tag verpasst (Actions-Ausfall), holt `--tag` ihn nach. Die
Haltedauer zaehlt in Kalendertagen der Depotlogik weiter; ein Loch faellt
also auf und wird nicht stillschweigend uebergangen.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import time
from typing import Optional

from . import (alpaca, analysis, calibration, config, learning, notify,
               portfolio)

log = logging.getLogger("abrechnung")


def schreiben(pfad, daten) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(json.dumps(daten, indent=1, default=str), encoding="utf-8")
    log.info("geschrieben: %s (%.0f KB)", pfad.name, pfad.stat().st_size / 1024)


def bars_am_tag(symbols: list[str], tag: dt.date) -> dict[str, dict]:
    """Die Tagesbars genau dieses Handelstags."""
    if not symbols:
        return {}
    reihen = alpaca.daily_bars(symbols, tag - dt.timedelta(days=10),
                               end=tag)
    heute = tag.isoformat()
    out = {}
    for sym, serie in reihen.items():
        for b in reversed(serie):
            if b["t"][:10] == heute:
                out[sym] = b
                break
    return out


def spy_kurve(bench: list[dict], vorhanden: list[dict],
              ab: str = "") -> list[dict]:
    """Kauf-und-Halten des Index, auf dasselbe Startkapital normiert.

    Der dritte Vergleich neben KI und Zufall — und der unbequemste: ein
    Indexkauf ist gratis zu haben und braucht keine Analyse.

    `ab` ist der erste Tag der Depots. Die Bars reichen 400 Kalendertage
    zurueck; ohne diese Grenze begaenne der Index ein Jahr vor den Depots
    und stuende schon am ersten Abrechnungstag scheinbar dutzende Prozent
    vorn. Verglichen wird ab dem Tag, an dem beide Depots starten.
    """
    if not bench:
        return vorhanden
    reihe = [b for b in bench if b["t"][:10] >= ab] if ab else list(bench)
    if not reihe:
        return vorhanden
    start = vorhanden[0]["basis"] if vorhanden else float(reihe[0]["c"])
    kurve = []
    for b in reihe:
        kurs = float(b["c"])
        kurve.append({
            "date": b["t"][:10],
            "equity": round(config.START_CAPITAL * kurs / start, 2),
            "return_pct": round((kurs / start - 1) * 100, 3),
            "basis": start,
        })
    return kurve


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probelauf", action="store_true",
                    help="rechnen, aber nichts schreiben")
    ap.add_argument("--tag", type=str, default="",
                    help="Handelstag im Format JJJJ-MM-TT (Standard: heute)")
    ap.add_argument("--kein-lernen", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    config.load_local_secrets()
    gestartet = time.time()
    tag = dt.date.fromisoformat(args.tag) if args.tag else dt.date.today()

    steuerung = notify.apply_control()
    if steuerung.get("paused"):
        log.info("Pausiert — keine Abrechnung")
        return 0
    if not alpaca.is_trading_day(tag):
        log.info("%s ist kein Handelstag — Ende", tag)
        return 0

    pf_ki = portfolio.load("ki")
    pf_zufall = portfolio.load("zufall")

    gebraucht = sorted({
        p["symbol"]
        for pf in (pf_ki, pf_zufall)
        for liste in (pf["positions"], pf["pending"])
        for p in liste
    })
    log.info("%d Symbole in Positionen und Auftraegen", len(gebraucht))

    bench = alpaca.daily_bars([config.BENCHMARK],
                              tag - dt.timedelta(days=config.HISTORY_DAYS),
                              end=tag).get(config.BENCHMARK, [])
    regime = analysis.market_regime(bench)

    bars_heute = bars_am_tag(gebraucht, tag)
    if bench:
        bars_heute[config.BENCHMARK] = bench[-1]
    fehlend = [s for s in gebraucht if s not in bars_heute]
    if fehlend:
        log.warning("Keine Bars fuer %d Symbole: %s", len(fehlend),
                    ", ".join(fehlend[:10]))

    ergebnis = {}
    for pf in (pf_ki, pf_zufall):
        r = portfolio.settle_day(pf, bars_heute, tag, regime)
        ergebnis[pf["name"]] = r
        log.info("%s: %d gefuellt, %d geschlossen, Depotwert %.0f USD "
                 "(%d offen)", pf["label"], len(r["filled"]), len(r["closed"]),
                 r["equity"]["equity"], len(pf["positions"]))
        for t in r["closed"]:
            log.info("   %s %s zu %.2f — %s, %+.2f R",
                     pf["name"], t["symbol"], t["exit_price"],
                     t["exit_reason"], t["r_multiple"])

    # Lernen — nur aus dem KI-Depot, und nur wenn genug Trades vorliegen.
    gewichte = learning.load()
    lernschritte: list[str] = []
    if not args.kein_lernen and len(pf_ki["closed"]) >= config.LEARN_MIN_TRADES:
        lernschritte = learning.update(gewichte, pf_ki["closed"], tag,
                                       calibration.get())
        for zeile in lernschritte:
            log.info("gelernt: %s", zeile)
    elif not args.kein_lernen:
        log.info("Erst %d von %d noetigen Trades — es wird noch nicht gelernt",
                 len(pf_ki["closed"]), config.LEARN_MIN_TRADES)

    stats = {"ki": portfolio.statistics(pf_ki),
             "zufall": portfolio.statistics(pf_zufall)}
    for name, s in stats.items():
        log.info("%s: %+.2f %% aus %d Trades, Trefferquote %s %%, "
                 "Erwartung %s R", name, s["return_pct"], s["trades"],
                 s["win_rate"], s["expectancy_r"])

    if args.probelauf:
        log.info("Probelauf — nichts geschrieben (%.0f s)",
                 time.time() - gestartet)
        return 0

    portfolio.save(pf_ki)
    portfolio.save(pf_zufall)
    learning.save(gewichte)

    alt = config.DATA_DIR / "equity.json"
    vorher = []
    if alt.exists():
        try:
            vorher = json.loads(alt.read_text(encoding="utf-8")).get("spy", [])
        except (OSError, json.JSONDecodeError):
            vorher = []
    # Die Kennzahlen liegen auch in trades.json — dort aber hinter Megabytes
    # Einzeltrades. Die Seite soll fuer den Depotvergleich nicht die ganze
    # Handelshistorie laden muessen, deshalb stehen sie hier noch einmal.
    schreiben(alt, {
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(
            timespec="seconds"),
        "start_capital": config.START_CAPITAL,
        "statistik": stats,
        "ki": pf_ki["equity_curve"],
        "zufall": pf_zufall["equity_curve"],
        "spy": spy_kurve(bench, vorher, ab=(pf_ki["equity_curve"][0]["date"]
                                            if pf_ki["equity_curve"] else
                                            tag.isoformat())),
    })
    schreiben(config.DATA_DIR / "trades.json", {
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(
            timespec="seconds"),
        "ki": pf_ki["closed"],
        "zufall": pf_zufall["closed"],
        "statistik": stats,
    })
    schreiben(config.DATA_DIR / "status.json", {
        "letzter_lauf": dt.datetime.now(dt.timezone.utc).isoformat(
            timespec="seconds"),
        "lauf": "abrechnung", "ergebnis": "fertig", "tag": tag.isoformat(),
        "gefuellt": {k: len(v["filled"]) for k, v in ergebnis.items()},
        "geschlossen": {k: len(v["closed"]) for k, v in ergebnis.items()},
        "lernschritte": lernschritte,
        "sekunden": round(time.time() - gestartet, 1),
        "steuerung": steuerung,
    })

    log.info("Lauf B fertig in %.0f s", time.time() - gestartet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
