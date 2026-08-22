"""Yahoo-Finance-Fundamentaldaten: Analystenziele, Bewertung, Qualitaet.

Yahoo verlangt einen Cookie plus einen "Crumb"-Token, bevor quoteSummary
antwortet. Der Handshake wird einmal pro Prozess gemacht und danach
wiederverwendet.

Das ist eine INOFFIZIELLE Schnittstelle. Sie kann sich ohne Ankuendigung
aendern oder Rechenzentrums-IPs blockieren. Deshalb:
  - jeder Abruf ist einzeln gekapselt und gibt bei Fehlschlag None zurueck
  - Ergebnisse liegen eine Woche im Plattencache
  - der Lauf geht ohne Fundamentaldaten weiter, die Karten werden markiert
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

import requests

from . import config, net

log = logging.getLogger(__name__)

CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"
COOKIE_URL = "https://fc.yahoo.com"
SUMMARY_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{sym}"
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"

MODULES = "assetProfile,defaultKeyStatistics,financialData,calendarEvents,price"
CACHE_TTL_DAYS = 7
POLITE_DELAY = 0.4      # Sekunden zwischen Abrufen

_crumb: Optional[str] = None
_crumb_failed = False


# Ein einziger Speicher statt vieler Einzeldateien: so laesst er sich in der
# Cloud als eine Datei versionieren und ueberlebt jeden Lauf.
_store: dict | None = None
_store_dirty = False


def _store_path() -> Path:
    return config.DATA_DIR / "fundamentals.json"


def load_store() -> dict:
    global _store
    if _store is not None:
        return _store
    p = _store_path()
    if p.exists():
        try:
            _store = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("Fundamentaldaten-Speicher unlesbar — beginne neu")
            _store = {}
    else:
        _store = {}
    return _store


def save_store() -> None:
    """Nur schreiben, wenn sich etwas geaendert hat."""
    global _store_dirty
    if not _store_dirty or _store is None:
        return
    _store_path().parent.mkdir(parents=True, exist_ok=True)
    _store_path().write_text(json.dumps(_store, separators=(",", ":")),
                             encoding="utf-8")
    _store_dirty = False
    log.info("Fundamentaldaten gespeichert: %d Titel", len(_store))


def store_age_days(symbol: str) -> float | None:
    entry = load_store().get(symbol.upper())
    if not entry:
        return None
    return (time.time() - entry.get("_fetched_at", 0)) / 86400


def to_yahoo_symbol(symbol: str) -> str:
    """Alpaca schreibt BRK.B, Yahoo schreibt BRK-B."""
    return symbol.replace(".", "-")


def _handshake() -> Optional[str]:
    """Cookie holen, dann Crumb. Einmal pro Prozess."""
    global _crumb, _crumb_failed
    if _crumb:
        return _crumb
    if _crumb_failed:
        return None
    s = net.session()
    try:
        s.get(COOKIE_URL, timeout=10)
    except requests.RequestException:
        pass                        # setzt trotzdem oft das Cookie
    try:
        r = s.get(CRUMB_URL, timeout=10)
        if r.status_code == 200 and r.text and len(r.text) < 40:
            _crumb = r.text.strip()
            return _crumb
        log.warning("Yahoo-Crumb fehlgeschlagen: HTTP %s", r.status_code)
    except requests.RequestException as e:
        log.warning("Yahoo-Crumb fehlgeschlagen: %s", e)
    _crumb_failed = True
    return None


def _raw(v: Any) -> Optional[float]:
    """Yahoo verpackt Zahlen als {'raw': 1.23, 'fmt': '1.23'}."""
    if isinstance(v, dict):
        v = v.get("raw")
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def fundamentals(symbol: str, max_age_days: int = CACHE_TTL_DAYS,
                 use_cache: bool = True) -> Optional[dict]:
    """Normalisierte Fundamentaldaten oder None.

    None bedeutet: keine Daten verfuegbar. Jeder Aufrufer muss damit umgehen
    koennen, statt sich auf Yahoo zu verlassen.
    """
    global _store_dirty
    sym = symbol.upper()
    store = load_store()

    cached = store.get(sym)
    if use_cache and cached:
        age = time.time() - cached.get("_fetched_at", 0)
        if age < max_age_days * 86400:
            return cached if cached.get("_ok") else None

    data = _fetch(sym)
    payload = dict(data) if data is not None else {"_ok": False, "symbol": sym}
    payload["_fetched_at"] = time.time()
    payload.setdefault("_ok", data is not None)
    store[sym] = payload
    _store_dirty = True
    return data


def _fetch(sym: str) -> Optional[dict]:
    crumb = _handshake()
    if not crumb:
        return None
    time.sleep(POLITE_DELAY)
    try:
        r = net.session().get(
            SUMMARY_URL.format(sym=to_yahoo_symbol(sym)),
            params={"modules": MODULES, "crumb": crumb},
            timeout=config.HTTP_TIMEOUT,
        )
    except requests.RequestException as e:
        log.debug("Yahoo %s: %s", sym, e)
        return None
    if r.status_code != 200:
        log.debug("Yahoo %s: HTTP %s", sym, r.status_code)
        return None
    try:
        result = r.json()["quoteSummary"]["result"]
    except (ValueError, KeyError, TypeError):
        return None
    if not result:
        return None
    return _normalise(sym, result[0])


def _normalise(sym: str, d: dict) -> dict:
    profile = d.get("assetProfile") or {}
    stats = d.get("defaultKeyStatistics") or {}
    fin = d.get("financialData") or {}
    cal = d.get("calendarEvents") or {}
    price = d.get("price") or {}

    earnings_dates = ((cal.get("earnings") or {}).get("earningsDate") or [])
    next_earnings = None
    for e in earnings_dates:
        ts = _raw(e)
        if ts:
            day = dt.datetime.fromtimestamp(ts, dt.timezone.utc).date()
            if next_earnings is None or day < next_earnings:
                next_earnings = day

    return {
        "_ok": True,
        "symbol": sym,
        "quote_type": price.get("quoteType"),
        "sector": profile.get("sector"),
        "industry": profile.get("industry"),
        "employees": profile.get("fullTimeEmployees"),
        # Analysten
        "target_mean": _raw(fin.get("targetMeanPrice")),
        "target_high": _raw(fin.get("targetHighPrice")),
        "target_low": _raw(fin.get("targetLowPrice")),
        "recommendation": fin.get("recommendationKey"),
        "analyst_count": _raw(fin.get("numberOfAnalystOpinions")),
        "current_price": _raw(fin.get("currentPrice")),
        # Qualitaet
        "profit_margin": _raw(fin.get("profitMargins")),
        "operating_margin": _raw(fin.get("operatingMargins")),
        "return_on_equity": _raw(fin.get("returnOnEquity")),
        "revenue_growth": _raw(fin.get("revenueGrowth")),
        "earnings_growth": _raw(fin.get("earningsGrowth")),
        "free_cashflow": _raw(fin.get("freeCashflow")),
        "total_debt": _raw(fin.get("totalDebt")),
        "total_cash": _raw(fin.get("totalCash")),
        "debt_to_equity": _raw(fin.get("debtToEquity")),
        # Bewertung
        "forward_pe": _raw(stats.get("forwardPE")),
        "trailing_pe": _raw(stats.get("trailingPE")),
        "forward_eps": _raw(stats.get("forwardEps")),
        "trailing_eps": _raw(stats.get("trailingEps")),
        "peg_ratio": _raw(stats.get("pegRatio")),
        "ev_to_ebitda": _raw(stats.get("enterpriseToEbitda")),
        "price_to_book": _raw(stats.get("priceToBook")),
        "market_cap": _raw(price.get("marketCap")),
        # Risiko
        "beta": _raw(stats.get("beta")),
        "next_earnings": next_earnings.isoformat() if next_earnings else None,
    }


def is_operating_company(symbol: str) -> bool:
    """True nur fuer echte Unternehmen — ETFs und Fonds haben keinen Sektor."""
    f = fundamentals(symbol)
    if not f:
        return False
    if (f.get("quote_type") or "").upper() not in ("EQUITY", ""):
        return False
    return bool(f.get("sector"))


def chart_closes(symbol: str, rng: str = "1y",
                 interval: str = "1d") -> list[tuple[str, float]]:
    """Schlusskurse ueber den Chart-Endpunkt — braucht keinen Crumb.

    Wird fuer ^VIX und als Notfall-Kursquelle benutzt.
    """
    try:
        r = net.session().get(
            CHART_URL.format(sym=symbol),
            params={"range": rng, "interval": interval},
            timeout=config.HTTP_TIMEOUT,
        )
        if r.status_code != 200:
            return []
        res = r.json()["chart"]["result"][0]
        stamps = res.get("timestamp") or []
        closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        return []
    out = []
    for ts, c in zip(stamps, closes):
        if c is None:
            continue
        day = dt.datetime.fromtimestamp(ts, dt.timezone.utc).date().isoformat()
        out.append((day, float(c)))
    return out
