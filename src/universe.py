"""Das Anlageuniversum: welche Aktien ueberhaupt analysiert werden.

Aufbau in zwei Schichten:

  1. Grundstock — die S&P-500-Mitglieder. Quelle ist Wikipedia, weil dort
     Symbol UND GICS-Sektor in einer Tabelle stehen. Faellt Wikipedia aus,
     greift ein gepflegtes CSV-Dataset, danach der letzte Cache.

  2. Erweiterung — die liquidesten US-Aktien, die nicht im Index sind
     (Nasdaq-100-Zugaenge, junge Grosswerte, ADRs). Sie werden nach
     IEX-Dollarvolumen sortiert und ueber Yahoo geprueft: nur echte
     Unternehmen mit Sektor, keine ETFs.

Das Universum wird woechentlich neu gebaut und liegt im Plattencache.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import logging
import re
import time

from . import alpaca, config, net, yahoo

log = logging.getLogger(__name__)

WIKI_SP500 = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
CSV_SP500 = ("https://raw.githubusercontent.com/datasets/"
             "s-and-p-500-companies/main/data/constituents.csv")

# GICS- und Yahoo-Sektornamen auf gemeinsame deutsche Labels abbilden.
SECTOR_DE = {
    "information technology": "Technologie",
    "technology": "Technologie",
    "health care": "Gesundheit",
    "healthcare": "Gesundheit",
    "financials": "Finanzen",
    "financial services": "Finanzen",
    "consumer discretionary": "Konsum zyklisch",
    "consumer cyclical": "Konsum zyklisch",
    "consumer staples": "Konsum defensiv",
    "consumer defensive": "Konsum defensiv",
    "communication services": "Kommunikation",
    "industrials": "Industrie",
    "energy": "Energie",
    "utilities": "Versorger",
    "real estate": "Immobilien",
    "materials": "Grundstoffe",
    "basic materials": "Grundstoffe",
}
SECTOR_UNKNOWN = "Unbekannt"

ALL_SECTORS = [
    "Technologie", "Gesundheit", "Finanzen", "Konsum zyklisch",
    "Konsum defensiv", "Kommunikation", "Industrie", "Energie",
    "Versorger", "Immobilien", "Grundstoffe",
]


def normalise_sector(name: str | None) -> str:
    if not name:
        return SECTOR_UNKNOWN
    return SECTOR_DE.get(name.strip().lower(), SECTOR_UNKNOWN)


# -- Quellen fuer den Grundstock -------------------------------------------

def _sp500_from_wikipedia() -> dict[str, dict]:
    html = net.get_text(WIKI_SP500)
    if not html:
        return {}
    m = re.search(r'<table[^>]*id="constituents".*?</table>', html, re.S)
    if not m:
        return {}
    out: dict[str, dict] = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(0), re.S)[1:]:
        cells = [
            re.sub(r"<[^>]+>", "", c).replace("&amp;", "&").strip()
            for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
        ]
        if len(cells) < 3 or not cells[0]:
            continue
        sym = cells[0].replace(".", "").upper()
        out[sym] = {
            "symbol": sym,
            "name": cells[1],
            "sector": normalise_sector(cells[2]),
        }
    return out


def _sp500_from_csv() -> dict[str, dict]:
    text = net.get_text(CSV_SP500)
    if not text:
        return {}
    out: dict[str, dict] = {}
    for row in csv.DictReader(io.StringIO(text)):
        sym = (row.get("Symbol") or "").replace(".", "").upper()
        if not sym:
            continue
        out[sym] = {
            "symbol": sym,
            "name": row.get("Security", ""),
            "sector": normalise_sector(row.get("GICS Sector")),
        }
    return out


def _sp500() -> dict[str, dict]:
    for label, fn in (("Wikipedia", _sp500_from_wikipedia),
                      ("CSV-Dataset", _sp500_from_csv)):
        try:
            members = fn()
        except Exception as e:                      # noqa: BLE001
            log.warning("S&P-500-Quelle %s fehlgeschlagen: %s", label, e)
            continue
        if len(members) >= 400:
            log.info("S&P 500 aus %s: %d Mitglieder", label, len(members))
            return members
        log.warning("S&P-500-Quelle %s lieferte nur %d Zeilen", label, len(members))
    return {}


# -- Aufbau ----------------------------------------------------------------

def _cache_path():
    """Das Universum liegt im versionierten Datenordner, nicht im Cache.

    Es ist Zustand, kein Zwischenergebnis: wer wann in den Index kam oder ihn
    verliess, gehoert in den Git-Verlauf. Nebenbei startet der Lauf in der
    Cloud damit sofort — ein Neuaufbau dauert rund drei Minuten.
    """
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return config.DATA_DIR / "universe.json"


def dollar_volumes(symbols: list[str], days: int = 20) -> dict[str, dict]:
    """Durchschnittliches IEX-Dollarvolumen und letzter Kurs je Symbol."""
    bars = alpaca.daily_bars(symbols, dt.date.today() - dt.timedelta(days=days + 15))
    out: dict[str, dict] = {}
    for sym, series in bars.items():
        if len(series) < max(10, days // 2):
            continue
        window = series[-days:]
        out[sym] = {
            "dollar_volume": sum(b["c"] * b["v"] for b in window) / len(window),
            "last_close": window[-1]["c"],
            "bars_available": len(series),
        }
    return out


def build(verify_extension: bool = True) -> dict:
    """Universum neu aufbauen und in den Cache schreiben."""
    started = time.time()
    base = _sp500()
    if not base:
        cached = load(max_age_days=10 ** 6)
        if cached:
            log.error("Alle Indexquellen aus — benutze Cache vom %s",
                      cached.get("built_at"))
            return cached
        raise RuntimeError("Keine Indexquelle erreichbar und kein Cache vorhanden.")

    tradable = {a["symbol"]: a for a in alpaca.tradable_equities()}
    stats = dollar_volumes(list(tradable))
    log.info("Liquiditaet gemessen fuer %d Symbole", len(stats))

    def passes(sym: str) -> bool:
        s = stats.get(sym)
        return bool(
            s
            and s["last_close"] >= config.MIN_PRICE
            and s["dollar_volume"] >= config.MIN_DOLLAR_VOLUME
        )

    members: list[dict] = []
    for sym, info in base.items():
        if sym in tradable and passes(sym):
            members.append({**info, "source": "sp500", **stats[sym]})

    dropped = len(base) - len(members)
    log.info("Grundstock: %d von %d Indexmitgliedern (%d ausgefiltert)",
             len(members), len(base), dropped)

    # Erweiterung: liquideste Nicht-Indexwerte, ETFs per Yahoo aussortieren
    have = {m["symbol"] for m in members}
    candidates = sorted(
        (s for s in stats if s not in have and passes(s)),
        key=lambda s: stats[s]["dollar_volume"],
        reverse=True,
    )
    need = max(0, config.UNIVERSE_SIZE - len(members))
    checked = 0
    for sym in candidates:
        if need <= 0 or checked >= need * 4:
            break
        checked += 1
        if verify_extension:
            f = yahoo.fundamentals(sym)
            if not f or not f.get("sector"):
                continue
            if (f.get("quote_type") or "").upper() not in ("EQUITY", ""):
                continue
            sector = normalise_sector(f.get("sector"))
        else:
            sector = SECTOR_UNKNOWN
        members.append({
            "symbol": sym,
            "name": tradable.get(sym, {}).get("name", sym),
            "sector": sector,
            "source": "liquid",
            **stats[sym],
        })
        need -= 1

    members.sort(key=lambda m: m["dollar_volume"], reverse=True)
    universe = {
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "count": len(members),
        "min_price": config.MIN_PRICE,
        "min_dollar_volume": config.MIN_DOLLAR_VOLUME,
        "note": "Dollarvolumen aus dem IEX-Feed, rund 3-5 % des Gesamtmarkts.",
        "symbols": members,
    }
    _cache_path().write_text(json.dumps(universe, indent=1), encoding="utf-8")
    log.info("Universum gebaut: %d Titel in %.1f s",
             len(members), time.time() - started)
    return universe


def load(max_age_days: int | None = None) -> dict | None:
    """Universum aus dem Cache, sofern nicht zu alt."""
    max_age_days = (config.UNIVERSE_MAX_AGE_DAYS
                    if max_age_days is None else max_age_days)
    p = _cache_path()
    if not p.exists():
        return None
    try:
        u = json.loads(p.read_text(encoding="utf-8"))
        built = dt.datetime.fromisoformat(u["built_at"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None
    age = dt.datetime.now(dt.timezone.utc) - built
    return u if age.days < max_age_days else None


def get(force_rebuild: bool = False) -> dict:
    """Universum holen — aus dem Cache, sonst neu bauen."""
    if not force_rebuild:
        cached = load()
        if cached:
            return cached
    return build()


def sector_counts(universe: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in universe["symbols"]:
        counts[m["sector"]] = counts.get(m["sector"], 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
