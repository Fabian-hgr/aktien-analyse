"""Lauf A — Vorboerse: analysieren, auswaehlen, Auftraege setzen, Push senden.

Laeuft werktags um 12:15 UTC auf GitHub Actions, rund 75 Minuten vor der
US-Eroeffnung. Der Vorlauf ist Absicht: geplante Laeufe bei GitHub koennen
sich um bis zu einer halben Stunde verspaeten.

    python -m src.run_premarket                 # scharf
    python -m src.run_premarket --probelauf     # nichts schreiben, nichts senden
    python -m src.run_premarket --kein-push     # schreiben, aber nicht senden

Ablauf und Abbruchgruende:

    Steuer-Thema abfragen  ──pausiert──>  Ende (rund eine Minute Leerlauf)
    Handelt die Boerse heute?  ──nein──>  Ende
    Universum, Tagesbars, Indikatoren
    Bewertung, Fundamentaldaten, Kursziele
    Nachrichten + Sprachmodell auf die Vorauswahl
    3 Kaeufe je Depot vormerken  (KI nach Score, Zufall gewuerfelt)
    JSON schreiben  ->  Push

Faellt eine Quelle aus, laeuft der Rest weiter: ohne Ollama ohne Sentiment,
ohne Yahoo ohne Fundamentaldaten, ohne ntfy ohne Push. Nur ohne Alpaca-Bars
gibt es nichts zu rechnen — dann bricht der Lauf ab.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import time
from typing import Optional

from . import (alpaca, analysis, calibration, config, learning, llm, news,
               notify, portfolio, universe, yahoo)

log = logging.getLogger("vorboerse")

DASHBOARD_URL = "https://fabian-hgr.github.io/aktien-analyse/"


# ── Was auf die Seite geht ─────────────────────────────────────────────────

def kompakt(e: dict) -> dict:
    """Eine Zeile der Universumstabelle — ohne die grossen Rohdaten."""
    sc = e.get("scoring") or {}
    tg = e.get("targets") or {}
    bq = tg.get("basisquote") or {}
    return {
        "symbol": e["symbol"],
        "name": e.get("name"),
        "sector": e.get("sector"),
        "score": sc.get("score"),
        "coverage": sc.get("coverage"),
        "price": tg.get("price"),
        "target": tg.get("target"),
        "upside_pct": tg.get("upside_pct"),
        "stop": tg.get("stop"),
        "reward_risk": tg.get("reward_risk"),
        "p_ziel": tg.get("p_ziel_beruehrt"),
        "basis_erwartung_r": bq.get("erwartung_r"),
        "tradeable": e.get("tradeable"),
        "reject_reason": e.get("reject_reason"),
    }


def vollstaendig(e: dict) -> dict:
    """Eine Ideenkarte mit allem, was die Herleitung braucht."""
    return {
        **kompakt(e),
        "snapshot": e.get("snapshot"),
        "fundamentals": e.get("fundamentals"),
        "llm": e.get("llm"),
        "scoring": e.get("scoring"),
        "targets": e.get("targets"),
    }


def schreiben(pfad, daten) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(json.dumps(daten, indent=1, default=str), encoding="utf-8")
    log.info("geschrieben: %s (%.0f KB)", pfad.name,
             pfad.stat().st_size / 1024)


# ── Auswahl fuer die beiden Depots ─────────────────────────────────────────

def als_auftrag(e: dict, regime: dict) -> dict:
    """Aus einer Idee einen Kaufauftrag machen.

    Alles, was die Lernschleife spaeter braucht, wird hier mitgegeben —
    insbesondere die Basisquote, an der sich der Ausgang messen laesst.
    """
    tg, sc = e["targets"], e["scoring"]
    bq = tg.get("basisquote") or {}
    return {
        "symbol": e["symbol"],
        "name": e.get("name"),
        "sector": e.get("sector"),
        "score": sc.get("score"),
        "target": tg["target"],
        "stop": tg["stop"],
        "atr_at_entry": (e.get("snapshot") or {}).get("atr"),
        "ziel_atr": tg.get("ziel_atr"),
        "stop_atr": tg.get("stop_atr"),
        "basis_p_ziel": bq.get("p_ziel"),
        "basis_erwartung_r": bq.get("erwartung_r"),
        "score_components": {c["key"]: c["score"]
                             for c in sc.get("components", [])
                             if c.get("score") is not None},
        "target_methods": {m["key"]: m["value"] for m in tg.get("methods", [])
                           if m.get("role") == "niveau" and m.get("value")},
        "regime": {"trend": regime.get("trend"),
                   "vix_level": regime.get("vix_level")},
    }


# ── Der Lauf ───────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    config.konsole_utf8()
    ap = argparse.ArgumentParser()
    ap.add_argument("--probelauf", action="store_true",
                    help="nichts schreiben, nichts senden")
    ap.add_argument("--kein-push", action="store_true")
    ap.add_argument("--auch-ohne-handelstag", action="store_true",
                    help="auch laufen, wenn die Boerse heute geschlossen hat")
    ap.add_argument("--budget", type=int, default=600,
                    help="hoechstens so viele Fundamentaldaten je Lauf")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    config.load_local_secrets()
    gestartet = time.time()
    heute = dt.date.today()

    # 1. Aus-Schalter. Steht vor allem anderen, damit eine Pause wirklich
    #    fast nichts kostet.
    steuerung = notify.apply_control()
    if steuerung.get("paused"):
        log.info("Pausiert seit %s (%s) — nichts zu tun",
                 steuerung.get("changed_at"), steuerung.get("changed_by"))
        if not args.probelauf:
            schreiben(config.DATA_DIR / "status.json", {
                "letzter_lauf": dt.datetime.now(dt.timezone.utc).isoformat(
                    timespec="seconds"),
                "lauf": "vorboerse", "ergebnis": "pausiert",
                "steuerung": steuerung,
            })
        return 0

    # 2. Handelt die Boerse heute ueberhaupt?
    if not args.auch_ohne_handelstag and not alpaca.is_trading_day(heute):
        log.info("%s ist kein Handelstag — Ende", heute)
        return 0

    # 3. Daten. Bewusst nur BIS GESTERN: der Lauf startet 75 Minuten vor der
    #    Eroeffnung, aber geplante Laeufe koennen sich um eine halbe Stunde
    #    verspaeten. Ohne diese Grenze koennte bei einem verspaeteten Lauf ein
    #    angefangener Tagesbar als "Schlusskurs" in die Indikatoren geraten —
    #    ein Blick in die Zukunft, den kein Backtest je aufdecken wuerde,
    #    weil er nur im Livebetrieb entsteht.
    #
    #    So gilt live dieselbe Regel wie im Backtest: Analyse auf dem Schluss
    #    von gestern, Ausfuehrung zur Eroeffnung von heute.
    u = universe.get()
    log.info("Universum %d Titel (gebaut %s)", u["count"], u["built_at"])
    syms = [m["symbol"] for m in u["symbols"]]
    gestern = heute - dt.timedelta(days=1)
    bars = alpaca.daily_bars(
        syms + [config.BENCHMARK],
        heute - dt.timedelta(days=config.HISTORY_DAYS), end=gestern)
    if len(bars) < 50:
        log.error("Nur %d Symbole mit Bars — Abbruch", len(bars))
        return 1
    vix = yahoo.chart_closes("^VIX", "1mo")

    # 4. Nachrichten und Sprachmodell. Beide duerfen ausfallen.
    ollama = llm.Ollama()
    modell_da = ollama.probe()
    log.info("Sprachmodell %s", "bereit" if modell_da else "nicht erreichbar")
    nachrichten: dict = {}

    def llm_fn(shortlist: list[dict]) -> dict:
        nonlocal nachrichten
        nachrichten = news.build([e["symbol"] for e in shortlist])
        if not modell_da:
            return {}
        return llm.analyse_symbols(ollama, shortlist,
                                   nachrichten.get("by_symbol", {}))

    # 5. Analyse mit den gelernten Gewichten
    gewichte = learning.load()
    res = analysis.run(
        u, bars, bars.get(config.BENCHMARK, []), heute,
        llm_fn=llm_fn,
        weights=gewichte["score_weights"],
        method_weights=gewichte["target_method_weights"],
        k_mult_by_sector=gewichte["sector_k_mult"],
        vix_closes=vix,
        fundamentals_budget=args.budget,
    )
    log.info("Bewertet %d, handelbar %d, Laufzeit %.0f s",
             res["scored"], len(res["kandidaten"]), res["seconds"])
    letzter = (bars.get(config.BENCHMARK) or [{}])[-1].get("t", "")[:10]
    if letzter >= heute.isoformat():
        log.error("Ein Bar von heute (%s) ist in die Analyse geraten — "
                  "Abbruch, das waere ein Blick in die Zukunft", letzter)
        return 1
    log.info("Analyse auf dem Schluss von %s, Ausfuehrung zur Eroeffnung "
             "von %s", letzter, heute)

    if not nachrichten:
        nachrichten = news.build([])
    digest = (llm.market_digest(ollama, news.digest_payload(
        nachrichten.get("market", []))) if modell_da else None)

    # 6. Auswahl und Auftraege. Beide Depots ziehen aus DEMSELBEN Topf;
    #    nur die Auswahlregel unterscheidet sie.
    regime = res["regime"]
    pool = res["kandidaten"]
    for e in pool:
        e["_auftrag"] = als_auftrag(e, regime)

    pf_ki = portfolio.load("ki")
    pf_zufall = portfolio.load("zufall")

    ki_picks = [e["_auftrag"] for e in analysis.select_picks(
        pool, belegt=portfolio.offene_titel(pf_ki))]
    zufall_picks = portfolio.random_picks(
        [e["_auftrag"] for e in pool], config.PICKS_PER_DAY, heute,
        belegt=portfolio.offene_titel(pf_zufall))
    portfolio.place_orders(pf_ki, ki_picks, heute)
    portfolio.place_orders(pf_zufall, zufall_picks, heute)
    log.info("Auftraege — KI: %s | Zufall: %s",
             [p["symbol"] for p in ki_picks],
             [p["symbol"] for p in zufall_picks])

    ideen = [e for e in pool if e["symbol"] in {p["symbol"] for p in ki_picks}]
    stats = {"ki": portfolio.statistics(pf_ki),
             "zufall": portfolio.statistics(pf_zufall)}

    # 7. Schreiben
    if args.probelauf:
        print(json.dumps([kompakt(e) for e in pool[:10]], indent=1,
                         ensure_ascii=False))
        log.info("Probelauf — nichts geschrieben, nichts gesendet "
                 "(%.0f s)", time.time() - gestartet)
        return 0

    cal = calibration.get()
    seite = {
        "date": heute.isoformat(),
        "generated_at": res["generated_at"],
        "regime": regime,
        "kalibrierung": res.get("kalibrierung"),
        "universe_size": res["universe_size"],
        # Damit die Seite die Depotregeln nicht doppelt kennen muss.
        "position_pct": config.POSITION_PCT,
        "picks_per_day": config.PICKS_PER_DAY,
        "scored": res["scored"],
        "excluded": res["excluded"],
        "sector_median_pe": res["sector_median_pe"],
        "basisquote_gesamt": calibration.base_rate(cal, "_gesamt"),
        "ideen": [vollstaendig(e) for e in ideen],
        "vorauswahl": [vollstaendig(e) for e in res["shortlist"]],
        "universum": [kompakt(e) for e in res["all_scored"]],
        "kaeufe": {"ki": ki_picks, "zufall": zufall_picks},
        "statistik": stats,
        "seconds": round(time.time() - gestartet, 1),
    }
    schreiben(config.DATA_DIR / "latest.json", seite)
    schreiben(config.ARCHIVE_DIR / f"{heute.isoformat()}.json", {
        k: v for k, v in seite.items() if k not in ("universum", "vorauswahl")})
    schreiben(config.DATA_DIR / "news.json", nachrichten)
    portfolio.save(pf_ki)
    portfolio.save(pf_zufall)
    schreiben(config.DATA_DIR / "status.json", {
        "letzter_lauf": dt.datetime.now(dt.timezone.utc).isoformat(
            timespec="seconds"),
        "lauf": "vorboerse", "ergebnis": "fertig",
        "handelbare_ideen": len(pool),
        "sprachmodell": modell_da,
        "sekunden": round(time.time() - gestartet, 1),
        "steuerung": steuerung,
    })

    # 8. Push. Ganz zum Schluss — die Daten stehen dann schon.
    if not args.kein_push:
        titel, text = notify.format_morning(
            heute, ideen, digest, stats, DASHBOARD_URL,
            headlines=nachrichten.get("market"))
        notify.send(titel, text, tags=["chart_with_upwards_trend"],
                    click=DASHBOARD_URL)

    log.info("Lauf A fertig in %.0f s", time.time() - gestartet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
