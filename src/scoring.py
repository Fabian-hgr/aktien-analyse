"""Bewertung eines Titels — sieben Komponenten, jede zwischen 0 und 1.

Grundsaetze:

  - Jede Komponente ist auf 0..1 begrenzt und begruendet ihren Wert im
    Klartext. Kein Wert entsteht aus einer undurchsichtigen Formel.

  - Fehlende Daten fuehren NICHT zu einer schlechten Note, sondern dazu,
    dass die Komponente entfaellt und die uebrigen Gewichte neu normiert
    werden. Ein Titel ohne Yahoo-Daten wird nicht bestraft, sondern als
    "duenne Datenlage" markiert — und ist erst ab einer Mindestabdeckung
    ueberhaupt vorschlagsfaehig.

  - Abzuege (Earnings, hohes Beta) kommen NACH der Normierung, damit sie
    unabhaengig von der Datenlage gleich stark wirken.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

from . import config

MIN_DATA_COVERAGE = 0.60     # so viel Gewicht muss belegt sein


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _ramp(x: Optional[float], low: float, high: float) -> Optional[float]:
    """Lineare Rampe: bei `low` und darunter 0, bei `high` und darueber 1."""
    if x is None or high == low:
        return None
    return _clip01((x - low) / (high - low))


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x * 100:.1f} %"


@dataclass
class Component:
    key: str
    label: str
    score: Optional[float]           # 0..1, None = keine Daten
    weight: float
    reasons: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.score is not None

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "score": round(self.score, 3) if self.score is not None else None,
            "weight": round(self.weight, 3),
            "reasons": self.reasons,
        }


# -- Einzelne Komponenten --------------------------------------------------

def trend(snap: dict) -> Component:
    """Trendrichtung und relative Staerke. Vier gleich gewichtete Teile."""
    c = Component("trend", config.SCORE_LABELS["trend"], None,
                  config.SCORE_WEIGHTS["trend"])
    close = snap.get("close")
    ema9, ema21, ema50 = snap.get("ema9"), snap.get("ema21"), snap.get("ema50")
    sma200, adx = snap.get("sma200"), snap.get("adx")
    rel = snap.get("rel_strength_63d")

    parts: list[float] = []
    if ema9 is not None and ema21 is not None:
        up = ema9 > ema21
        parts.append(1.0 if up else 0.0)
        c.reasons.append(f"EMA9 {'über' if up else 'unter'} EMA21")
    if ema21 is not None and ema50 is not None:
        up = ema21 > ema50
        parts.append(1.0 if up else 0.0)
        c.reasons.append(f"EMA21 {'über' if up else 'unter'} EMA50")
    if close is not None and sma200 is not None:
        up = close > sma200
        parts.append(1.0 if up else 0.0)
        c.reasons.append(
            f"Kurs {'über' if up else 'unter'} 200-Tage-Linie "
            f"({(close / sma200 - 1) * 100:+.1f} %)")
    if adx is not None:
        v = _clip01(adx / 40.0)
        parts.append(v)
        c.reasons.append(f"ADX {adx:.0f} (Trendstärke {(v) * 100:.0f} %)")
    if rel is not None:
        v = _ramp(rel, -0.10, 0.10)
        parts.append(v)
        c.reasons.append(f"63 Tage gegen SPY: {(rel) * 100:+.1f} %")

    if parts:
        c.score = sum(parts) / len(parts)
    return c


def setup(snap: dict) -> Component:
    """Einstiegsqualitaet: nicht ueberdehnt, RSI im brauchbaren Bereich,
    genug Luft bis zum naechsten Widerstand."""
    c = Component("setup", config.SCORE_LABELS["setup"], None,
                  config.SCORE_WEIGHTS["setup"])
    close, atr = snap.get("close"), snap.get("atr")
    ema21, rsi = snap.get("ema21"), snap.get("rsi")
    high55 = snap.get("donchian_high55")

    parts: list[float] = []
    if close and atr and ema21:
        # Ideal: 0 bis 1.5 ATR ueber der EMA21. Darunter noch kein Trend,
        # darueber ueberdehnt und anfaellig fuer Rueckschlaege.
        d = (close - ema21) / atr
        if d < 0:
            v = _clip01(1 + d)                 # bis -1 ATR linear auf 0
        elif d <= 1.5:
            v = 1.0
        else:
            v = _clip01(1 - (d - 1.5) / 2.0)   # ab 3.5 ATR auf 0
        parts.append(v)
        c.reasons.append(f"Abstand zur EMA21: {d:+.1f} ATR ({(v) * 100:.0f} % Eignung)")
    if rsi is not None:
        # 45-70 ist der brauchbare Bereich: Trend da, aber nicht ueberkauft.
        if 45 <= rsi <= 70:
            v = 1.0
        elif rsi < 45:
            v = _clip01((rsi - 25) / 20)
        else:
            v = _clip01((85 - rsi) / 15)
        parts.append(v)
        c.reasons.append(f"RSI {rsi:.0f} ({(v) * 100:.0f} % Eignung)")
    if close and atr and high55:
        room = (high55 - close) / atr
        v = _clip01(room / 2.0) if room > 0 else 1.0   # Ausbruch = volle Luft
        parts.append(v)
        c.reasons.append(
            f"Luft bis 55-Tage-Hoch: {room:+.1f} ATR"
            + (" (Ausbruch)" if room <= 0 else ""))

    if parts:
        c.score = sum(parts) / len(parts)
    return c


def volume(snap: dict) -> Component:
    """Bestaetigt das Volumen die Bewegung?"""
    c = Component("volumen", config.SCORE_LABELS["volumen"], None,
                  config.SCORE_WEIGHTS["volumen"])
    v, avg = snap.get("volume"), snap.get("avg_volume_20d")
    if not v or not avg or avg <= 0:
        return c
    ratio = v / avg
    c.score = _ramp(ratio, 0.7, 1.8)
    c.reasons.append(
        f"Tagesvolumen {ratio:.2f}x des 20-Tage-Schnitts")
    return c


def quality(f: Optional[dict]) -> Component:
    """Fundamentale Qualitaet: verdient das Unternehmen Geld, waechst es,
    und wie hoch ist es verschuldet?"""
    c = Component("qualitaet", config.SCORE_LABELS["qualitaet"], None,
                  config.SCORE_WEIGHTS["qualitaet"])
    if not f:
        return c
    parts, weights = [], []
    pm, roe = f.get("profit_margin"), f.get("return_on_equity")
    rg, de = f.get("revenue_growth"), f.get("debt_to_equity")

    if pm is not None:
        v = _ramp(pm, 0.0, 0.25); parts.append(v); weights.append(0.30)
        c.reasons.append(f"Nettomarge {_pct(pm)}")
    if roe is not None:
        v = _ramp(roe, 0.05, 0.30); parts.append(v); weights.append(0.25)
        c.reasons.append(f"Eigenkapitalrendite {_pct(roe)}")
    if rg is not None:
        v = _ramp(rg, -0.05, 0.25); parts.append(v); weights.append(0.25)
        c.reasons.append(f"Umsatzwachstum {_pct(rg)}")
    if de is not None:
        # debt_to_equity kommt bei Yahoo in Prozent (150 = 1.5x)
        v = _clip01(1 - de / 250.0); parts.append(v); weights.append(0.20)
        c.reasons.append(f"Verschuldungsgrad {de:.0f} % des Eigenkapitals")

    if parts:
        c.score = sum(p * w for p, w in zip(parts, weights)) / sum(weights)
    return c


def valuation(f: Optional[dict], sector_median_pe: Optional[float]) -> Component:
    """Bewertung gegen die eigene Branche. Guenstiger ist besser —
    aber nur bis zu einem Punkt, unter dem es ein Warnsignal waere."""
    c = Component("bewertung", config.SCORE_LABELS["bewertung"], None,
                  config.SCORE_WEIGHTS["bewertung"])
    if not f:
        return c
    parts, weights = [], []
    fpe, peg = f.get("forward_pe"), f.get("peg_ratio")

    if fpe and fpe > 0 and sector_median_pe and sector_median_pe > 0:
        ratio = fpe / sector_median_pe
        # 0.5x Branchenmedian -> 1.0 ; 1.6x -> 0.0
        v = _clip01((1.6 - ratio) / 1.1); parts.append(v); weights.append(0.6)
        c.reasons.append(
            f"Forward-KGV {fpe:.1f} = {ratio:.2f}x Branchenmedian "
            f"{sector_median_pe:.1f}")
    if peg and peg > 0:
        v = _clip01((2.5 - peg) / 2.0); parts.append(v); weights.append(0.4)
        c.reasons.append(f"PEG {peg:.2f}")

    if parts:
        c.score = sum(p * w for p, w in zip(parts, weights)) / sum(weights)
    return c


RECOMMENDATION_SCORE = {
    "strong_buy": 1.00, "buy": 0.75, "hold": 0.40,
    "underperform": 0.15, "sell": 0.0,
}


def analysts(f: Optional[dict], price: Optional[float]) -> Component:
    """Rueckenwind durch Analysten: Abstand zum Konsensziel, Empfehlung,
    Breite der Abdeckung."""
    c = Component("analysten", config.SCORE_LABELS["analysten"], None,
                  config.SCORE_WEIGHTS["analysten"])
    if not f or not price or price <= 0:
        return c
    parts, weights = [], []
    tm, rec, n = f.get("target_mean"), f.get("recommendation"), f.get("analyst_count")

    if tm and tm > 0:
        upside = tm / price - 1
        v = _ramp(upside, -0.05, 0.30); parts.append(v); weights.append(0.55)
        c.reasons.append(f"Konsensziel {(upside) * 100:+.1f} % über Kurs")
    if rec:
        v = RECOMMENDATION_SCORE.get(rec.lower())
        if v is not None:
            parts.append(v); weights.append(0.30)
            c.reasons.append(f"Empfehlung: {rec}")
    if n:
        v = _ramp(n, 3, 25); parts.append(v); weights.append(0.15)
        c.reasons.append(f"{n:.0f} Analysten decken den Titel ab")

    if parts:
        c.score = sum(p * w for p, w in zip(parts, weights)) / sum(weights)
    return c


def sentiment(llm: Optional[dict]) -> Component:
    """News-Stimmung aus dem Sprachmodell, -1..+1 auf 0..1 abgebildet."""
    c = Component("sentiment", config.SCORE_LABELS["sentiment"], None,
                  config.SCORE_WEIGHTS["sentiment"])
    if not llm or llm.get("sentiment") is None:
        return c
    s = max(-1.0, min(1.0, float(llm["sentiment"])))
    c.score = (s + 1) / 2
    c.reasons.append(f"Stimmung {s:+.2f} aus {llm.get('news_count', 0)} Meldungen")
    if llm.get("these"):
        c.reasons.append(llm["these"])
    return c


# -- Abzuege und Ausschluesse ----------------------------------------------

def hard_exclusions(snap: dict, universe_entry: dict) -> list[str]:
    """Gruende, den Titel gar nicht erst zu bewerten."""
    out = []
    close = snap.get("close")
    if close is None:
        out.append("Kein Kurs verfügbar")
        return out
    if close < config.MIN_PRICE:
        out.append(f"Kurs {close:.2f} USD unter {config.MIN_PRICE:.0f} USD")
    dv = snap.get("dollar_volume_20d") or universe_entry.get("dollar_volume")
    if dv is not None and dv < config.MIN_DOLLAR_VOLUME:
        out.append(f"Dollarvolumen {dv / 1e6:.1f} Mio unter "
                   f"{config.MIN_DOLLAR_VOLUME / 1e6:.0f} Mio (IEX)")
    if snap.get("atr") is None:
        out.append("Zu kurze Historie für ATR")
    if (snap.get("bars") or 0) < 60:
        out.append(f"Nur {snap.get('bars', 0)} Bars Historie")
    return out


def penalties(f: Optional[dict], today: Optional[dt.date] = None,
              trading_days_ahead: Optional[int] = None) -> tuple[float, list[str]]:
    """Abzuege vom Gesamtscore. Gibt (Summe, Begruendungen) zurueck."""
    today = today or dt.date.today()
    total, reasons = 0.0, []
    if not f:
        return total, reasons

    nxt = f.get("next_earnings")
    if nxt:
        try:
            day = dt.date.fromisoformat(nxt)
        except ValueError:
            day = None
        if day:
            # Naeherung: 5 Kalendertage je 7 sind Handelstage
            calendar_days = (day - today).days
            approx_trading = (trading_days_ahead if trading_days_ahead is not None
                              else round(calendar_days * 5 / 7))
            if 0 <= approx_trading <= config.EARNINGS_BLACKOUT_DAYS:
                total += config.PENALTY_EARNINGS_SOON
                reasons.append(
                    f"Zahlen am {day.isoformat()} — in rund {approx_trading} "
                    f"Handelstagen (-{config.PENALTY_EARNINGS_SOON:.2f})")

    beta = f.get("beta")
    if beta and beta > config.HIGH_BETA_THRESHOLD:
        total += config.PENALTY_HIGH_BETA
        reasons.append(f"Beta {beta:.2f} über {config.HIGH_BETA_THRESHOLD:.1f} "
                       f"(-{config.PENALTY_HIGH_BETA:.2f})")
    return total, reasons


# -- Gesamtbewertung -------------------------------------------------------

def score(snap: dict, fundamentals: Optional[dict],
          llm: Optional[dict] = None,
          sector_median_pe: Optional[float] = None,
          weights: Optional[dict[str, float]] = None,
          today: Optional[dt.date] = None) -> dict:
    """Gesamtscore mit allen Komponenten, Abzuegen und Datenabdeckung."""
    weights = weights or config.SCORE_WEIGHTS
    price = snap.get("close")

    components = [
        trend(snap), setup(snap), volume(snap),
        quality(fundamentals),
        valuation(fundamentals, sector_median_pe),
        analysts(fundamentals, price),
        sentiment(llm),
    ]
    for c in components:
        c.weight = weights.get(c.key, c.weight)

    available = [c for c in components if c.available and c.weight > 0]
    total_weight = sum(c.weight for c in available)
    all_weight = sum(weights.get(c.key, 0) for c in components)
    coverage = total_weight / all_weight if all_weight else 0.0

    if not available:
        return {
            "score": None, "raw_score": None, "coverage": 0.0,
            "eligible": False,
            "components": [c.to_dict() for c in components],
            "penalties": [], "penalty_total": 0.0,
            "reason": "Keine einzige Komponente berechenbar.",
        }

    raw = sum(c.score * c.weight for c in available) / total_weight
    penalty_total, penalty_reasons = penalties(fundamentals, today)
    final = _clip01(raw - penalty_total)

    return {
        "score": round(final, 4),
        "raw_score": round(raw, 4),
        "coverage": round(coverage, 3),
        "eligible": coverage >= MIN_DATA_COVERAGE,
        "components": [c.to_dict() for c in components],
        "penalties": penalty_reasons,
        "penalty_total": round(penalty_total, 4),
        "missing": [c.key for c in components if not c.available],
    }


def sector_median_pes(entries: list[dict]) -> dict[str, float]:
    """Median-Forward-KGV je Branche, aus dem eigenen Universum berechnet.

    Ausreisser werden ausgeschlossen: negative Gewinne ergeben kein KGV,
    und ueber 100 ist der Wert fuer einen Median nicht mehr aussagekraeftig.
    """
    buckets: dict[str, list[float]] = {}
    for e in entries:
        f = e.get("fundamentals") or {}
        pe, sec = f.get("forward_pe"), e.get("sector")
        if pe and 0 < pe < 100 and sec:
            buckets.setdefault(sec, []).append(pe)
    out = {}
    for sec, vals in buckets.items():
        if len(vals) < 3:
            continue
        vals.sort()
        n = len(vals)
        out[sec] = (vals[n // 2] if n % 2
                    else (vals[n // 2 - 1] + vals[n // 2]) / 2)
    return out
