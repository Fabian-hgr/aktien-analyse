"""Kursziele — nachrechenbar hergeleitet.

Ein Kursziel hier ist ein GEWINNMITNAHME-NIVEAU fuer eine bedingte Bewegung:
"Wenn dieser Trade aufgeht, wo wird verkauft?" Das ist eine andere Frage als
"wo steht die Aktie im Mittel in 15 Tagen". Diese Unterscheidung bestimmt den
ganzen Aufbau:

  NIVEAU liefern zwei Methoden — beide beantworten die bedingte Frage:
    1  ATR-Projektion    aus der Schwankungsbreite der Aktie
    2  Struktur          naechster echter Widerstand oder gemessene Bewegung

  NEIGUNG liefern zwei Quellen — sie sagen etwas ueber Richtung und
  Wahrscheinlichkeit, nicht ueber das Niveau:
    3  Analystenkonsens  12-Monats-Ziel als Rueckenwind oder Gegenwind
    4  Bewertung         fairer Wert gegen Kurs

Warum Analysten NICHT gemittelt werden: ein 12-Monats-Ziel mit dem Zeitanteil
15/252 heruntergerechnet liegt fast immer auf Kursniveau. Im Mittelwert zieht
es damit jedes Ziel zum Kurs hin und zerstoert das Chance-Risiko-Verhaeltnis
systematisch — bei Apple gemessen von 2.6 auf 0.84. Als Neigung wirkt
dieselbe Information richtig: sie verschiebt das Ziel um wenige Prozent.

Jede Methode liefert die Rechenschritte mit den eingesetzten Zahlen mit.
Die Seite zeigt genau diese Schritte, damit jedes Ziel von Hand nachvollzogen
werden kann.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from . import calibration as kalib, config

TRADING_DAYS_PER_YEAR = 252


@dataclass
class Method:
    """Ergebnis einer Kurszielmethode oder einer Neigungsquelle."""
    key: str
    label: str
    value: Optional[float]          # USD-Niveau, None wenn nicht bestimmbar
    steps: list[str] = field(default_factory=list)
    note: str = ""
    role: str = "niveau"            # "niveau" oder "neigung"

    @property
    def available(self) -> bool:
        return self.value is not None

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "role": self.role,
            "value": round(self.value, 2) if self.value is not None else None,
            "steps": self.steps,
            "note": self.note,
        }


def _fmt(x: float, digits: int = 2) -> str:
    return f"{x:,.{digits}f}".replace(",", "'")


def _clip(x: float, cap: float) -> float:
    return max(-cap, min(cap, x))


# -- Niveau 1: ATR-Projektion ----------------------------------------------

def atr_projection(price: float, atr: Optional[float],
                   k: Optional[float] = None, herkunft: str = "") -> Method:
    k = config.ATR_TARGET_MULT if k is None else k
    m = Method("atr", config.TARGET_METHOD_LABELS["atr"], None)
    if not atr or atr <= 0 or price <= 0:
        m.note = "Keine ATR verfügbar (zu kurze Historie)."
        return m
    m.value = price + k * atr
    m.steps = [
        f"ATR(14) = {_fmt(atr)} USD, das sind {_fmt(atr / price * 100)} % vom Kurs",
        f"Faktor k = {_fmt(k)}{herkunft}",
        f"Ziel = {_fmt(price)} + {_fmt(k)} x {_fmt(atr)} = {_fmt(m.value)} USD",
    ]
    return m


def atr_stop(price: float, atr: Optional[float],
             stop_mult: Optional[float] = None) -> tuple[Optional[float], list[str]]:
    stop_mult = config.ATR_STOP_MULT if stop_mult is None else stop_mult
    if not atr or atr <= 0:
        return None, []
    stop = price - stop_mult * atr
    return stop, [
        f"Stop = {_fmt(price)} - {_fmt(stop_mult)} x {_fmt(atr)} = {_fmt(stop)} USD",
    ]


# -- Niveau 2: Struktur / gemessene Bewegung -------------------------------

def structure_target(price: float, high55: Optional[float],
                     high20: Optional[float], low20: Optional[float],
                     atr: Optional[float],
                     cap_atr: Optional[float] = None,
                     cap_grund: str = "") -> Method:
    """Naechster echter Widerstand — oder, wenn dieser schon gebrochen oder
    in Rauschweite ist, die Basishoehe auf das Ausbruchsniveau aufgeschlagen.
    """
    m = Method("struktur", config.TARGET_METHOD_LABELS["struktur"], None)
    if not high55 or not high20 or not low20 or price <= 0:
        m.note = "Zu wenig Historie für Widerstandsmarken."
        return m

    base_height = high20 - low20
    distance = high55 - price
    min_distance = (atr or 0) * config.STRUCTURE_MIN_ATR_DISTANCE

    if distance > min_distance:
        m.value = high55
        m.steps = [
            f"55-Tage-Hoch = {_fmt(high55)} USD, das sind "
            f"{_fmt(distance)} USD über dem Kurs {_fmt(price)}",
            f"Abstand grösser als 1 x ATR ({_fmt(min_distance)} USD) — "
            f"echter Widerstand, nicht Rauschen",
            f"Ziel = {_fmt(high55)} USD ({_fmt((high55 / price - 1) * 100)} %)",
        ]
    else:
        m.value = high55 + base_height
        reason = ("Kurs liegt auf oder über dem 55-Tage-Hoch — Ausbruch"
                  if distance <= 0 else
                  f"Abstand {_fmt(distance)} USD liegt innerhalb einer ATR "
                  f"({_fmt(min_distance)} USD) — kein tragfähiger Widerstand")
        m.steps = [
            f"55-Tage-Hoch = {_fmt(high55)} USD. {reason}.",
            f"Basishoehe = 20-Tage-Hoch {_fmt(high20)} minus 20-Tage-Tief "
            f"{_fmt(low20)} = {_fmt(base_height)} USD",
            f"Ziel = {_fmt(high55)} + {_fmt(base_height)} = {_fmt(m.value)} USD",
        ]

    # Deckel: ein Ziel nuetzt nichts, wenn es nie erreicht wird. Die Grenze
    # kommt aus der Messung, nicht aus einer Schaetzung.
    if atr and atr > 0:
        k = cap_atr or config.STRUCTURE_CAP_ATR_FALLBACK
        cap = price + k * atr
        if m.value > cap:
            m.steps.append(
                f"Gekappt auf {_fmt(k)} x ATR über dem Kurs = {_fmt(cap)} USD"
                + (cap_grund or " (Rückfallwert ohne Kalibrierung)")
            )
            m.value = cap
    return m


# -- Neigung 3: Analystenkonsens -------------------------------------------

def analyst_tilt(price: float, target_mean: Optional[float],
                 analyst_count: Optional[float],
                 horizon_days: Optional[int] = None) -> tuple[float, Method]:
    """Analystenziele als Rueckenwind, nicht als Niveau.

    Gibt (Neigung, Methode) zurueck. Die Neigung ist der gekappte relative
    Abstand des 12-Monats-Konsensziels zum Kurs.
    """
    horizon_days = config.HORIZON_DAYS if horizon_days is None else horizon_days
    m = Method("analysten", config.TARGET_METHOD_LABELS["analysten"], None, role="neigung")
    if not target_mean or target_mean <= 0 or price <= 0:
        m.note = "Kein Analystenziel verfügbar."
        return 0.0, m
    if not analyst_count or analyst_count < 3:
        m.note = (f"Nur {analyst_count or 0:.0f} Analysten — "
                  f"zu duenne Abdeckung, nicht verwendet.")
        return 0.0, m

    raw = target_mean / price - 1.0
    tilt = _clip(raw, config.TILT_CAP)
    shift = tilt * config.TILT_STRENGTH_ANALYST
    implied = price * (1 + raw * horizon_days / TRADING_DAYS_PER_YEAR)

    m.value = target_mean
    m.steps = [
        f"Konsensziel auf 12 Monate: {_fmt(target_mean)} USD "
        f"({analyst_count:.0f} Analysten)",
        f"Abstand zum Kurs {_fmt(price)}: {_fmt(raw * 100)} %"
        + (f" (gekappt auf {_fmt(tilt * 100)} %)" if raw != tilt else ""),
        f"Zeitanteilig auf {horizon_days} Handelstage wären das nur "
        f"{_fmt(implied)} USD — deshalb kein eigenes Ziel, sondern Neigung",
        f"Neigung = {_fmt(tilt * 100)} % x {_fmt(config.TILT_STRENGTH_ANALYST, 2)} "
        f"= {_fmt(shift * 100)} % auf das Gesamtziel",
    ]
    m.note = ("12-Monats-Sicht. Wirkt als Richtungshinweis, nicht als "
              "Kursziel für den Horizont.")
    return tilt, m


# -- Neigung 4: Bewertungsanker --------------------------------------------

def valuation_tilt(price: float, forward_eps: Optional[float],
                   sector_median_pe: Optional[float],
                   sector: str = "") -> tuple[float, Method]:
    m = Method("bewertung", config.TARGET_METHOD_LABELS["bewertung"], None, role="neigung")
    if not forward_eps or forward_eps <= 0 or not sector_median_pe \
            or sector_median_pe <= 0 or price <= 0:
        m.note = "Kein Forward-Gewinn oder kein Branchenmedian verfügbar."
        return 0.0, m

    fair = forward_eps * sector_median_pe
    raw = fair / price - 1.0
    tilt = _clip(raw, config.TILT_CAP)
    shift = tilt * config.TILT_STRENGTH_VALUATION

    m.value = fair
    m.steps = [
        f"Erwarteter Gewinn je Aktie (12 Monate): {_fmt(forward_eps)} USD",
        f"Median-Forward-KGV der Branche {sector or 'n/a'}: "
        f"{_fmt(sector_median_pe, 1)}",
        f"Fairer Wert = {_fmt(forward_eps)} x {_fmt(sector_median_pe, 1)} "
        f"= {_fmt(fair)} USD",
        f"Abweichung zum Kurs: {_fmt(raw * 100)} %"
        + (f" (gekappt auf {_fmt(tilt * 100)} %)" if raw != tilt else ""),
        f"Neigung = {_fmt(tilt * 100)} % x "
        f"{_fmt(config.TILT_STRENGTH_VALUATION, 2)} "
        f"= {_fmt(shift * 100)} % auf das Gesamtziel",
    ]
    m.note = ("Bewertung wirkt über Monate, nicht über Wochen — deshalb "
              "nur eine Neigung.")
    return tilt, m


# -- Zusammenfuehrung ------------------------------------------------------

def expected_move(price: float, annual_vol: Optional[float],
                  horizon_days: Optional[int] = None) -> Optional[float]:
    """Eine Standardabweichung der Kursbewegung ueber den Horizont, in USD.

    Das ist die ehrliche Unsicherheit: sie beschreibt, wie weit der Kurs in
    der Zeit ueberhaupt wandern kann. Die Streuung der Methoden ist dagegen
    nur Uneinigkeit zwischen Modellen und wird getrennt ausgewiesen.
    """
    horizon_days = config.HORIZON_DAYS if horizon_days is None else horizon_days
    if not annual_vol or annual_vol <= 0 or price <= 0:
        return None
    return price * annual_vol * math.sqrt(horizon_days / TRADING_DAYS_PER_YEAR)


def marks(sector: str, cal=None, k_sector=None, stop_mult=None,
          k_mult=None) -> dict:
    """Ziel- und Stop-Faktor in ATR-Einheiten, mit ihrer Herkunft.

    Vorrang hat immer eine ausdrueckliche Vorgabe (Backtest, Tests). Sonst
    kommen beide Faktoren aus der Kalibrierung, also aus der gemessenen
    Bewegung des ganzen Universums. Erst wenn auch die fehlt, greifen die
    Startwerte aus config.

    Der GELERNTE Anteil ist bewusst nur ein Multiplikator auf den Zielfaktor,
    begrenzt auf [0.7, 1.4]. Der Stop wird NICHT gelernt: er wuerde sich aus
    den eigenen Trades selbst nach unten ziehen — ein enger Stop schneidet
    jede Messung der Bewegung ab, was den naechsten Stop noch enger macht.
    Die Universumsmessung kennt diese Rueckkopplung nicht.
    """
    quelle = "config"
    k_basis, s_basis = config.ATR_TARGET_MULT, config.ATR_STOP_MULT
    if cal:
        k_basis, s_basis = kalib.factors(cal, sector)
        quelle = "kalibriert"

    mult = 1.0 if k_mult is None else max(config.SECTOR_K_MULT_MIN,
                                          min(config.SECTOR_K_MULT_MAX, k_mult))
    ziel_k = k_basis * mult
    stop_k = s_basis
    if k_sector is not None:
        ziel_k, quelle, mult = k_sector, "vorgegeben", 1.0
    if stop_mult is not None:
        stop_k = stop_mult

    text = {
        "kalibriert": (" — gemessener Median der Aufwärtsbewegung in "
                       + (sector or "allen Branchen")),
        "config": " (Startwert aus config, keine Kalibrierung vorhanden)",
        "vorgegeben": " (ausdrücklich vorgegeben)",
    }[quelle]
    if quelle == "kalibriert" and abs(mult - 1.0) > 0.005:
        text += ", gelernter Multiplikator " + _fmt(mult, 2)

    return {"ziel_k": ziel_k, "stop_k": stop_k, "basis_ziel_k": k_basis,
            "k_mult": round(mult, 3), "quelle": quelle, "herkunft_text": text}


def probabilities(cal, sector: str, price: float, atr, target, stop) -> dict:
    """Gemessene Wahrscheinlichkeiten fuer genau diese beiden Marken.

    Zwei verschiedene Dinge, die nicht verwechselt werden duerfen:

      Beruehrungsquote — wie oft wurde ein Niveau in dieser Entfernung
      ueberhaupt erreicht. Ziel und Stop koennen beide haeufig beruehrt
      werden; die Summe ist nicht 1.

      Basisquote — was kam ZUERST. Nur das entscheidet ueber den Ausgang.
      Sie ist die Messlatte: was eine ZUFAELLIGE Auswahl mit diesen Marken
      erreicht. Was die Analyse darueber hinaus schafft, ist ihr Beitrag.
    """
    out: dict = {"ziel_atr": None, "stop_atr": None, "p_ziel_beruehrt": None,
                 "p_stop_beruehrt": None, "basisquote": None}
    if not atr or atr <= 0 or target is None or stop is None:
        return out
    ziel_atr = (target - price) / atr
    stop_atr = (price - stop) / atr
    out["ziel_atr"] = round(ziel_atr, 2)
    out["stop_atr"] = round(stop_atr, 2)
    out["p_ziel_beruehrt"] = kalib.hit_probability(cal, sector, "up", ziel_atr)
    out["p_stop_beruehrt"] = kalib.hit_probability(cal, sector, "down", stop_atr)
    out["basisquote"] = kalib.outcome(cal, sector, ziel_atr, stop_atr)
    return out


def build(price: float, snap: dict, fundamentals: Optional[dict],
          weights: Optional[dict[str, float]] = None, sector: str = "",
          sector_median_pe: Optional[float] = None,
          k_sector: Optional[float] = None,
          stop_mult: Optional[float] = None,
          k_mult: Optional[float] = None,
          cal: Optional[dict] = None,
          tilt_strength_analyst: Optional[float] = None,
          tilt_strength_valuation: Optional[float] = None) -> dict:
    """Vollstaendige Kurszielrechnung fuer einen Titel.

    Gibt Ziel, Bandbreite, Stop, Chance-Risiko-Verhaeltnis, die gemessenen
    Wahrscheinlichkeiten und alle Herleitungsschritte zurueck.
    """
    weights = dict(weights or config.TARGET_METHOD_WEIGHTS)
    sa = (config.TILT_STRENGTH_ANALYST if tilt_strength_analyst is None
          else tilt_strength_analyst)
    sv = (config.TILT_STRENGTH_VALUATION if tilt_strength_valuation is None
          else tilt_strength_valuation)

    f = fundamentals or {}
    atr = snap.get("atr")
    mk = marks(sector, cal, k_sector, stop_mult, k_mult)

    # Deckel fuer die Erreichbarkeit: gemessen, wo immer es geht.
    gemessener_deckel = kalib.distance_for_probability(
        cal, sector, "up", config.STRUCTURE_CAP_PROBABILITY)
    cap_atr = gemessener_deckel or config.STRUCTURE_CAP_ATR_FALLBACK
    cap_grund = (f" — weiter wurde in {sector or 'allen Branchen'} historisch "
                 f"nur in {(config.STRUCTURE_CAP_PROBABILITY) * 100:.0f} % der Fälle "
                 f"gelaufen" if gemessener_deckel
                 else " (Rückfallwert ohne Kalibrierung)")

    level_methods = [
        atr_projection(price, atr, k=mk["ziel_k"], herkunft=mk["herkunft_text"]),
        structure_target(price, snap.get("donchian_high55"),
                         snap.get("donchian_high20"),
                         snap.get("donchian_low20"), atr,
                         cap_atr=cap_atr, cap_grund=cap_grund),
    ]
    a_tilt, m_analyst = analyst_tilt(price, f.get("target_mean"),
                                     f.get("analyst_count"))
    v_tilt, m_valuation = valuation_tilt(price, f.get("forward_eps"),
                                         sector_median_pe, sector)

    usable = [m for m in level_methods if m.available and weights.get(m.key, 0) > 0]
    all_methods = level_methods + [m_analyst, m_valuation]

    if not usable:
        return {
            "price": round(price, 2),
            "target": None,
            "stop": None,
            "reward_risk": None,
            "reason": "Keine Kurszielmethode lieferte ein Niveau.",
            "methods": [m.to_dict() for m in all_methods],
            "blend_steps": [],
            "stop_steps": [],
            "marks": mk,
        }

    total_w = sum(weights[m.key] for m in usable)
    base = sum(m.value * weights[m.key] for m in usable) / total_w

    steps = ["Gewichteter Mittelwert der " + str(len(usable)) + " Niveau-Methoden:"]
    for m in usable:
        steps.append(
            f"   {m.label}: {_fmt(m.value)} USD x Gewicht "
            f"{_fmt(weights[m.key], 2)} ({_fmt(weights[m.key] / total_w * 100, 0)} %)"
        )
    steps.append(f"   = {_fmt(base)} USD")

    # Die Neigungen wirken auf den ABSTAND, nicht auf das Kursniveau. Sonst
    # haette dieselbe Neigung bei einer ruhigen Aktie ein Vielfaches der
    # Wirkung - in ATR gerechnet, und nur die zaehlt fuer die Erreichbarkeit.
    factor = 1 + a_tilt * sa + v_tilt * sv
    distanz = base - price
    target = price + distanz * factor
    if a_tilt or v_tilt:
        steps.append(
            f"Neigungen: Analysten {_fmt(a_tilt * 100)} % x {_fmt(sa, 2)}, "
            f"Bewertung {_fmt(v_tilt * 100)} % x {_fmt(sv, 2)} "
            f"-> Faktor {_fmt(factor, 4)} auf den Abstand"
        )
        steps.append(
            f"Ziel = {_fmt(price)} + {_fmt(distanz)} x {_fmt(factor, 4)} "
            f"= {_fmt(target)} USD"
        )

    # Letzter Deckel: auch nach den Neigungen muss das Ziel erreichbar bleiben.
    if atr and atr > 0 and cap_atr:
        obergrenze = price + cap_atr * atr
        if target > obergrenze:
            steps.append(
                f"Auf {_fmt(cap_atr)} x ATR gekappt = {_fmt(obergrenze)} USD"
                f"{cap_grund}"
            )
            target = obergrenze

    sigma = expected_move(price, snap.get("vol_20d"))
    band_low = band_high = None
    if sigma:
        band_low, band_high = target - sigma, target + sigma
        steps.append(
            f"Erwartungsbereich (1 Sigma über {config.HORIZON_DAYS} Tage bei "
            f"{_fmt((snap.get('vol_20d') or 0) * 100, 1)} % Volatilität): "
            f"{_fmt(band_low)} bis {_fmt(band_high)} USD"
        )

    stop, stop_steps = atr_stop(price, atr, mk["stop_k"])
    if stop_steps:
        stop_steps.insert(0, "Stop-Faktor = " + _fmt(mk["stop_k"]) + " x ATR"
                          + (" — gemessener Median der Abwärtsbewegung"
                             if mk["quelle"] == "kalibriert" else ""))
    values = [m.value for m in usable]

    result = {
        "price": round(price, 2),
        "target": round(target, 2),
        "upside_pct": round((target / price - 1) * 100, 2),
        "band_low": round(band_low, 2) if band_low else None,
        "band_high": round(band_high, 2) if band_high else None,
        "sigma": round(sigma, 2) if sigma else None,
        "method_spread": round(max(values) - min(values), 2) if len(values) > 1 else None,
        "methods_used": [m.key for m in usable],
        "methods": [m.to_dict() for m in all_methods],
        "blend_steps": steps,
        "stop": round(stop, 2) if stop else None,
        "stop_steps": stop_steps,
        "analyst_tilt": round(a_tilt, 4),
        "valuation_tilt": round(v_tilt, 4),
        "horizon_days": config.HORIZON_DAYS,
        "marks": mk,
    }

    if stop and price > stop:
        risk = price - stop
        reward = target - price
        result["risk"] = round(risk, 2)
        result["reward"] = round(reward, 2)
        result["reward_risk"] = round(reward / risk, 2) if risk > 0 else None
    else:
        result["reward_risk"] = None

    result.update(probabilities(cal, sector, price, atr, target, result["stop"]))
    bq = result.get("basisquote")
    if bq and result["p_ziel_beruehrt"] is not None:
        quelle_text = (", alle Branchen)" if bq["quelle"] == "_gesamt"
                       else ", Branche " + bq["quelle"] + ")")
        result["probability_steps"] = [
            f"Ziel liegt {_fmt(result['ziel_atr'])} ATR über dem Kurs, "
            f"Stop {_fmt(result['stop_atr'])} ATR darunter",
            f"Historisch berührt: Ziel in {(result['p_ziel_beruehrt']) * 100:.0f} % "
            f"der Fälle, Stop in {(result['p_stop_beruehrt']) * 100:.0f} %",
            f"Zuerst erreicht wurde bei diesen Marken das Ziel in "
            f"{(bq['p_ziel']) * 100:.0f} %, der Stop in {(bq['p_stop']) * 100:.0f} %, "
            f"Zeitablauf {(bq['p_zeit']) * 100:.0f} %",
            f"Basisquote einer Zufallsauswahl: {bq['erwartung_r']:+.3f} R "
            f"je Trade (aus {bq['n']:,} Beobachtungen".replace(",", "'")
            + quelle_text,
            "Die Analyse muss diese Basisquote schlagen — sonst ist sie "
            "nicht besser als Würfeln.",
        ]

    if f.get("target_mean"):
        result["analyst_target_12m"] = round(f["target_mean"], 2)
        result["analyst_count"] = int(f.get("analyst_count") or 0)
        result["analyst_recommendation"] = f.get("recommendation")
    return result
