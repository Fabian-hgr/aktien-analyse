"""Nachrichten: pro Titel von Alpaca, fuer den Markt aus RSS-Feeds.

Die Feeds sind bewusst mehrere und unabhaengig voneinander. Faellt einer aus,
laeuft der Lauf mit den uebrigen weiter — Nachrichten sind Beiwerk, kein
Grund, die Analyse abzubrechen.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import xml.etree.ElementTree as ET
from typing import Iterable, Optional

from . import alpaca, net

log = logging.getLogger(__name__)

FEEDS = [
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml"
             "?partnerId=wrss01&id=10000664"),
    ("Yahoo Finance", "https://feeds.finance.yahoo.com/rss/2.0/headline"
                      "?s=SPY&region=US&lang=en-US"),
    ("Investing.com", "https://de.investing.com/rss/news.rss"),
]

_TAG = re.compile(r"<[^>]+>")


def _text(node: Optional[ET.Element]) -> str:
    if node is None or not node.text:
        return ""
    return _TAG.sub("", node.text).strip()


def _parse_rss(xml_text: str, source: str, limit: int) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.debug("Feed %s nicht lesbar: %s", source, e)
        return []
    out = []
    # RSS 2.0 (item) und Atom (entry) abdecken
    items = root.iter("item")
    for item in items:
        title = _text(item.find("title"))
        if not title:
            continue
        out.append({
            "source": source,
            "headline": title[:250],
            "summary": _text(item.find("description"))[:400],
            "url": _text(item.find("link")),
            "published": _text(item.find("pubDate")),
        })
        if len(out) >= limit:
            break
    return out


def market_headlines(limit_per_feed: int = 8) -> list[dict]:
    """Schlagzeilen aus allen Feeds, Duplikate entfernt."""
    alle: list[dict] = []
    for source, url in FEEDS:
        xml_text = net.get_text(url)
        if not xml_text:
            log.info("Feed %s lieferte nichts", source)
            continue
        alle.extend(_parse_rss(xml_text, source, limit_per_feed))

    gesehen: set[str] = set()
    einzig = []
    for item in alle:
        schluessel = re.sub(r"\W+", "", item["headline"].lower())[:60]
        if schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        einzig.append(item)
    log.info("Marktnachrichten: %d Schlagzeilen aus %d Feeds",
             len(einzig), len(FEEDS))
    return einzig


def symbol_news(symbols: Iterable[str], per_symbol: int = 6,
                hours_back: int = 36) -> dict[str, list[dict]]:
    """Nachrichten je Titel ueber Alpaca."""
    try:
        return alpaca.news_by_symbol(symbols, per_symbol, hours_back)
    except Exception as e:                             # noqa: BLE001
        log.warning("Titel-Nachrichten nicht abrufbar: %s", e)
        return {}


def digest_payload(headlines: list[dict], limit: int = 20) -> list[str]:
    """Schlagzeilen als reine Textliste fuer das Sprachmodell."""
    return [h["headline"] for h in headlines[:limit]]


def build(symbols: list[str]) -> dict:
    """Alles auf einmal: Markt-Schlagzeilen und Nachrichten je Titel."""
    headlines = market_headlines()
    per_symbol = symbol_news(symbols)
    return {
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "market": headlines,
        "by_symbol": per_symbol,
        "symbols_with_news": len(per_symbol),
    }
