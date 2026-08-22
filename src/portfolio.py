"""Das Spiel: zwei virtuelle Depots mit identischen Regeln.

Nur die Titelauswahl unterscheidet sie — Depot "ki" folgt der Analyse, Depot
"zufall" wuerfelt aus demselben Universum. Stop, Ziel, Positionsgroesse,
Einstiegszeitpunkt und Ausstiegsregeln sind gleich. Damit misst der Vergleich
die Analyse und nicht die Handelsmechanik.

Regeln, damit die Simulation ehrlich bleibt:

  Einstieg   Ein Auftrag von heute wird zur Eroeffnung des NAECHSTEN
             Handelstags ausgefuehrt. Kein Blick in die Zukunft.

  Luecken    Eroeffnet der Kurs jenseits von Stop oder Ziel, gilt der
             Eroeffnungskurs, nicht die Marke. Wer die Marke ansetzt,
             schoent Ausreisser nach unten weg.

  Beruehrung Werden Tageshoch und Tagestief so weit, dass BEIDE Marken
             getroffen sein koennten, zaehlt der STOP. Tagesbars koennen
             die Reihenfolge nicht aufloesen; die konservative Annahme ist
             die einzige, die nicht heimlich schoenrechnet.

  Zeit       Nach 20 Handelstagen wird zum Schluss verkauft.

  Kosten     5 Basispunkte Schlupf je Seite.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import random
from typing import Iterable, Optional

from . import config

log = logging.getLogger(__name__)

DEPOTS = {
    "ki": "Depot KI",
    "zufall": "Depot Zufall",
}


def new_portfolio(name: str, label: Optional[str] = None,
                  capital: Optional[float] = None) -> dict:
    return {
        "name": name,
        "label": label or DEPOTS.get(name, name),
        "start_capital": capital or config.START_CAPITAL,
        "cash": capital or config.START_CAPITAL,
        "positions": [],
        "pending": [],
        "closed": [],
        "equity_curve": [],
        "skipped": [],
    }


# -- Speichern und Laden ---------------------------------------------------

def path(name: str):
    return config.DATA_DIR / f"portfolio_{name}.json"


def save(pf: dict) -> None:
    """Depot ins Repo schreiben.

    Der Zustand liegt bewusst im Git-Verlauf und nicht in einer Datenbank:
    jeder Auftrag, jeder Ausstieg und jede Bewertung ist damit Monate spaeter
    noch nachvollziehbar — und wiederherstellbar, falls ein Lauf schiefgeht.
    """
    p = path(pf["name"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(pf, indent=1, default=str), encoding="utf-8")


def load(name: str) -> dict:
    """Depot laden — oder ein frisches anlegen, wenn es noch keines gibt."""
    p = path(name)
    if not p.exists():
        return new_portfolio(name)
    try:
        pf = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.error("portfolio_%s.json unlesbar — es wird NICHT ueberschrieben, "
                  "der Lauf bricht ab", name)
        raise
    basis = new_portfolio(name)
    basis.update(pf)
    return basis


# -- Auftraege -------------------------------------------------------------

def place_orders(pf: dict, picks: list[dict], order_date: dt.date) -> list[dict]:
    """Kaufauftraege fuer den naechsten Handelstag vormerken.

    `picks` sind Dicts mit mindestens symbol, target, stop. Alles Weitere
    (Score, Sektor, Kurszielmethoden, Regime) wird mitgeschleppt, weil die
    Lernschleife es spaeter braucht.
    """
    placed = []
    for p in picks:
        if p.get("target") is None or p.get("stop") is None:
            pf["skipped"].append({
                "date": order_date.isoformat(), "symbol": p.get("symbol"),
                "reason": "Kein Ziel oder Stop",
            })
            continue
        if any(x["symbol"] == p["symbol"] for x in pf["positions"]):
            pf["skipped"].append({
                "date": order_date.isoformat(), "symbol": p["symbol"],
                "reason": "Position bereits offen",
            })
            continue
        if any(x["symbol"] == p["symbol"] for x in pf["pending"]):
            continue
        order = dict(p)
        order["order_date"] = order_date.isoformat()
        pf["pending"].append(order)
        placed.append(order)
    return placed


def _slip(price: float, side: str) -> float:
    """Schlupf: beim Kauf teurer, beim Verkauf billiger."""
    factor = config.SLIPPAGE_BPS / 10_000
    return price * (1 + factor) if side == "buy" else price * (1 - factor)


def fill_pending(pf: dict, bars_today: dict[str, dict], today: dt.date) -> list[dict]:
    """Offene Auftraege zur heutigen Eroeffnung ausfuehren."""
    filled = []
    still_pending = []

    for order in pf["pending"]:
        sym = order["symbol"]
        bar = bars_today.get(sym)
        if not bar:
            # Kein Handel heute (Aussetzung, Delisting): einmal verschieben,
            # danach verfallen lassen.
            tries = order.get("tries", 0) + 1
            order["tries"] = tries
            if tries <= 2:
                still_pending.append(order)
            else:
                pf["skipped"].append({
                    "date": today.isoformat(), "symbol": sym,
                    "reason": "Kein Kurs an drei Handelstagen",
                })
            continue

        if len(pf["positions"]) >= config.MAX_CONCURRENT_POSITIONS:
            pf["skipped"].append({
                "date": today.isoformat(), "symbol": sym,
                "reason": f"Depot voll ({config.MAX_CONCURRENT_POSITIONS} Positionen)",
            })
            continue

        entry = _slip(float(bar["o"]), "buy")
        equity = equity_value(pf, bars_today)
        notional = equity * config.POSITION_PCT
        shares = notional / entry if entry > 0 else 0
        if shares <= 0 or notional > pf["cash"]:
            pf["skipped"].append({
                "date": today.isoformat(), "symbol": sym,
                "reason": f"Zu wenig Barmittel ({pf['cash']:.0f} USD)",
            })
            continue

        pf["cash"] -= shares * entry
        position = {
            **{k: v for k, v in order.items() if k != "tries"},
            "shares": round(shares, 6),
            "entry_price": round(entry, 4),
            "entry_date": today.isoformat(),
            "entry_open": round(float(bar["o"]), 4),
            "days_held": 0,
            # Groesste guenstige und unguenstige Bewegung waehrend der
            # Haltedauer. Die Lernschleife liest daran ab, WELCHES Kursziel
            # erreichbar gewesen waere — nicht nur, ob der Trade gewann.
            "mfe_price": round(float(bar["o"]), 4),
            "mae_price": round(float(bar["o"]), 4),
        }
        pf["positions"].append(position)
        filled.append(position)

    pf["pending"] = still_pending
    return filled


# -- Ausstiege -------------------------------------------------------------

def _exit_decision(pos: dict, bar: dict) -> Optional[tuple[str, float]]:
    """Wird heute verkauft? Gibt (Grund, Kurs) oder None zurueck.

    Reihenfolge ist bewusst: erst Luecken pruefen, dann Beruehrungen, und
    bei doppelter Beruehrung gewinnt der Stop.
    """
    o, h, l = float(bar["o"]), float(bar["h"]), float(bar["l"])
    stop, target = pos["stop"], pos["target"]

    # Eroeffnung jenseits des Stops: der Stop war nicht zu halten.
    if o <= stop:
        return "stop_luecke", o
    # Eroeffnung jenseits des Ziels: guenstige Luecke, Gewinn zum Eroeffnungskurs.
    if o >= target:
        return "ziel_luecke", o
    # Beide Marken im Tagesverlauf beruehrt: konservativ zaehlt der Stop.
    if l <= stop and h >= target:
        return "stop_und_ziel", stop
    if l <= stop:
        return "stop", stop
    if h >= target:
        return "ziel", target
    return None


def _close(pf: dict, pos: dict, price: float, reason: str,
           day: dt.date, regime: Optional[dict] = None) -> dict:
    exit_price = _slip(price, "sell")
    proceeds = pos["shares"] * exit_price
    pf["cash"] += proceeds

    entry, stop = pos["entry_price"], pos["stop"]
    risk_per_share = entry - stop
    pnl = proceeds - pos["shares"] * entry
    r_multiple = ((exit_price - entry) / risk_per_share
                  if risk_per_share > 0 else 0.0)

    trade = {
        "symbol": pos["symbol"],
        "name": pos.get("name"),
        "sector": pos.get("sector"),
        "depot": pf["name"],
        "entry_date": pos["entry_date"],
        "entry_price": round(entry, 4),
        "exit_date": day.isoformat(),
        "exit_price": round(exit_price, 4),
        "exit_reason": reason,
        "shares": pos["shares"],
        "stop": stop,
        "target": pos["target"],
        "pnl": round(pnl, 2),
        "pnl_pct": round((exit_price / entry - 1) * 100, 3) if entry else 0.0,
        "r_multiple": round(r_multiple, 3),
        "days_held": pos.get("days_held", 0),
        "mfe_price": round(pos.get("mfe_price", entry), 4),
        "mae_price": round(pos.get("mae_price", entry), 4),
        "mfe_r": round((pos.get("mfe_price", entry) - entry) / risk_per_share, 3)
                 if risk_per_share > 0 else 0.0,
        "atr_at_entry": pos.get("atr_at_entry"),
        # Was beim Einstieg fuer genau diese Marken gemessen war. Ohne diese
        # Messlatte laesst sich hinterher nicht sagen, ob eine Trefferquote
        # von 45 % gut oder schlecht war.
        "ziel_atr": pos.get("ziel_atr"),
        "stop_atr": pos.get("stop_atr"),
        "basis_p_ziel": pos.get("basis_p_ziel"),
        "basis_erwartung_r": pos.get("basis_erwartung_r"),
        "score": pos.get("score"),
        "score_components": pos.get("score_components"),
        "target_methods": pos.get("target_methods"),
        "regime_at_entry": pos.get("regime"),
        "regime_at_exit": regime,
    }
    pf["closed"].append(trade)
    return trade


def process_exits(pf: dict, bars_today: dict[str, dict], today: dt.date,
                  regime: Optional[dict] = None) -> list[dict]:
    """Alle offenen Positionen gegen den heutigen Bar pruefen."""
    closed, still_open = [], []
    for pos in pf["positions"]:
        bar = bars_today.get(pos["symbol"])
        if not bar:
            still_open.append(pos)
            continue

        pos["days_held"] = pos.get("days_held", 0) + 1
        pos["mfe_price"] = max(pos.get("mfe_price", 0.0), float(bar["h"]))
        pos["mae_price"] = min(pos.get("mae_price", float("inf")), float(bar["l"]))
        decision = _exit_decision(pos, bar)
        if decision:
            reason, price = decision
            closed.append(_close(pf, pos, price, reason, today, regime))
            continue
        if pos["days_held"] >= config.MAX_HOLD_DAYS:
            closed.append(_close(pf, pos, float(bar["c"]), "zeit", today, regime))
            continue
        still_open.append(pos)

    pf["positions"] = still_open
    return closed


# -- Bewertung -------------------------------------------------------------

def equity_value(pf: dict, bars_today: dict[str, dict]) -> float:
    """Barmittel plus offene Positionen zum letzten bekannten Kurs."""
    total = pf["cash"]
    for pos in pf["positions"]:
        bar = bars_today.get(pos["symbol"])
        price = float(bar["c"]) if bar else pos["entry_price"]
        total += pos["shares"] * price
    return total


def mark_to_market(pf: dict, bars_today: dict[str, dict], today: dt.date) -> dict:
    """Tagesbewertung in die Equity-Kurve schreiben."""
    equity = equity_value(pf, bars_today)
    invested = equity - pf["cash"]
    point = {
        "date": today.isoformat(),
        "equity": round(equity, 2),
        "cash": round(pf["cash"], 2),
        "positions": len(pf["positions"]),
        "invested_pct": round(invested / equity * 100, 2) if equity > 0 else 0.0,
        "return_pct": round((equity / pf["start_capital"] - 1) * 100, 3),
    }
    pf["equity_curve"].append(point)
    return point


def settle_day(pf: dict, bars_today: dict[str, dict], today: dt.date,
               regime: Optional[dict] = None) -> dict:
    """Ein vollstaendiger Handelstag: erst Auftraege fuellen, dann Ausstiege,
    dann bewerten.

    Die Reihenfolge ist wichtig: ein heute gefuellter Auftrag kann noch am
    selben Tag ausgestoppt werden, wenn der Kurs nach der Eroeffnung faellt.
    """
    filled = fill_pending(pf, bars_today, today)
    closed = process_exits(pf, bars_today, today, regime)
    point = mark_to_market(pf, bars_today, today)
    return {"filled": filled, "closed": closed, "equity": point}


# -- Zufallsauswahl --------------------------------------------------------

def random_picks(candidates: list[dict], n: int, seed_date: dt.date,
                 depot_seed: int = 0, belegt: Optional[set] = None) -> list[dict]:
    """Zufaellige Titel aus demselben Universum.

    Der Zufall wird aus dem Datum abgeleitet, damit ein erneuter Lauf
    dasselbe Ergebnis liefert — sonst waere kein Backtest reproduzierbar
    und kein Fehler nachstellbar.

    `belegt` schliesst offene Titel aus, damit beide Depots gleich viele
    Kaeufe zustande bringen. Sonst vergliche man Kapitaleinsatz statt
    Auswahl.
    """
    rng = random.Random(f"{seed_date.isoformat()}-{depot_seed}")
    belegt = belegt or set()
    pool = [c for c in candidates if c.get("target") is not None
            and c.get("stop") is not None and c["symbol"] not in belegt]
    return rng.sample(pool, min(n, len(pool)))


def offene_titel(pf: dict) -> set:
    """Symbole, die im Depot liegen oder als Auftrag warten."""
    return ({p["symbol"] for p in pf["positions"]}
            | {p["symbol"] for p in pf["pending"]})


# -- Kennzahlen ------------------------------------------------------------

def statistics(pf: dict) -> dict:
    """Kennzahlen ueber alle abgeschlossenen Trades."""
    closed = pf["closed"]
    n = len(closed)
    curve = pf["equity_curve"]
    equity = curve[-1]["equity"] if curve else pf["start_capital"]

    base = {
        "depot": pf["name"],
        "label": pf["label"],
        "equity": round(equity, 2),
        "cash": round(pf["cash"], 2),
        "open_positions": len(pf["positions"]),
        "return_pct": round((equity / pf["start_capital"] - 1) * 100, 2),
        "trades": n,
        "max_drawdown_pct": _max_drawdown(curve),
    }
    if not n:
        return {**base, "win_rate": None, "expectancy_r": None,
                "profit_factor": None, "avg_hold_days": None}

    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))

    return {
        **base,
        "win_rate": round(len(wins) / n * 100, 1),
        "wins": len(wins),
        "losses": len(losses),
        "expectancy_r": round(sum(t["r_multiple"] for t in closed) / n, 3),
        "avg_win_r": round(sum(t["r_multiple"] for t in wins) / len(wins), 3) if wins else None,
        "avg_loss_r": round(sum(t["r_multiple"] for t in losses) / len(losses), 3) if losses else None,
        "profit_factor": (round(gross_win / gross_loss, 2) if gross_loss > 0
                          else (99.0 if gross_win > 0 else None)),
        "avg_hold_days": round(sum(t["days_held"] for t in closed) / n, 1),
        "exit_reasons": _count(t["exit_reason"] for t in closed),
        "total_pnl": round(sum(t["pnl"] for t in closed), 2),
    }


def _count(values: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _max_drawdown(curve: list[dict]) -> Optional[float]:
    if not curve:
        return None
    peak = curve[0]["equity"]
    worst = 0.0
    for p in curve:
        peak = max(peak, p["equity"])
        if peak > 0:
            worst = min(worst, p["equity"] / peak - 1)
    return round(worst * 100, 2)
