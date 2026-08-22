"""Kalibrierung: wie weit laeuft ein Kurs binnen 15 Handelstagen wirklich?

Gemessen wird ueber das GESAMTE Universum und alle verfuegbaren Tage — nicht
ueber die eigenen Trades. Das ist entscheidend: wuerde man den ATR-Faktor aus
den eigenen Trades lernen, entstuende eine Rueckkopplung. Ein zu enger Stop
schneidet die gemessene Bewegung ab, das Ziel wandert naeher, der Stop wird
noch enger — eine Abwaertsspirale.

Die Universumsstatistik kennt diese Rueckkopplung nicht. Sie sagt schlicht:
"In dieser Branche lief der Kurs in der Haelfte der Faelle mindestens 2.0 ATR
nach oben, bevor 15 Tage um waren."

Gemessen am 2026-08-19 ueber 212'861 Beobachtungen aus 532 Titeln:

    Aufwaerts   Median 2.01 ATR   75. Perzentil 3.47 ATR
    Abwaerts    Median 1.66 ATR   75. Perzentil 2.99 ATR

    Ziel 2.2 ATR wird in 46 % der Faelle beruehrt
    Ziel 5.5 ATR nur noch in  8.5 %

ZWEI VERSCHIEDENE ZAHLEN, die leicht verwechselt werden:

  Beruehrungsquote  — wie oft wird ein Niveau ueberhaupt erreicht. Ziel und
                      Stop koennen BEIDE in der Haelfte der Faelle beruehrt
                      werden; die Summe muss nicht 1 ergeben.
  Erstpassage       — was kam ZUERST. Nur das entscheidet ueber den Ausgang
                      eines Trades. Dafuer gibt es das Gitter `first_passage`:
                      fuer jede Kombination aus Ziel- und Stop-Abstand die
                      gemessene Haeufigkeit von Ziel / Stop / Zeitablauf.

Die Erstpassage-Tabelle ist die Messlatte auf jeder Karte: sie sagt, was eine
ZUFAELLIGE Auswahl mit genau diesen Marken erreicht. Alles, was die Analyse
darueber hinaus schafft, ist echter Auswahlvorteil. Ein Chance-Risiko-
Verhaeltnis von 2.0 sagt fuer sich genommen nichts — es kommt darauf an, wie
oft das Ziel zuerst kommt.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import statistics
from typing import Optional

from . import config, indicators as ind

log = logging.getLogger(__name__)

MIN_BEOBACHTUNGEN = 2000        # je Branche, sonst Gesamtwert nehmen
MIN_SPEICHERN = 200             # weniger wird gar nicht erst abgelegt

# Stuetzstellen in ATR-Einheiten. Zwischen ihnen wird interpoliert.
GITTER = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0]
ZIEL_GITTER = GITTER
STOP_GITTER = [0.8, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]


def _path():
    return config.DATA_DIR / "calibration.json"


# ── Messung ────────────────────────────────────────────────────────────────

def _erstpassage_schluessel(z: float, s: float) -> str:
    return f"{z:g}|{s:g}"


def measure(bars: dict[str, list[dict]], sectors: dict[str, str],
            horizon: Optional[int] = None,
            min_index: int = 60,
            min_observations: Optional[int] = None) -> dict:
    """Beruehrungsverteilung UND Erstpassage-Gitter in einem Durchgang.

    Fuer jede Beobachtung wird das Vorwaertsfenster genau einmal durchlaufen.
    Dabei entsteht je Stuetzstelle der erste Tag, an dem sie beruehrt wurde
    (`t_auf`, `t_ab`). Aus dem Vergleich zweier solcher Zeitpunkte folgt der
    Ausgang fuer jede Ziel-Stop-Kombination — ohne das Fenster erneut zu
    durchlaufen.

    Bei Gleichstand am selben Tag gewinnt der Stop. Tagesbars loesen die
    Reihenfolge innerhalb eines Tages nicht auf; die pessimistische Annahme
    ist fuer einen Ehrlichkeitstest die richtige.
    """
    horizon = horizon or config.HORIZON_DAYS
    mindestens = MIN_SPEICHERN if min_observations is None else min_observations
    nz, ns = len(ZIEL_GITTER), len(STOP_GITTER)

    auf: dict[str, list[float]] = {"_gesamt": []}
    ab: dict[str, list[float]] = {"_gesamt": []}
    # je Branche eine flache Liste: [n, ziel, stop, zeit, r_summe] * (nz*ns)
    passage: dict[str, list[float]] = {}

    def eimer(key: str) -> list[float]:
        e = passage.get(key)
        if e is None:
            e = [0.0] * (nz * ns * 5)
            passage[key] = e
        return e

    for sym, series in bars.items():
        if len(series) < min_index + horizon + 1:
            continue
        sektor = sectors.get(sym, "Unbekannt")
        pre = ind.precompute(series)
        e_sektor, e_gesamt = eimer(sektor), eimer("_gesamt")
        auf_s = auf.setdefault(sektor, [])
        ab_s = ab.setdefault(sektor, [])

        for i in range(min_index, len(series) - horizon):
            atr = pre["atr"][i]
            if not atr or atr <= 0:
                continue
            entry = float(series[i]["c"])

            hoch = -math.inf
            tief = math.inf
            t_auf: list[Optional[int]] = [None] * nz
            t_ab: list[Optional[int]] = [None] * ns
            iz = is_ = 0
            for d in range(1, horizon + 1):
                b = series[i + d]
                h, l = float(b["h"]), float(b["l"])
                if h > hoch:
                    hoch = h
                    while iz < nz and hoch >= entry + ZIEL_GITTER[iz] * atr:
                        t_auf[iz] = d
                        iz += 1
                if l < tief:
                    tief = l
                    while is_ < ns and tief <= entry - STOP_GITTER[is_] * atr:
                        t_ab[is_] = d
                        is_ += 1

            d_auf = (hoch - entry) / atr
            d_ab = (entry - tief) / atr
            auf_s.append(d_auf)
            ab_s.append(d_ab)
            auf["_gesamt"].append(d_auf)
            ab["_gesamt"].append(d_ab)

            r_zeit_atr = (float(series[i + horizon]["c"]) - entry) / atr

            for j in range(nz):
                tj = t_auf[j]
                z = ZIEL_GITTER[j]
                basis = j * ns * 5
                for k in range(ns):
                    tk = t_ab[k]
                    s = STOP_GITTER[k]
                    if tk is not None and (tj is None or tk <= tj):
                        spalte, r = 2, -1.0            # Stop zuerst (Gleichstand: Stop)
                    elif tj is not None:
                        spalte, r = 1, z / s           # Ziel zuerst
                    else:
                        spalte, r = 3, r_zeit_atr / s  # nichts beruehrt
                    p = basis + k * 5
                    e_sektor[p] += 1
                    e_sektor[p + spalte] += 1
                    e_sektor[p + 4] += r
                    e_gesamt[p] += 1
                    e_gesamt[p + spalte] += 1
                    e_gesamt[p + 4] += r

    def zusammenfassen(werte: list[float]) -> dict:
        werte = sorted(werte)
        n = len(werte)
        return {
            "n": n,
            "median": round(statistics.median(werte), 3),
            "p75": round(werte[int(n * 0.75)], 3),
            "p90": round(werte[int(n * 0.90)], 3),
            "erreichbar": {str(k): round(
                sum(1 for x in werte if x >= k) / n, 4) for k in GITTER},
        }

    fp = {}
    for key, e in passage.items():
        if e[0] < mindestens:
            continue
        tabelle = {}
        for j in range(nz):
            for k in range(ns):
                p = j * ns * 5 + k * 5
                n = e[p]
                if not n:
                    continue
                tabelle[_erstpassage_schluessel(ZIEL_GITTER[j], STOP_GITTER[k])] = [
                    round(e[p + 1] / n, 4),      # p_ziel
                    round(e[p + 2] / n, 4),      # p_stop
                    round(e[p + 3] / n, 4),      # p_zeit
                    round(e[p + 4] / n, 4),      # Erwartungswert in R
                ]
        fp[key] = {"n": int(e[0]), "tabelle": tabelle}

    return {
        "measured_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "horizon_days": horizon,
        "observations": len(auf["_gesamt"]),
        "ziel_gitter": ZIEL_GITTER,
        "stop_gitter": STOP_GITTER,
        "up": {s: zusammenfassen(v) for s, v in auf.items()
               if len(v) >= mindestens},
        "down": {s: zusammenfassen(v) for s, v in ab.items()
                 if len(v) >= mindestens},
        "first_passage": fp,
    }


# ── Speichern und Laden ────────────────────────────────────────────────────

def save(cal: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cal, indent=1), encoding="utf-8")
    log.info("Kalibrierung gespeichert: %d Beobachtungen, %d Branchen",
             cal["observations"], len(cal["up"]) - 1)


def load() -> Optional[dict]:
    p = _path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("calibration.json unlesbar")
        return None


_zwischenspeicher: dict = {}


def get() -> Optional[dict]:
    """Kalibrierung einmal je Prozess laden."""
    if "cal" not in _zwischenspeicher:
        _zwischenspeicher["cal"] = load()
    return _zwischenspeicher["cal"]


# ── Abfragen ───────────────────────────────────────────────────────────────

def _bucket(cal: dict, richtung: str, sektor: str) -> Optional[dict]:
    daten = cal.get(richtung) or {}
    b = daten.get(sektor)
    if b and b["n"] >= MIN_BEOBACHTUNGEN:
        return b
    return daten.get("_gesamt")


def factors(cal: Optional[dict], sektor: str) -> tuple[float, float]:
    """(Ziel-Faktor, Stop-Faktor) in ATR-Einheiten fuer diese Branche.

    Beide auf dem Median der jeweiligen Richtung: das Ziel wird dann in rund
    der Haelfte der Faelle beruehrt, der Stop ebenso. Ausgewogene Ausgaenge
    liefern die meiste Information je Trade darueber, ob die Auswahl etwas
    taugt — und die wenigsten nichtssagenden Zeitausstiege.
    """
    if not cal:
        return config.ATR_TARGET_MULT, config.ATR_STOP_MULT
    up = _bucket(cal, "up", sektor)
    down = _bucket(cal, "down", sektor)
    ziel = up["median"] if up else config.ATR_TARGET_MULT
    stop = down["median"] if down else config.ATR_STOP_MULT
    return (max(1.0, min(4.0, ziel)), max(0.8, min(3.0, stop)))


def hit_probability(cal: Optional[dict], sektor: str, richtung: str,
                    atr_distance: Optional[float]) -> Optional[float]:
    """Wie oft wurde ein Niveau in dieser Entfernung historisch BERUEHRT?

    Das ist eine Randwahrscheinlichkeit — sie sagt nichts darueber, ob Ziel
    oder Stop zuerst kam. Dafuer ist `outcome()` da.
    """
    if not cal or not atr_distance or atr_distance <= 0:
        return None
    b = _bucket(cal, richtung, sektor)
    if not b:
        return None
    gitter = sorted((float(k), v) for k, v in b["erreichbar"].items())
    if atr_distance <= gitter[0][0]:
        return gitter[0][1]
    if atr_distance >= gitter[-1][0]:
        return gitter[-1][1]
    for (k1, v1), (k2, v2) in zip(gitter, gitter[1:]):
        if k1 <= atr_distance <= k2:
            anteil = (atr_distance - k1) / (k2 - k1)
            return round(v1 + (v2 - v1) * anteil, 4)
    return None


def distance_for_probability(cal, sektor: str, richtung: str,
                             p: float):
    """Wie weit darf ein Niveau hoechstens liegen, damit es noch in `p` der
    Faelle beruehrt wurde? Die Umkehrung von `hit_probability`.

    Damit laesst sich eine Kursziel-Methode nicht mehr ins Unerreichbare
    laufen: der Deckel ist keine geschaetzte Zahl mehr (frueher pauschal
    6 ATR), sondern das Niveau, das die Messung noch hergibt.
    """
    if not cal or not p or p <= 0 or p >= 1:
        return None
    b = _bucket(cal, richtung, sektor)
    if not b:
        return None
    gitter = sorted((float(k), v) for k, v in b["erreichbar"].items())
    if p >= gitter[0][1]:
        return gitter[0][0]
    if p <= gitter[-1][1]:
        return gitter[-1][0]
    for (k1, v1), (k2, v2) in zip(gitter, gitter[1:]):
        if v2 <= p <= v1:
            if v1 == v2:
                return k1
            return round(k1 + (k2 - k1) * (v1 - p) / (v1 - v2), 3)
    return None


def _umschliessen(gitter: list[float], x: float) -> tuple[int, int, float]:
    """Index-Paar und Anteil fuer lineare Interpolation, am Rand geklemmt."""
    if x <= gitter[0]:
        return 0, 0, 0.0
    if x >= gitter[-1]:
        n = len(gitter) - 1
        return n, n, 0.0
    for i in range(len(gitter) - 1):
        if gitter[i] <= x <= gitter[i + 1]:
            return i, i + 1, (x - gitter[i]) / (gitter[i + 1] - gitter[i])
    return len(gitter) - 1, len(gitter) - 1, 0.0


def outcome(cal: Optional[dict], sektor: str,
            ziel_atr: Optional[float], stop_atr: Optional[float]) -> Optional[dict]:
    """Was kam ZUERST — gemessen, fuer genau diese beiden Marken.

    Bilineare Interpolation im Erstpassage-Gitter. Ausserhalb des Gitters
    wird geklemmt; der Wert ist dann konservativ, nicht extrapoliert.

    Rueckgabe: p_ziel, p_stop, p_zeit, erwartung_r, n, quelle, geklemmt.
    Das ist die BASISQUOTE — was eine Zufallsauswahl in dieser Branche mit
    diesen Marken erreicht, nicht was dieser Titel erreichen wird.
    """
    if not cal or not ziel_atr or not stop_atr or ziel_atr <= 0 or stop_atr <= 0:
        return None
    fp = cal.get("first_passage") or {}
    quelle = sektor
    b = fp.get(sektor)
    if not b or b["n"] < MIN_BEOBACHTUNGEN:
        b, quelle = fp.get("_gesamt"), "_gesamt"
    if not b:
        return None

    zg = cal.get("ziel_gitter") or ZIEL_GITTER
    sg = cal.get("stop_gitter") or STOP_GITTER
    j0, j1, fz = _umschliessen(zg, ziel_atr)
    k0, k1, fs = _umschliessen(sg, stop_atr)
    tab = b["tabelle"]

    ecken = []
    for j, wz in ((j0, 1 - fz), (j1, fz)):
        if wz <= 0 and j != j0:
            continue
        for k, ws in ((k0, 1 - fs), (k1, fs)):
            if ws <= 0 and k != k0:
                continue
            v = tab.get(_erstpassage_schluessel(zg[j], sg[k]))
            if v:
                ecken.append((wz * ws, v))
    if not ecken:
        return None
    gewicht = sum(w for w, _ in ecken)
    gemischt = [sum(w * v[i] for w, v in ecken) / gewicht for i in range(4)]

    return {
        "p_ziel": round(gemischt[0], 4),
        "p_stop": round(gemischt[1], 4),
        "p_zeit": round(gemischt[2], 4),
        "erwartung_r": round(gemischt[3], 4),
        "n": b["n"],
        "quelle": quelle,
        "geklemmt": not (zg[0] <= ziel_atr <= zg[-1]
                         and sg[0] <= stop_atr <= sg[-1]),
    }


def base_rate(cal: Optional[dict], sektor: str) -> Optional[dict]:
    """Basisquote der Branche bei den kalibrierten Marken."""
    if not cal:
        return None
    ziel_k, stop_k = factors(cal, sektor)
    return outcome(cal, sektor, ziel_k, stop_k)


# ── Gegenprobe ─────────────────────────────────────────────────────────────

def measure_base_rates(bars: dict[str, list[dict]], sectors: dict[str, str],
                       cal: dict, horizon: Optional[int] = None,
                       min_index: int = 60,
                       min_observations: Optional[int] = None) -> dict:
    """Unabhaengige Nachrechnung der Basisquote — die Gegenprobe zum Gitter.

    Laeuft die Tage einzeln nach und beruecksichtigt zusaetzlich
    Eroeffnungsluecken (`open` unter dem Stop bzw. ueber dem Ziel), was das
    Gitter bewusst nicht tut. Weichen beide Wege stark voneinander ab, ist
    einer davon falsch — genau dafuer existiert diese Funktion.
    """
    horizon = horizon or config.HORIZON_DAYS
    mindestens = MIN_SPEICHERN if min_observations is None else min_observations
    zaehler: dict[str, dict] = {}

    for sym, series in bars.items():
        if len(series) < min_index + horizon + 1:
            continue
        sektor = sectors.get(sym, "Unbekannt")
        k_ziel, k_stop = factors(cal, sektor)
        pre = ind.precompute(series)
        for i in range(min_index, len(series) - horizon):
            atr = pre["atr"][i]
            if not atr or atr <= 0:
                continue
            entry = float(series[i]["c"])
            ziel = entry + k_ziel * atr
            stop = entry - k_stop * atr
            ausgang, r = "zeit", None
            for b in series[i + 1:i + 1 + horizon]:
                o, h, l = float(b["o"]), float(b["h"]), float(b["l"])
                if o <= stop:
                    ausgang, r = "stop", (o - entry) / (k_stop * atr)
                    break
                if o >= ziel:
                    ausgang, r = "ziel", (o - entry) / (k_stop * atr)
                    break
                if l <= stop:                    # bei Gleichstand zaehlt der Stop
                    ausgang, r = "stop", -1.0
                    break
                if h >= ziel:
                    ausgang, r = "ziel", k_ziel / k_stop
                    break
            if r is None:
                schluss = float(series[i + horizon]["c"])
                r = (schluss - entry) / (k_stop * atr)

            for schluessel in (sektor, "_gesamt"):
                z = zaehler.setdefault(schluessel, {
                    "n": 0, "ziel": 0, "stop": 0, "zeit": 0, "r_summe": 0.0})
                z["n"] += 1
                z[ausgang] += 1
                z["r_summe"] += r

    out = {}
    for schluessel, z in zaehler.items():
        if z["n"] < mindestens:
            continue
        n = z["n"]
        out[schluessel] = {
            "n": n,
            "p_ziel": round(z["ziel"] / n, 4),
            "p_stop": round(z["stop"] / n, 4),
            "p_zeit": round(z["zeit"] / n, 4),
            "erwartung_r": round(z["r_summe"] / n, 4),
        }
    return out


def summary_line(cal: Optional[dict]) -> str:
    if not cal:
        return "Keine Kalibrierung vorhanden — Standardfaktoren aus config."
    g_up = cal["up"].get("_gesamt", {})
    g_dn = cal["down"].get("_gesamt", {})
    n = f"{cal['observations']:,}".replace(",", "'")
    return (f"Kalibriert am {cal['measured_at'][:10]} ueber {n} Beobachtungen: "
            f"Median aufwaerts {g_up.get('median')} ATR, abwaerts "
            f"{g_dn.get('median')} ATR in {cal['horizon_days']} Handelstagen.")
