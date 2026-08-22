"""Die Analyse-Pipeline: vom Universum zu den Tagesideen.

    Universum (532)
        v  Tagesbars, Indikatoren
    Kennzahlen je Titel
        v  harte Ausschluesse
    Bewertbare Titel
        v  Vorbewertung nur aus Kursdaten (schnell, kein Netz)
    Vorauswahl
        v  Fundamentaldaten (woechentlich, aus dem Speicher)
    Vollbewertung mit Branchenmedianen
        v  Sprachmodell auf die besten 25
    Endbewertung mit Sentiment
        v  Kursziele, Stop, Chance-Risiko
    Ideen des Tages

Das Sprachmodell laeuft bewusst erst am Schluss und nur auf einer kurzen
Liste: es ist der langsamste Schritt und darf nicht ueber 532 Titel laufen.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Callable, Optional

from . import (calibration, config, indicators as ind, scoring, targets,
               yahoo)

log = logging.getLogger(__name__)

# Nur aus Kursdaten berechenbare Komponenten — fuer die Vorbewertung.
TECHNICAL_KEYS = ("trend", "setup", "volumen")


def market_regime(benchmark_bars: list[dict],
                  vix_closes: Optional[list[tuple[str, float]]] = None) -> dict:
    """Marktumfeld als Merkmal fuer die Lernschleife — kein Filter.

    Die Anzahl Kaeufe bleibt konstant, sonst waere der Vergleich mit dem
    Zufallsdepot statistisch wertlos. Das Regime wird nur mitgeschrieben,
    damit spaeter auffaellt, ob die Analyse in bestimmten Phasen versagt.
    """
    snap = ind.snapshot(benchmark_bars) if benchmark_bars else {}
    close, sma200 = snap.get("close"), snap.get("sma200")
    trend = None
    if close and sma200:
        trend = "aufwaerts" if close > sma200 else "abwaerts"
    vix = vix_closes[-1][1] if vix_closes else None
    return {
        "benchmark": config.BENCHMARK,
        "benchmark_close": close,
        "benchmark_sma200": round(sma200, 2) if sma200 else None,
        "trend": trend,
        "gap_to_sma200": snap.get("gap_to_sma200"),
        "vix": round(vix, 2) if vix else None,
        "vix_level": (None if vix is None else
                      "ruhig" if vix < 16 else
                      "erhoeht" if vix < 25 else "gestresst"),
        "chg_21d": snap.get("chg_21d"),
    }


def build_entries(universe: dict, bars: dict[str, list[dict]],
                  benchmark_bars: list[dict]) -> tuple[list[dict], list[dict]]:
    """Kennzahlen je Titel berechnen und harte Ausschluesse anwenden.

    Gibt (bewertbare Eintraege, ausgeschlossene Eintraege) zurueck.
    """
    ok, excluded = [], []
    for member in universe["symbols"]:
        sym = member["symbol"]
        series = bars.get(sym) or []
        if not series:
            excluded.append({**member, "exclusions": ["Keine Kursdaten"]})
            continue
        snap = ind.snapshot(series, benchmark=benchmark_bars)
        reasons = scoring.hard_exclusions(snap, member)
        entry = {
            "symbol": sym,
            "name": member.get("name", sym),
            "sector": member.get("sector", "Unbekannt"),
            "source": member.get("source"),
            "snapshot": snap,
        }
        if reasons:
            entry["exclusions"] = reasons
            excluded.append(entry)
        else:
            ok.append(entry)
    return ok, excluded


def technical_prescore(entries: list[dict]) -> None:
    """Schnelle Vorbewertung nur aus Kursdaten — schreibt `prescore`.

    Dient allein dazu, die Liste fuer den teuren Fundamentaldaten-Abruf
    zu kuerzen. Sie ersetzt die Vollbewertung nicht.
    """
    weights = {k: config.SCORE_WEIGHTS[k] for k in TECHNICAL_KEYS}
    for e in entries:
        s = scoring.score(e["snapshot"], None, None, weights=weights)
        e["prescore"] = s["score"] if s["score"] is not None else 0.0


def refresh_fundamentals(symbols: list[str], budget: int = 600,
                         max_age_days: int = yahoo.CACHE_TTL_DAYS) -> dict:
    """Veraltete Fundamentaldaten nachladen, hoechstens `budget` Stueck.

    Das Budget verhindert, dass ein einzelner Lauf bei leerem Speicher
    unbegrenzt lange braucht. Was nicht mehr reinpasst, kommt beim
    naechsten Lauf dran.
    """
    started = time.time()
    stale = []
    for sym in symbols:
        age = yahoo.store_age_days(sym)
        if age is None or age >= max_age_days:
            stale.append(sym)

    fetched = failed = 0
    for sym in stale[:budget]:
        if yahoo.fundamentals(sym, max_age_days=max_age_days):
            fetched += 1
        else:
            failed += 1
    yahoo.save_store()

    stats = {
        "veraltet": len(stale),
        "geholt": fetched,
        "fehlgeschlagen": failed,
        "uebrig": max(0, len(stale) - budget),
        "sekunden": round(time.time() - started, 1),
    }
    if stale:
        log.info("Fundamentaldaten: %s", stats)
    return stats


def attach_fundamentals(entries: list[dict]) -> None:
    for e in entries:
        e["fundamentals"] = yahoo.fundamentals(e["symbol"],
                                               max_age_days=10 ** 6)


def full_score(entries: list[dict], sector_pes: dict[str, float],
               weights: Optional[dict] = None,
               today: Optional[dt.date] = None) -> None:
    """Vollbewertung mit allen verfuegbaren Komponenten."""
    for e in entries:
        e["scoring"] = scoring.score(
            e["snapshot"], e.get("fundamentals"), e.get("llm"),
            sector_median_pe=sector_pes.get(e["sector"]),
            weights=weights, today=today,
        )


def compute_targets(entries: list[dict], sector_pes: dict[str, float],
                    method_weights: Optional[dict] = None,
                    k_mult_by_sector: Optional[dict] = None,
                    cal: Optional[dict] = None) -> None:
    """Kursziele fuer alle Eintraege.

    `cal` ist die Kalibrierung. Ohne sie greifen die Rueckfallwerte aus
    config; die Karten zeigen dann keine gemessenen Wahrscheinlichkeiten.
    """
    if cal is None:
        cal = calibration.get()
    for e in entries:
        snap = e["snapshot"]
        e["targets"] = targets.build(
            snap["close"], snap, e.get("fundamentals"),
            weights=method_weights,
            sector=e["sector"],
            sector_median_pe=sector_pes.get(e["sector"]),
            k_mult=(k_mult_by_sector or {}).get(e["sector"]),
            cal=cal,
        )


def tradeable(entry: dict) -> tuple[bool, str]:
    """Taugt der Titel als Idee? Gibt (ja/nein, Begruendung) zurueck.

    Drei Tore, aufsteigend nach Aussagekraft:

      1  Chance-Risiko  — nur noch eine Plausibilitaetsschwelle. Bei den
         kalibrierten Marken liegt es bauartbedingt bei rund 1.2; eine hohe
         Huerde wuerde hier nur die Titel mit besonders engem Stop bevorzugen.
      2  Trefferwahrscheinlichkeit — wurde ein Ziel in dieser Entfernung
         historisch ueberhaupt erreicht? Faengt Ziele ab, die zu weit liegen.
      3  Basisquote — traegt die Marken-Geometrie schon fuer eine
         ZUFALLSAUSWAHL? Ist sie negativ, muesste die Auswahl den Nachteil
         erst aufholen, bevor sie etwas verdient.
    """
    sc = entry.get("scoring") or {}
    tg = entry.get("targets") or {}
    if not sc.get("eligible"):
        return False, (f"Datenabdeckung {sc.get('coverage', 0):.0%} unter "
                       f"{scoring.MIN_DATA_COVERAGE:.0%}")
    if tg.get("target") is None:
        return False, "Kein Kursziel berechenbar"
    if tg.get("reward_risk") is None:
        return False, "Kein Chance-Risiko-Verhaeltnis berechenbar"
    if tg.get("upside_pct", 0) <= 0:
        return False, "Kursziel liegt nicht ueber dem Kurs"
    if tg["reward_risk"] < config.MIN_REWARD_RISK:
        return False, (f"Chance-Risiko {tg['reward_risk']:.2f} unter "
                       f"{config.MIN_REWARD_RISK:.2f}")
    p = tg.get("p_ziel_beruehrt")
    if p is not None and p < config.MIN_HIT_PROBABILITY:
        return False, (f"Ziel historisch nur in {p:.0%} der Faelle erreicht "
                       f"(mindestens {config.MIN_HIT_PROBABILITY:.0%})")
    bq = tg.get("basisquote")
    if bq and bq["erwartung_r"] < config.MIN_BASE_EXPECTANCY_R:
        return False, (f"Basisquote dieser Marken {bq['erwartung_r']:+.3f} R "
                       f"- schon fuer eine Zufallsauswahl ein Verlustgeschaeft")
    return True, ""


def select_picks(ideas: list[dict], n: Optional[int] = None,
                 max_per_sector: Optional[int] = None,
                 belegt: Optional[set] = None) -> list[dict]:
    """Die besten Ideen auswaehlen, mit Branchendeckel.

    Der Deckel ist Portfoliokonstruktion, kein Analyseschritt: er verhindert,
    dass ein Tag zufaellig zur Sektorwette wird. Das Zufallsdepot streut
    ohnehin ueber alle Branchen — ohne Deckel waere der Vergleich verzerrt.

    `belegt` sind Titel, die schon im Depot liegen oder als Auftrag warten.
    Sie werden UEBERSPRUNGEN, nicht einfach verworfen — sonst kauft das
    KI-Depot weniger als das Zufallsdepot, weil die Analyse dieselben Titel
    tagelang oben haelt. Im Backtest gemessen: 588 gegen 685 Trades. Ein
    Vergleich der Depotrenditen waere damit ein Vergleich der eingesetzten
    Mittel, nicht der Auswahl.
    """
    n = n or config.PICKS_PER_DAY
    max_per_sector = (config.MAX_PICKS_PER_SECTOR if max_per_sector is None
                      else max_per_sector)
    belegt = belegt or set()
    picks: list[dict] = []
    per_sector: dict[str, int] = {}
    uebergangen: list[dict] = []

    for e in ideas:
        if len(picks) >= n:
            break
        if e["symbol"] in belegt:
            continue
        sector = e.get("sector", "Unbekannt")
        if per_sector.get(sector, 0) >= max_per_sector:
            uebergangen.append({"symbol": e["symbol"], "sector": sector,
                                "grund": f"schon {max_per_sector} aus {sector}"})
            continue
        per_sector[sector] = per_sector.get(sector, 0) + 1
        picks.append(e)

    for p in picks:
        p["sector_cap_skipped"] = uebergangen
    return picks


def run(universe: dict, bars: dict[str, list[dict]],
        benchmark_bars: list[dict], today: dt.date,
        llm_fn: Optional[Callable[[list[dict]], dict]] = None,
        weights: Optional[dict] = None,
        method_weights: Optional[dict] = None,
        k_mult_by_sector: Optional[dict] = None,
        vix_closes: Optional[list] = None,
        fundamentals_budget: int = 600,
        shortlist_size: Optional[int] = None) -> dict:
    """Die vollstaendige Tagesanalyse.

    `llm_fn` bekommt die Vorauswahl und gibt {symbol: {sentiment, these, ...}}
    zurueck. Fehlt sie, laeuft alles ohne Sentiment weiter — der Backtest
    nutzt genau diesen Weg.
    """
    started = time.time()
    shortlist_size = shortlist_size or config.SHORTLIST_SIZE

    entries, excluded = build_entries(universe, bars, benchmark_bars)
    log.info("Bewertbar: %d Titel, ausgeschlossen: %d", len(entries), len(excluded))

    technical_prescore(entries)
    entries.sort(key=lambda e: e["prescore"], reverse=True)

    fund_stats = refresh_fundamentals([e["symbol"] for e in entries],
                                      budget=fundamentals_budget)
    attach_fundamentals(entries)

    sector_pes = scoring.sector_median_pes(entries)
    log.info("Branchenmediane (Forward-KGV): %s",
             {k: round(v, 1) for k, v in sorted(sector_pes.items())})

    full_score(entries, sector_pes, weights, today)
    entries.sort(key=lambda e: (e["scoring"]["score"] or 0), reverse=True)

    # Sprachmodell nur auf die Vorauswahl — der teuerste Schritt.
    bewertbar = [e for e in entries if e["scoring"]["eligible"]]
    shortlist = bewertbar[:shortlist_size]
    if llm_fn and shortlist:
        llm_results = llm_fn(shortlist)
        for e in shortlist:
            e["llm"] = llm_results.get(e["symbol"])
        full_score(shortlist, sector_pes, weights, today)
        bewertbar.sort(key=lambda e: (e["scoring"]["score"] or 0), reverse=True)

    # Kursziele fuer ALLE bewertbaren Titel, nicht nur die Vorauswahl. Das
    # kostet nichts (reine Arithmetik) und ist zweimal wichtig: die Seite
    # zeigt das ganze Universum mit Zielen, und das Zufallsdepot zieht aus
    # demselben Topf wie die Analyse — sonst waere der Vergleich verzerrt.
    cal = calibration.get()
    compute_targets(bewertbar, sector_pes, method_weights,
                    k_mult_by_sector, cal)
    for e in bewertbar:
        ok, why = tradeable(e)
        e["tradeable"] = ok
        e["reject_reason"] = why

    handelbar = [e for e in bewertbar if e["tradeable"]]
    handelbar.sort(key=lambda e: (e["scoring"]["score"] or 0), reverse=True)
    ideas = handelbar

    return {
        "date": today.isoformat(),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "regime": market_regime(benchmark_bars, vix_closes),
        "universe_size": len(universe["symbols"]),
        "scored": len(entries),
        "excluded": len(excluded),
        "exclusion_examples": excluded[:10],
        "fundamentals": fund_stats,
        "sector_median_pe": {k: round(v, 2) for k, v in sector_pes.items()},
        "kalibrierung": (None if not cal else {
            "gemessen_am": cal["measured_at"],
            "beobachtungen": cal["observations"],
            "horizont": cal["horizon_days"],
            "text": calibration.summary_line(cal),
        }),
        "shortlist": shortlist,
        "ideas": ideas,
        "kandidaten": handelbar,
        "all_scored": entries,
        "seconds": round(time.time() - started, 1),
    }
