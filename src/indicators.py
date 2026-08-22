"""Technische Indikatoren — reine Rechnung, kein Netzzugriff.

Bewusst ohne pandas: die Reihen sind kurz (rund 275 Tage x 550 Titel), und
in reinem Python ist jede Formel gegen eine Handrechnung pruefbar. Genau das
tut tests/test_indicators.py.

Konventionen:
  - Eingabe ist eine Liste von Bars, aufsteigend nach Datum, mit den
    Alpaca-Schluesseln: o, h, l, c, v, t
  - Jede Reihenfunktion gibt eine Liste gleicher Laenge zurueck; Werte, die
    sich noch nicht berechnen lassen, sind None. Damit bleibt der Index
    immer deckungsgleich mit den Bars.
  - Geglaettet wird nach Wilder (RSI, ATR, ADX), wie in der Literatur und
    in gaengigen Charting-Programmen.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

Bar = dict
Series = list[Optional[float]]


# -- Grundbausteine --------------------------------------------------------

def sma(values: Sequence[float], period: int) -> Series:
    out: Series = [None] * len(values)
    if period <= 0:
        return out
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def ema(values: Sequence[float], period: int) -> Series:
    """Exponentieller Durchschnitt, mit dem SMA der ersten `period` Werte
    angestossen — sonst haengt das Ergebnis am zufaelligen ersten Kurs."""
    out: Series = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    alpha = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * alpha + prev * (1 - alpha)
        out[i] = prev
    return out


def stdev(values: Sequence[float], period: int) -> Series:
    """Standardabweichung der Grundgesamtheit (Nenner n) — die Konvention
    fuer Bollinger-Baender."""
    out: Series = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        mean = sum(window) / period
        out[i] = math.sqrt(sum((x - mean) ** 2 for x in window) / period)
    return out


def _wilder(values: Sequence[Optional[float]], period: int,
            first_index: int) -> Series:
    """Wilder-Glaettung: erster Wert ist der einfache Durchschnitt,
    danach avg = (avg * (n-1) + neu) / n."""
    out: Series = [None] * len(values)
    seed = [v for v in values[first_index:first_index + period] if v is not None]
    if len(seed) < period:
        return out
    prev = sum(seed) / period
    out[first_index + period - 1] = prev
    for i in range(first_index + period, len(values)):
        v = values[i]
        if v is None:
            continue
        prev = (prev * (period - 1) + v) / period
        out[i] = prev
    return out


# -- Indikatoren auf Bars --------------------------------------------------

def closes(bars: Sequence[Bar]) -> list[float]:
    return [float(b["c"]) for b in bars]


def true_range(bars: Sequence[Bar]) -> Series:
    out: Series = [None] * len(bars)
    for i in range(1, len(bars)):
        h, l = float(bars[i]["h"]), float(bars[i]["l"])
        pc = float(bars[i - 1]["c"])
        out[i] = max(h - l, abs(h - pc), abs(l - pc))
    return out


def atr(bars: Sequence[Bar], period: int = 14) -> Series:
    """Average True Range nach Wilder."""
    return _wilder(true_range(bars), period, first_index=1)


def rsi(bars: Sequence[Bar], period: int = 14) -> Series:
    """Relative Strength Index nach Wilder, 0..100."""
    c = closes(bars)
    gains: Series = [None] * len(c)
    losses: Series = [None] * len(c)
    for i in range(1, len(c)):
        diff = c[i] - c[i - 1]
        gains[i] = max(diff, 0.0)
        losses[i] = max(-diff, 0.0)

    avg_gain = _wilder(gains, period, first_index=1)
    avg_loss = _wilder(losses, period, first_index=1)

    out: Series = [None] * len(c)
    for i in range(len(c)):
        g, l = avg_gain[i], avg_loss[i]
        if g is None or l is None:
            continue
        if l == 0:
            out[i] = 100.0
        else:
            rs = g / l
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def adx(bars: Sequence[Bar], period: int = 14) -> tuple[Series, Series, Series]:
    """Trendstaerke nach Wilder. Gibt (ADX, +DI, -DI) zurueck."""
    n = len(bars)
    plus_dm: Series = [None] * n
    minus_dm: Series = [None] * n
    for i in range(1, n):
        up = float(bars[i]["h"]) - float(bars[i - 1]["h"])
        down = float(bars[i - 1]["l"]) - float(bars[i]["l"])
        plus_dm[i] = up if (up > down and up > 0) else 0.0
        minus_dm[i] = down if (down > up and down > 0) else 0.0

    atr_s = _wilder(true_range(bars), period, first_index=1)
    plus_s = _wilder(plus_dm, period, first_index=1)
    minus_s = _wilder(minus_dm, period, first_index=1)

    plus_di: Series = [None] * n
    minus_di: Series = [None] * n
    dx: Series = [None] * n
    for i in range(n):
        a, p, m = atr_s[i], plus_s[i], minus_s[i]
        if a is None or p is None or m is None or a == 0:
            continue
        plus_di[i] = 100.0 * p / a
        minus_di[i] = 100.0 * m / a
        total = plus_di[i] + minus_di[i]
        dx[i] = 0.0 if total == 0 else 100.0 * abs(plus_di[i] - minus_di[i]) / total

    first_dx = next((i for i, v in enumerate(dx) if v is not None), None)
    adx_s: Series = [None] * n if first_dx is None else _wilder(dx, period, first_dx)
    return adx_s, plus_di, minus_di


def bollinger(bars: Sequence[Bar], period: int = 20,
              mult: float = 2.0) -> tuple[Series, Series, Series]:
    """(Mittellinie, oberes Band, unteres Band)."""
    c = closes(bars)
    mid = sma(c, period)
    sd = stdev(c, period)
    upper: Series = [None] * len(c)
    lower: Series = [None] * len(c)
    for i in range(len(c)):
        if mid[i] is None or sd[i] is None:
            continue
        upper[i] = mid[i] + mult * sd[i]
        lower[i] = mid[i] - mult * sd[i]
    return mid, upper, lower


def donchian(bars: Sequence[Bar], period: int = 20) -> tuple[Series, Series]:
    """Hoechstes Hoch und tiefstes Tief der letzten `period` Bars —
    einschliesslich des aktuellen."""
    n = len(bars)
    high: Series = [None] * n
    low: Series = [None] * n
    for i in range(period - 1, n):
        window = bars[i - period + 1:i + 1]
        high[i] = max(float(b["h"]) for b in window)
        low[i] = min(float(b["l"]) for b in window)
    return high, low


def realised_volatility(bars: Sequence[Bar], period: int = 20) -> Optional[float]:
    """Annualisierte Volatilitaet aus logarithmischen Tagesrenditen."""
    c = closes(bars)
    if len(c) < period + 1:
        return None
    rets = [math.log(c[i] / c[i - 1]) for i in range(len(c) - period, len(c))
            if c[i - 1] > 0]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def relative_strength(bars: Sequence[Bar], benchmark: Sequence[Bar],
                      period: int = 63) -> Optional[float]:
    """Mehrrendite gegenueber dem Vergleichsindex ueber `period` Handelstage.

    0.05 heisst: fuenf Prozentpunkte besser als der Vergleich. Beide Reihen
    muessen dieselbe Laenge und dieselben Handelstage haben.
    """
    if len(bars) <= period or len(benchmark) <= period:
        return None
    def change(series: Sequence[Bar]) -> Optional[float]:
        old = float(series[-period - 1]["c"])
        return None if old <= 0 else float(series[-1]["c"]) / old - 1.0
    a, b = change(bars), change(benchmark)
    return None if a is None or b is None else a - b


def percent_change(bars: Sequence[Bar], period: int) -> Optional[float]:
    if len(bars) <= period:
        return None
    old = float(bars[-period - 1]["c"])
    return None if old <= 0 else float(bars[-1]["c"]) / old - 1.0


def gap_to_ma(bars: Sequence[Bar], period: int = 200) -> Optional[float]:
    """Abstand des Schlusskurses zum gleitenden Durchschnitt, relativ."""
    c = closes(bars)
    ma = sma(c, period)
    if not ma or ma[-1] is None or ma[-1] == 0:
        return None
    return c[-1] / ma[-1] - 1.0


def snapshot(bars: Sequence[Bar], benchmark: Sequence[Bar] | None = None,
             atr_period: int = 14) -> dict:
    """Alle Kennzahlen des juengsten Bars gebuendelt.

    Fehlende Werte bleiben None statt zu werfen — bei jungen Boersengaengen
    gibt es schlicht noch keine 200-Tage-Linie.
    """
    if not bars:
        return {}
    c = closes(bars)
    last = len(bars) - 1
    a = atr(bars, atr_period)
    r = rsi(bars, 14)
    adx_s, plus_di, minus_di = adx(bars, 14)
    mid, upper, lower = bollinger(bars, 20)
    dc_high20, dc_low20 = donchian(bars, 20)
    dc_high55, dc_low55 = donchian(bars, 55)

    price = c[last]
    atr_val = a[last]

    return {
        "bars": len(bars),
        "date": bars[last]["t"][:10],
        "close": price,
        "open": float(bars[last]["o"]),
        "high": float(bars[last]["h"]),
        "low": float(bars[last]["l"]),
        "volume": float(bars[last]["v"]),
        "atr": atr_val,
        "atr_pct": (atr_val / price) if atr_val and price else None,
        "rsi": r[last],
        "adx": adx_s[last],
        "plus_di": plus_di[last],
        "minus_di": minus_di[last],
        "ema9": (ema(c, 9) or [None])[last],
        "ema21": (ema(c, 21) or [None])[last],
        "ema50": (ema(c, 50) or [None])[last],
        "sma200": (sma(c, 200) or [None])[last],
        "bb_mid": mid[last],
        "bb_upper": upper[last],
        "bb_lower": lower[last],
        "donchian_high20": dc_high20[last],
        "donchian_low20": dc_low20[last],
        "donchian_high55": dc_high55[last],
        "donchian_low55": dc_low55[last],
        "vol_20d": realised_volatility(bars, 20),
        "chg_5d": percent_change(bars, 5),
        "chg_21d": percent_change(bars, 21),
        "chg_63d": percent_change(bars, 63),
        "gap_to_sma200": gap_to_ma(bars, 200),
        "rel_strength_63d": (relative_strength(bars, benchmark, 63)
                             if benchmark else None),
        "avg_volume_20d": (sum(float(b["v"]) for b in bars[-20:]) / 20
                           if len(bars) >= 20 else None),
        "dollar_volume_20d": (sum(float(b["c"]) * float(b["v"])
                                  for b in bars[-20:]) / 20
                              if len(bars) >= 20 else None),
    }


# -- Vorberechnung fuer den Backtest ---------------------------------------
#
# snapshot() rechnet jede Reihe neu. Fuer den Backtest waeren das 250 Tage x
# 528 Titel = 132'000 Neuberechnungen. Stattdessen werden alle Reihen EINMAL
# je Titel berechnet; snapshot_at() liest daraus nur den gewuenschten Index.
# Ergebnis ist identisch, nur ohne die 250-fache Wiederholung.

def precompute(bars: Sequence[Bar],
               benchmark: Sequence[Bar] | None = None,
               atr_period: int = 14) -> dict:
    """Alle Indikatorreihen eines Titels auf einen Schlag."""
    n = len(bars)
    c = closes(bars)
    adx_s, plus_di, minus_di = adx(bars, 14)
    mid, upper, lower = bollinger(bars, 20)
    dc_h20, dc_l20 = donchian(bars, 20)
    dc_h55, dc_l55 = donchian(bars, 55)

    # Rollende Fenster als Reihen statt als Einzelaufrufe
    vol: Series = [None] * n
    avg_vol: Series = [None] * n
    dollar_vol: Series = [None] * n
    for i in range(n):
        if i >= 20:
            window = bars[i - 19:i + 1]
            rets = [math.log(c[j] / c[j - 1])
                    for j in range(i - 19, i + 1) if c[j - 1] > 0]
            if len(rets) > 1:
                mean = sum(rets) / len(rets)
                var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
                vol[i] = math.sqrt(var) * math.sqrt(252)
            avg_vol[i] = sum(float(b["v"]) for b in window) / 20
            dollar_vol[i] = sum(float(b["c"]) * float(b["v"]) for b in window) / 20

    bench_closes = closes(benchmark) if benchmark else None

    return {
        "bars": bars,
        "closes": c,
        "atr": atr(bars, atr_period),
        "rsi": rsi(bars, 14),
        "adx": adx_s, "plus_di": plus_di, "minus_di": minus_di,
        "ema9": ema(c, 9), "ema21": ema(c, 21), "ema50": ema(c, 50),
        "sma200": sma(c, 200),
        "bb_mid": mid, "bb_upper": upper, "bb_lower": lower,
        "dc_h20": dc_h20, "dc_l20": dc_l20,
        "dc_h55": dc_h55, "dc_l55": dc_l55,
        "vol_20d": vol, "avg_volume_20d": avg_vol,
        "dollar_volume_20d": dollar_vol,
        "benchmark_closes": bench_closes,
    }


def _chg(values: Sequence[float], i: int, period: int) -> Optional[float]:
    if i < period:
        return None
    old = values[i - period]
    return None if old <= 0 else values[i] / old - 1.0


def snapshot_at(pre: dict, i: int) -> dict:
    """Kennzahlen zum Bar mit Index `i` — nur Daten bis einschliesslich i.

    Dass hier ausschliesslich vorwaertsgerichtete Reihen an Position i
    gelesen werden, ist genau die Eigenschaft, die den Backtest frei von
    Zukunftsblick haelt.
    """
    bars, c = pre["bars"], pre["closes"]
    if i < 0 or i >= len(bars):
        return {}
    price = c[i]
    atr_val = pre["atr"][i]

    rel = None
    bench = pre.get("benchmark_closes")
    if bench and i >= 63 and len(bench) > i:
        a = _chg(c, i, 63)
        b = _chg(bench, i, 63)
        if a is not None and b is not None:
            rel = a - b

    sma200 = pre["sma200"][i]
    return {
        "bars": i + 1,
        "date": bars[i]["t"][:10],
        "close": price,
        "open": float(bars[i]["o"]),
        "high": float(bars[i]["h"]),
        "low": float(bars[i]["l"]),
        "volume": float(bars[i]["v"]),
        "atr": atr_val,
        "atr_pct": (atr_val / price) if atr_val and price else None,
        "rsi": pre["rsi"][i],
        "adx": pre["adx"][i],
        "plus_di": pre["plus_di"][i], "minus_di": pre["minus_di"][i],
        "ema9": pre["ema9"][i], "ema21": pre["ema21"][i],
        "ema50": pre["ema50"][i], "sma200": sma200,
        "bb_mid": pre["bb_mid"][i], "bb_upper": pre["bb_upper"][i],
        "bb_lower": pre["bb_lower"][i],
        "donchian_high20": pre["dc_h20"][i], "donchian_low20": pre["dc_l20"][i],
        "donchian_high55": pre["dc_h55"][i], "donchian_low55": pre["dc_l55"][i],
        "vol_20d": pre["vol_20d"][i],
        "chg_5d": _chg(c, i, 5),
        "chg_21d": _chg(c, i, 21),
        "chg_63d": _chg(c, i, 63),
        "gap_to_sma200": (price / sma200 - 1.0) if sma200 else None,
        "rel_strength_63d": rel,
        "avg_volume_20d": pre["avg_volume_20d"][i],
        "dollar_volume_20d": pre["dollar_volume_20d"][i],
    }
