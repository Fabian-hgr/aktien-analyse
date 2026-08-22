"""Push aufs Handy ueber ntfy.sh — und der Aus-Schalter.

Zwei getrennte Themen mit zufaelligen Namen:

  Benachrichtigungs-Thema   steht nur in den GitHub Secrets. Hierhin geht
                            die Morgenmeldung. Niemand ausser Fabian kennt
                            den Namen.

  Steuer-Thema              steht im Quelltext der oeffentlichen Seite, weil
                            die Seite darauf senden koennen muss. Wer den
                            Namen kennt, kann die Simulation pausieren —
                            mehr nicht. Benachrichtigungen und Alpaca-
                            Schluessel sind davon getrennt.

Warum der Zustand im Repo landet und nicht in ntfy: ntfy haelt Nachrichten
nur rund zwoelf Stunden vor. Eine Pause ueber ein Wochenende wuerde dort
verfallen und das System liefe von selbst wieder an. Der Lauf schreibt den
Zustand deshalb nach control.json, und nur DAS zaehlt.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Optional

import requests

from . import config, net

log = logging.getLogger(__name__)

NTFY_BASE = "https://ntfy.sh"
BEFEHLE = {"PAUSE", "RESUME", "PAUSIEREN", "WEITER"}


def _control_path():
    return config.DATA_DIR / "control.json"


def load_control() -> dict:
    p = _control_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("control.json unlesbar — gehe von 'laeuft' aus")
    return {"paused": False, "changed_at": None, "changed_by": "standard"}


def save_control(state: dict) -> None:
    p = _control_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=1, ensure_ascii=False),
                 encoding="utf-8")


def poll_control_topic(topic: Optional[str] = None,
                       since: str = "48h") -> Optional[str]:
    """Neuester Befehl im Steuer-Thema, oder None.

    ntfy liefert eine Zeile JSON je Nachricht. Gelesen wird der letzte
    gueltige Befehl im Zeitfenster.
    """
    topic = topic or config.NTFY_CONTROL_TOPIC
    if not topic:
        return None
    try:
        r = net.session().get(f"{NTFY_BASE}/{topic}/json",
                              params={"poll": "1", "since": since},
                              timeout=15)
        if r.status_code != 200:
            log.warning("Steuer-Thema nicht lesbar: HTTP %s", r.status_code)
            return None
    except requests.RequestException as e:
        log.warning("Steuer-Thema nicht erreichbar: %s", e)
        return None

    letzter = None
    for zeile in r.text.splitlines():
        if not zeile.strip():
            continue
        try:
            msg = json.loads(zeile)
        except json.JSONDecodeError:
            continue
        if msg.get("event") != "message":
            continue
        text = (msg.get("message") or "").strip().upper()
        if text in BEFEHLE:
            letzter = text
    return letzter


def apply_control(topic: Optional[str] = None) -> dict:
    """Steuer-Thema abfragen, Zustand fortschreiben und zurueckgeben."""
    state = load_control()
    befehl = poll_control_topic(topic)
    if not befehl:
        return state

    pausiert = befehl in ("PAUSE", "PAUSIEREN")
    if pausiert != state.get("paused"):
        state = {
            "paused": pausiert,
            "changed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "changed_by": "Seite",
            "command": befehl,
        }
        save_control(state)
        log.info("Steuerbefehl '%s' angewandt — System %s",
                 befehl, "pausiert" if pausiert else "laeuft wieder")
    return state


def send(title: str, message: str, *, tags: Optional[list[str]] = None,
         priority: int = 3, click: str = "",
         topic: Optional[str] = None) -> bool:
    """Push senden. Gibt True bei Erfolg zurueck.

    Ein Fehlschlag darf den Lauf nicht abbrechen: die Analyse ist zu diesem
    Zeitpunkt schon geschrieben und committet.
    """
    topic = topic or config.NTFY_TOPIC
    if not topic:
        log.warning("Kein ntfy-Thema gesetzt — keine Push gesendet")
        return False

    headers = {
        "Title": title.encode("utf-8").decode("latin-1", "ignore"),
        "Priority": str(priority),
        "Markdown": "yes",
    }
    if tags:
        headers["Tags"] = ",".join(tags)
    if click:
        headers["Click"] = click

    try:
        r = net.session().post(f"{NTFY_BASE}/{topic}",
                               data=message.encode("utf-8"),
                               headers=headers, timeout=20)
        if r.status_code == 200:
            log.info("Push gesendet (%d Zeichen)", len(message))
            return True
        log.warning("Push fehlgeschlagen: HTTP %s %s", r.status_code, r.text[:120])
    except requests.RequestException as e:
        log.warning("Push fehlgeschlagen: %s", e)
    return False


def format_morning(date: dt.date, ideas: list[dict], digest: Optional[dict],
                   stats: dict, dashboard_url: str = "",
                   headlines: Optional[list[dict]] = None) -> tuple[str, str]:
    """Titel und Text der Morgenmeldung. Gibt (Titel, Text) zurueck.

    `headlines` sind die rohen Schlagzeilen. Sie treten an die Stelle der
    Zusammenfassung, wenn das Sprachmodell ausfaellt — die Nachrichten sind
    der Kern dieser Meldung und duerfen nicht mit Ollama zusammen ausfallen.
    """
    titel = (f"Boersenoeffnung {date.strftime('%d.%m.')} — "
             f"{len(ideas)} neue Idee{'n' if len(ideas) != 1 else ''}")

    zeilen: list[str] = []
    for e in ideas:
        tg = e.get("targets") or {}
        these = ((e.get("llm") or {}).get("these") or "").strip()
        zeilen.append(
            f"**{e['symbol']}** ({e.get('sector', '')})  "
            f"{tg.get('price', 0):.2f} → **{tg.get('target', 0):.2f}** "
            f"({tg.get('upside_pct', 0):+.1f} %)  Stop {tg.get('stop', 0):.2f}  "
            f"CRV {tg.get('reward_risk', 0):.1f}")
        if these:
            zeilen.append(f"   _{these}_")

    if not ideas:
        zeilen.append("_Heute keine Idee, die alle Filter besteht._")

    if digest and digest.get("zusammenfassung"):
        zeilen.append("")
        zeilen.append("**Marktlage**")
        zeilen.append(digest["zusammenfassung"])
        for punkt in digest.get("punkte", [])[:4]:
            zeilen.append(f"• {punkt}")
    elif headlines:
        # Ohne Sprachmodell gibt es keine Zusammenfassung — aber die
        # Schlagzeilen selbst gibt es immer noch.
        zeilen.append("")
        zeilen.append("**Schlagzeilen**")
        for h in headlines[:5]:
            zeilen.append(f"• {h.get('headline', '')} _({h.get('source', '')})_")

    ki, zu = stats.get("ki", {}), stats.get("zufall", {})
    if ki:
        zeilen.append("")
        zeilen.append(
            f"**Depots** KI {ki.get('return_pct', 0):+.2f} % · "
            f"Zufall {zu.get('return_pct', 0):+.2f} % · "
            f"{ki.get('trades', 0)} Trades, Trefferquote "
            f"{ki.get('win_rate') if ki.get('win_rate') is not None else '—'} %")

    if dashboard_url:
        zeilen.append("")
        zeilen.append(dashboard_url)

    return titel, "\n".join(zeilen)
