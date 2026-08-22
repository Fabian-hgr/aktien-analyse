"""Gemeinsame HTTP-Hilfe: Session, Wiederholversuche, Backoff.

Zentral, damit jeder Datenzugriff dasselbe Verhalten bei Netzfehlern und
Ratenbegrenzung zeigt — und damit nirgends Zugangsdaten ins Log geraten.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests

from . import config

log = logging.getLogger(__name__)

_session: Optional[requests.Session] = None


def session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": config.USER_AGENT})
    return _session


def get_json(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    retries: int | None = None,
    timeout: float | None = None,
) -> Any:
    """GET mit Wiederholversuchen. Gibt geparstes JSON zurück oder wirft.

    429 und 5xx werden mit wachsender Wartezeit wiederholt, 4xx sofort
    aufgegeben — ein falscher Parameter wird durch Warten nicht richtig.
    """
    retries = config.HTTP_RETRIES if retries is None else retries
    timeout = config.HTTP_TIMEOUT if timeout is None else timeout
    last_error: Exception | None = None

    for attempt in range(retries):
        try:
            r = session().get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            last_error = e
            _sleep(attempt)
            continue

        if r.status_code == 200:
            return r.json()

        if r.status_code == 429 or r.status_code >= 500:
            last_error = RuntimeError(f"{r.status_code} bei {_safe(url)}")
            retry_after = r.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                time.sleep(min(int(retry_after), 30))
            else:
                _sleep(attempt)
            continue

        # 4xx: nicht wiederholen
        raise RuntimeError(f"{r.status_code} bei {_safe(url)}: {r.text[:200]}")

    raise RuntimeError(f"Aufgegeben nach {retries} Versuchen: {last_error}")


def get_text(url: str, *, params: dict | None = None,
             timeout: float | None = None) -> str:
    """GET für nicht-JSON (RSS). Gibt bei Fehler einen leeren String zurück —
    ein ausgefallener Nachrichten-Feed darf den ganzen Lauf nicht stoppen."""
    try:
        r = session().get(url, params=params,
                          timeout=timeout or config.HTTP_TIMEOUT)
        return r.text if r.status_code == 200 else ""
    except requests.RequestException as e:
        log.warning("Feed nicht erreichbar %s: %s", _safe(url), e)
        return ""


def _sleep(attempt: int) -> None:
    time.sleep(min(2 ** attempt, 8))


def _safe(url: str) -> str:
    """URL ohne Query-String — dort könnten Schlüssel stehen."""
    return url.split("?", 1)[0]
