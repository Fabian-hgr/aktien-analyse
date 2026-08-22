"""Alpaca-Zugriff: Uhr, Kalender, Wertpapierliste, Tagesbars, Nachrichten.

Nur der Gratis-Tarif wird benutzt (IEX-Feed). Die Schluessel kommen aus der
Umgebung und erscheinen nie in Logs — sie stehen ausschliesslich im Header,
und net._safe() schneidet Query-Strings aus Fehlermeldungen heraus.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Iterable

from . import config, net

log = logging.getLogger(__name__)

BAR_CHUNK = 200          # Symbole pro Anfrage
NEWS_CHUNK = 40


def _headers() -> dict:
    config.load_local_secrets()
    if not config.ALPACA_KEY or not config.ALPACA_SECRET:
        raise RuntimeError(
            "ALPACA_KEY / ALPACA_SECRET fehlen. Lokal: Trading-Bot-Datei, "
            "in der Cloud: GitHub Secrets."
        )
    return {
        "APCA-API-KEY-ID": config.ALPACA_KEY,
        "APCA-API-SECRET-KEY": config.ALPACA_SECRET,
    }


# -- Marktzeiten -----------------------------------------------------------

def clock() -> dict:
    """Marktstatus: is_open, next_open, next_close, timestamp."""
    return net.get_json(f"{config.ALPACA_TRADING}/clock", headers=_headers())


def calendar(start: dt.date, end: dt.date) -> list[dict]:
    """Handelstage samt Oeffnungs- und Schlusszeit. Feiertage fehlen darin."""
    return net.get_json(
        f"{config.ALPACA_TRADING}/calendar",
        params={"start": start.isoformat(), "end": end.isoformat()},
        headers=_headers(),
    )


def is_trading_day(day: dt.date) -> bool:
    return any(c["date"] == day.isoformat() for c in calendar(day, day))


def trading_days(start: dt.date, end: dt.date) -> list[dt.date]:
    return [dt.date.fromisoformat(c["date"]) for c in calendar(start, end)]


# -- Wertpapierliste -------------------------------------------------------

def tradable_equities() -> list[dict]:
    """Aktive, handelbare US-Aktien an NASDAQ / NYSE / ARCA.

    Filtert aus, was fuer eine Swing-Analyse ungeeignet ist: Symbole mit
    Punkten oder Bindestrichen (Vorzugsaktien, Bezugsrechte, Einheiten).
    """
    raw = net.get_json(
        f"{config.ALPACA_TRADING}/assets",
        params={"status": "active", "asset_class": "us_equity"},
        headers=_headers(),
        timeout=90.0,
    )
    out = []
    for a in raw:
        sym = a.get("symbol", "")
        if not a.get("tradable"):
            continue
        if a.get("exchange") not in ("NASDAQ", "NYSE", "ARCA"):
            continue
        if not sym.isalpha() or len(sym) > 5:
            continue
        out.append({
            "symbol": sym,
            "name": a.get("name", ""),
            "exchange": a.get("exchange"),
            "shortable": bool(a.get("shortable")),
            "fractionable": bool(a.get("fractionable")),
        })
    return out


# -- Kursdaten -------------------------------------------------------------

def daily_bars(
    symbols: Iterable[str],
    start: dt.date,
    end: dt.date | None = None,
) -> dict[str, list[dict]]:
    """Tagesbars je Symbol, aufsteigend nach Datum sortiert.

    Fasst bis zu 200 Symbole pro Anfrage zusammen und folgt der Seitennummer,
    bis Alpaca keine weitere meldet. Kurse sind um Splits und Dividenden
    bereinigt (adjustment=all) — sonst erzeugt jeder Split ein Scheinsignal
    im Indikator.
    """
    symbols = [s.upper() for s in symbols]
    merged: dict[str, list[dict]] = {}

    for i in range(0, len(symbols), BAR_CHUNK):
        chunk = symbols[i:i + BAR_CHUNK]
        page_token: str | None = None
        while True:
            params = {
                "symbols": ",".join(chunk),
                "timeframe": "1Day",
                "start": start.isoformat(),
                "limit": 10000,
                "feed": config.ALPACA_FEED,
                "adjustment": "all",
            }
            if end:
                params["end"] = end.isoformat()
            if page_token:
                params["page_token"] = page_token

            payload = net.get_json(
                f"{config.ALPACA_DATA}/v2/stocks/bars",
                params=params, headers=_headers(), timeout=90.0,
            )
            for sym, bars in (payload.get("bars") or {}).items():
                merged.setdefault(sym, []).extend(bars)

            page_token = payload.get("next_page_token")
            if not page_token:
                break

    for sym in merged:
        merged[sym].sort(key=lambda b: b["t"])
    return merged


def latest_trades(symbols: Iterable[str]) -> dict[str, float]:
    """Letzter Handelspreis je Symbol. Fuer die Bewertung offener Positionen."""
    symbols = [s.upper() for s in symbols]
    out: dict[str, float] = {}
    for i in range(0, len(symbols), BAR_CHUNK):
        chunk = symbols[i:i + BAR_CHUNK]
        payload = net.get_json(
            f"{config.ALPACA_DATA}/v2/stocks/trades/latest",
            params={"symbols": ",".join(chunk), "feed": config.ALPACA_FEED},
            headers=_headers(),
        )
        for sym, trade in (payload.get("trades") or {}).items():
            out[sym] = float(trade["p"])
    return out


# -- Nachrichten -----------------------------------------------------------

def news(symbols: Iterable[str], hours_back: int = 36,
         limit: int = 50) -> list[dict]:
    """Nachrichten der letzten Stunden zu den genannten Symbolen."""
    symbols = [s.upper() for s in symbols]
    if not symbols:
        return []
    start = (dt.datetime.now(dt.timezone.utc)
             - dt.timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    items: list[dict] = []

    for i in range(0, len(symbols), NEWS_CHUNK):
        chunk = symbols[i:i + NEWS_CHUNK]
        page_token: str | None = None
        pages = 0
        while pages < 5:
            params = {
                "symbols": ",".join(chunk),
                "start": start,
                "limit": min(50, limit),
                "sort": "desc",
            }
            if page_token:
                params["page_token"] = page_token
            payload = net.get_json(
                f"{config.ALPACA_DATA}/v1beta1/news",
                params=params, headers=_headers(),
            )
            items.extend(payload.get("news") or [])
            page_token = payload.get("next_page_token")
            pages += 1
            if not page_token:
                break
    return items


def news_by_symbol(symbols: Iterable[str], per_symbol: int = 6,
                   hours_back: int = 36) -> dict[str, list[dict]]:
    """Nachrichten nach Symbol gruppiert, je die neuesten `per_symbol`."""
    wanted = {s.upper() for s in symbols}
    grouped: dict[str, list[dict]] = {}
    for it in news(wanted, hours_back=hours_back):
        headline = (it.get("headline") or "").strip()
        if not headline:
            continue
        for sym in it.get("symbols") or []:
            if sym not in wanted:
                continue
            bucket = grouped.setdefault(sym, [])
            if len(bucket) < per_symbol:
                bucket.append({
                    "headline": headline,
                    "summary": (it.get("summary") or "")[:400],
                    "ts": it.get("created_at"),
                    "url": it.get("url"),
                    "source": it.get("source"),
                })
    return grouped
