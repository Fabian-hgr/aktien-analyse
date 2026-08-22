"""Belohnung und Bestrafung — die Lernschleife.

Belohnung eines Trades ist das R-Multiple: (Ausstieg - Einstieg) / (Einstieg
- Stop). Gewinn belohnt, Verlust bestraft, Hoehe proportional zum Ergebnis.

Vier Dinge werden gelernt:

  1  Gewichte der Kursziel-Methoden — welche Methode traf wirklich? Gemessen
     an der groessten guenstigen Bewegung waehrend der Haltedauer, nicht am
     zufaelligen Ausstiegsgrund.
  2  Gewichte der Score-Komponenten — welche Komponente trennte Gewinner von
     Verlierern? Gemessen als Unterschied im mittleren R zwischen oberer und
     unterer Haelfte.
  3  Zielweite je Branche — als MULTIPLIKATOR auf den kalibrierten Faktor,
     nie als freier Wert. Gelernt wird aus dem Vergleich der tatsaechlichen
     Trefferquote mit der gemessenen Basisquote.
  4  Multiplikatoren je Branche und Marktregime.

Vier Grundsaetze:

  GRENZEN sind hart. Kein Gewicht verlaesst je seinen Bereich. Ein
  Ausreisser-Monat kann das System nicht kippen.

  EVIDENZ statt Groesse. Der Schritt richtet sich nach dem t-Wert des
  gemessenen Unterschieds, nicht nach seiner Groesse. Das ist keine
  Feinheit, sondern der Kern: am 21.08.2026 wurde gemessen, dass die
  Komponente `trend` ueber 130'535 Beobachtungen der klar beste Trenner ist
  (+1.25 Prozentpunkte) — waehrend dieselbe Komponente im 100-Trade-Fenster
  der Lernschleife als schaedlich erschien und bestraft wurde. Bei 100
  Trades betraegt der Standardfehler eines solchen Vorsprungs rund 0.26 R;
  wer auf Unterschiede dieser Groesse reagiert, lernt Rauschen und
  verschlechtert das System. Gemessen: mit der alten Regel fiel die Rendite
  von +5.45 % auf +2.97 %.

  DAEMPFUNG mit sqrt(n): bei 20 Trades wird kaum bewegt, bei 600 voll.
  Zusammen mit dem t-Wert heisst das: in den ersten Monaten passiert fast
  nichts. Das ist richtig so — vorher gibt es nichts zu wissen.

  NUR DAS KI-DEPOT. Die Trades des Zufallsdepots fliessen nirgends ein.
  Es ist die Kontrollgruppe — waere es Teil der Lernschleife, waere der
  ganze Vergleich wertlos.

Und eine Sache wird bewusst NICHT gelernt: die Weite von Ziel und Stop in
ATR-Einheiten. Sie kommt aus der Kalibrierung ueber das ganze Universum
(`calibration.py`). Aus den eigenen Trades gelernt entstuende eine
Rueckkopplung: ein enger Stop schneidet jede Messung der Bewegung ab, das
Ziel wandert naeher, der Stop wird noch enger. Gelernt wird nur ein
begrenzter Multiplikator auf den Zielfaktor — und der aus der Trefferquote,
nicht aus der abgeschnittenen Bewegung.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import statistics
from typing import Optional

from . import calibration, config

log = logging.getLogger(__name__)

MIN_TRADES_PER_BUCKET = 25      # darunter wird ein Sektor/Regime nicht angefasst
FULL_CONFIDENCE_AT = 600        # ab so vielen Trades volle Schrittweite
T_KAPPE = 2.0                   # ab |t| = 2 voller Ausschlag des Lernsignals
SCORE_WEIGHT_MIN = 0.02
SCORE_WEIGHT_MAX = 0.40


def default_weights() -> dict:
    return {
        "updated_at": None,
        "trades_seen": 0,
        # Die Startwerte bleiben unveraendert stehen. Ohne sie haette die
        # Lernkurve keinen Nullpunkt: man saehe, wo ein Gewicht heute steht,
        # aber nicht, ob es dorthin gelernt wurde oder immer schon dort lag.
        "start": {
            "score_weights": dict(config.SCORE_WEIGHTS),
            "target_method_weights": dict(config.TARGET_METHOD_WEIGHTS),
        },
        "score_weights": dict(config.SCORE_WEIGHTS),
        "target_method_weights": dict(config.TARGET_METHOD_WEIGHTS),
        "sector_k_mult": {},
        "sector_multiplier": {},
        "regime_multiplier": {},
        "history": [],
    }


def _path():
    return config.DATA_DIR / "weights.json"


def load() -> dict:
    p = _path()
    if not p.exists():
        return default_weights()
    try:
        w = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        log.warning("weights.json unlesbar — beginne mit Startgewichten")
        return default_weights()
    base = default_weights()
    base.update(w)
    return base


def save(weights: dict) -> None:
    """Gewichte schreiben — samt Regeln und Klartextnamen.

    Beides steht in `config` und wird bei jedem Schreiben neu aus dort
    uebernommen, nie aus der alten Datei fortgeschrieben: sonst zeigte die
    Seite Grenzen an, die im Code laengst andere sind. Damit ist
    weights.json aus sich heraus lesbar — auch fuer die Seite, die sonst
    dieselben Zahlen ein zweites Mal kennen muesste.
    """
    weights["regeln"] = {
        "min_trades": config.LEARN_MIN_TRADES,
        "fenster": config.LEARN_WINDOW,
        "lernrate": config.LEARN_RATE,
        "volle_schrittweite_ab": FULL_CONFIDENCE_AT,
        "min_trades_je_eimer": MIN_TRADES_PER_BUCKET,
        "grenzen": {
            "methode": [config.WEIGHT_MIN, config.WEIGHT_MAX],
            "komponente": [SCORE_WEIGHT_MIN, SCORE_WEIGHT_MAX],
            "multiplikator": [config.MULT_MIN, config.MULT_MAX],
            "zielweite": [config.SECTOR_K_MULT_MIN, config.SECTOR_K_MULT_MAX],
        },
    }
    weights["labels"] = {
        "score": dict(config.SCORE_LABELS),
        "methode": {k: config.TARGET_METHOD_LABELS[k]
                    for k in config.TARGET_METHOD_WEIGHTS},
    }
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(weights, indent=1, ensure_ascii=False),
                 encoding="utf-8")


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _damping(n: int) -> float:
    """Schrittweite nach Stichprobengroesse: sqrt(n/600), hoechstens 1."""
    return min(1.0, math.sqrt(n / FULL_CONFIDENCE_AT))


def _evidenz(t: Optional[float]) -> float:
    """Lernsignal in [-1, 1] aus einer Teststatistik.

    Ein Unterschied von einer Standardabweichung ist kein Befund. Erst bei
    |t| = 2 wird voll reagiert, darunter anteilig. Damit waechst der Schritt
    mit der SICHERHEIT der Messung und nicht mit ihrem Ausschlag — die
    Stellschraube, die eine Lernschleife von einem Rauschverstaerker
    trennt.
    """
    if t is None or t != t:                  # None oder NaN
        return 0.0
    if math.isinf(t):
        # Zwei Gruppen ohne jede Streuung, die sich unterscheiden: das ist
        # kein Rauschen, sondern der klarste denkbare Befund.
        return math.copysign(1.0, t)
    return _clip(t, -T_KAPPE, T_KAPPE) / T_KAPPE


def _anteil_t(treffer: int, n: int, erwartet: float) -> Optional[float]:
    """t-Wert eines Anteils gegen eine Erwartung (Binomialnaeherung)."""
    if n < 2 or not 0 < erwartet < 1:
        return None
    se = math.sqrt(erwartet * (1 - erwartet) / n)
    return (treffer / n - erwartet) / se if se > 0 else None


def _mittelwert_t(a: list[float], b: list[float]) -> Optional[float]:
    """t-Wert der Differenz zweier Mittelwerte (Welch, ohne Freiheitsgrade).

    Verschwindet die Streuung in beiden Gruppen, ist der Unterschied
    entweder null oder vollkommen eindeutig — dann unendlich.
    """
    if len(a) < 2 or len(b) < 2:
        return None
    diff = statistics.mean(a) - statistics.mean(b)
    se = math.sqrt(statistics.variance(a) / len(a)
                   + statistics.variance(b) / len(b))
    if se > 0:
        return diff / se
    return 0.0 if abs(diff) < 1e-12 else math.copysign(math.inf, diff)


def ki_trades(trades: list[dict]) -> list[dict]:
    """Nur das KI-Depot. Die Kontrollgruppe bleibt unberuehrt."""
    return [t for t in trades if t.get("depot") == "ki"]


# -- 1. Kursziel-Methoden --------------------------------------------------

def method_hit_rates(trades: list[dict],
                     cal: Optional[dict] = None) -> dict[str, dict]:
    """War das Ziel einer Methode oefter erreichbar, als ihr Abstand erwarten liess?

    Massstab ist die groesste guenstige Bewegung (mfe_price), nicht der
    Ausstiegsgrund: das gemischte Gesamtziel kann verfehlt worden sein,
    waehrend eine einzelne Methode richtig lag.

    Verglichen wird NICHT gegen 50 %. Das war ein Denkfehler der ersten
    Fassung: seit Ziel und Stop kalibriert sind, liegt die Trefferquote
    bauartbedingt bei rund 40 %, und beide Methoden wurden deshalb in jeder
    Runde bestraft — eine Einbahnstrasse nach unten statt einer Lernschleife.

    Verglichen wird gegen die MESSUNG, und zwar gegen die ERSTPASSAGE mit
    dem tatsaechlichen Stop: wie oft wurde ein Niveau in diesem ATR-Abstand
    historisch erreicht, BEVOR der Stop kam. Die reine Beruehrungsquote
    waere die falsche Messlatte, weil sie den ganzen 15-Tage-Zeitraum
    unterstellt — die eigene Beobachtung endet aber am Stop. Gegen die
    ungestoppte Quote gemessen sieht jede Methode zu schlecht aus (im
    Backtest 46 % gegen scheinbar erwartete 51 %).

    ZUGEGEBENE RESTVERZERRUNG: die Beobachtung endet auch am Gesamtziel.
    Eine Methode, deren Niveau ueber dem gemischten Gesamtziel liegt, kann
    nach einem Zielausstieg nicht mehr "recht bekommen". Der Effekt trifft
    die weiter aussen liegende Methode staerker. Sauber messbar waere das
    nur ausserhalb der eigenen Trades — dafuer gibt es die Kalibrierung.
    """
    cal = calibration.get() if cal is None else cal
    stats: dict[str, dict] = {}
    for t in trades:
        methods = t.get("target_methods") or {}
        mfe, entry = t.get("mfe_price"), t.get("entry_price")
        atr = t.get("atr_at_entry")
        if mfe is None or not entry:
            continue
        for key, level in methods.items():
            if level is None:
                continue
            s = stats.setdefault(key, {"n": 0, "hits": 0, "erwartet": []})
            s["n"] += 1
            if mfe >= level:
                s["hits"] += 1
            if cal and atr and atr > 0:
                stop_atr = t.get("stop_atr")
                if not stop_atr and t.get("stop"):
                    stop_atr = (entry - t["stop"]) / atr
                if not stop_atr:
                    continue
                erg = calibration.outcome(cal, t.get("sector") or "",
                                          (level - entry) / atr, stop_atr)
                if erg:
                    s["erwartet"].append(erg["p_ziel"])

    for key, s in stats.items():
        s["hit_rate"] = round(s["hits"] / s["n"], 3) if s["n"] else None
        if s["erwartet"]:
            erwartet = statistics.mean(s["erwartet"])
            s["erwartet_rate"] = round(erwartet, 3)
            s["edge"] = round(s["hits"] / s["n"] - erwartet, 4)
            s["t"] = _anteil_t(s["hits"], s["n"], erwartet)
        else:
            s["erwartet_rate"] = s["edge"] = s["t"] = None
        s.pop("erwartet")
    return stats


def update_method_weights(weights: dict, trades: list[dict],
                          cal: Optional[dict] = None) -> list[str]:
    """Die Methoden gegeneinander gewichten, nicht gegen einen Absolutwert.

    Fuer das Kursziel zaehlt nur das VERHAELTNIS der Gewichte — der
    Mittelwert wird ohnehin durch die Gewichtssumme geteilt. Genau deshalb
    wird hier das Signal jeder Methode um den Durchschnitt aller Methoden
    bereinigt: eine Verzerrung, die alle gleich trifft, faellt heraus.

    Das ist nicht nur sauber, sondern noetig. Die eigene Beobachtung endet
    am Ausstieg, weshalb jede Methode systematisch schlechter aussieht als
    die Kalibrierung erwarten laesst (im Backtest 37 % gegen 45 %). Ohne
    Bereinigung wanderten alle Gewichte gemeinsam nach unten — sichtbar im
    Protokoll, wirkungslos im Ergebnis, und irrefuehrend fuer jeden, der es
    liest.
    """
    current = weights["target_method_weights"]
    stats = method_hit_rates(trades, cal)
    damp = _damping(len(trades))

    brauchbar = {k: s for k, s in stats.items()
                 if k in current and s["n"] >= config.LEARN_MIN_TRADES
                 and s.get("t") is not None}
    if len(brauchbar) < 2:
        return []
    signale = {k: _evidenz(s["t"]) for k, s in brauchbar.items()}
    mittel = sum(signale.values()) / len(signale)

    log_lines = []
    for key, s in brauchbar.items():
        w = current[key]
        relativ = signale[key] - mittel
        factor = 1 + config.LEARN_RATE * relativ * damp
        neu = _clip(w * factor, config.WEIGHT_MIN, config.WEIGHT_MAX)
        if abs(neu - w) > 1e-4:
            current[key] = round(neu, 4)
            richtung = "belohnt" if neu > w else "bestraft"
            log_lines.append(
                f"Kursziel-Methode «{config.TARGET_METHOD_LABELS.get(key, key)}» "
                f"{richtung}: Ziel in "
                f"{(s['hit_rate']) * 100:.0f} % der {s['n']} Trades erreichbar, "
                f"gemessene Erwartung {(s['erwartet_rate']) * 100:.0f} % "
                f"(t = {s['t']:+.2f}, relativ {relativ:+.2f}) → "
                f"Gewicht {w:.3f} auf {neu:.3f}")
    return log_lines


# -- 2. Score-Komponenten --------------------------------------------------

def component_edges(trades: list[dict]) -> dict[str, dict]:
    """Trennt eine Komponente Gewinner von Verlierern?

    Die Trades werden je Komponente am Median geteilt; verglichen wird das
    mittlere R der oberen gegen die untere Haelfte. Das ist robuster als
    eine Korrelation, die bei 100 Punkten stark rauscht.
    """
    edges: dict[str, dict] = {}
    keys = set()
    for t in trades:
        keys.update((t.get("score_components") or {}).keys())

    for key in keys:
        paare = [(c[key], t["r_multiple"])
                 for t in trades
                 if (c := t.get("score_components")) and c.get(key) is not None
                 and t.get("r_multiple") is not None]
        if len(paare) < config.LEARN_MIN_TRADES:
            continue
        # Nach RANG teilen, nicht am Wert: eine Komponente, die bei vielen
        # Titeln denselben Wert annimmt (etwa gedeckelt bei 1.0), waere sonst
        # stillschweigend aus der Lernschleife gefallen — genau das passierte
        # `volumen` im Backtest vom 21.08.2026.
        paare.sort(key=lambda pr: pr[0])
        h = len(paare) // 2
        if h < 3:
            continue
        unten = [r for _, r in paare[:h]]
        oben = [r for _, r in paare[-h:]]
        grenze = paare[h][0]
        gebunden = sum(1 for v, _ in paare if v == grenze) / len(paare)
        if gebunden > 0.8:
            # Fast alles gleich — die Komponente kann nicht trennen. Das ist
            # eine Aussage, kein Grund zum Wegsehen.
            edges[key] = {"n": len(paare), "edge_r": 0.0, "gebunden": True,
                          "mean_r_oben": 0.0, "mean_r_unten": 0.0}
            continue
        edge = statistics.mean(oben) - statistics.mean(unten)
        t = _mittelwert_t(oben, unten)
        edges[key] = {
            "n": len(paare), "edge_r": round(edge, 4), "gebunden": False,
            "mean_r_oben": round(statistics.mean(oben), 3),
            "mean_r_unten": round(statistics.mean(unten), 3),
            "t": t,
        }
    return edges


def update_score_weights(weights: dict, trades: list[dict]) -> list[str]:
    """Komponenten mit Trennschaerfe hoeher gewichten, andere niedriger.

    Protokolliert wird der FAKTOR der Lernregel, nicht das Gewicht danach.
    Der Unterschied ist wichtig: nach jedem Schritt wird auf Summe 1
    normiert, wodurch sich auch Gewichte veraendern, an denen die Regel gar
    nichts getan hat. Wer das Protokoll aus den Endwerten baut, behauptet
    Belohnungen, die nie stattgefunden haben.
    """
    current = weights["score_weights"]
    edges = component_edges(trades)
    if not edges:
        return []
    damp = _damping(len(trades))
    log_lines = []

    for key, w in list(current.items()):
        e = edges.get(key)
        if not e or e.get("gebunden"):
            continue
        faktor = 1 + config.LEARN_RATE * _evidenz(e.get("t")) * damp
        neu_w = _clip(w * faktor, SCORE_WEIGHT_MIN, SCORE_WEIGHT_MAX)
        current[key] = neu_w
        if abs(faktor - 1) > 0.001:
            richtung = "belohnt" if faktor > 1 else "bestraft"
            log_lines.append(
                f"Komponente «{config.SCORE_LABELS.get(key, key)}» "
                f"{richtung}: obere Hälfte "
                f"{e['mean_r_oben']:+.2f} R gegen untere "
                f"{e['mean_r_unten']:+.2f} R aus {e['n']} Trades "
                f"(t = {e['t']:+.2f}) → Gewicht {w:.3f} auf {neu_w:.3f}")

    total = sum(current.values())
    if total > 0:
        for key in current:
            current[key] = round(current[key] / total, 4)
    return log_lines


# -- 3. Zielweite je Branche -----------------------------------------------

def update_sector_k_mult(weights: dict, trades: list[dict]) -> list[str]:
    """Sitzt das Ziel je Branche zu nah oder zu weit?

    Verglichen wird die tatsaechliche Trefferquote mit der BASISQUOTE, die
    beim Einstieg fuer genau diese Marken gemessen war. Das ist der springende
    Punkt: die Basisquote ist die Erwartung fuer eine Zufallsauswahl. Trifft
    die Analyse haeufiger, taugt die Auswahl etwas — dann darf das Ziel weiter
    hinaus, um mehr von der Bewegung mitzunehmen. Trifft sie seltener, wird
    das Ziel naeher gesetzt.

    Warum nicht wie zuvor aus der groessten guenstigen Bewegung (MFE)? Weil
    die vom Stop abgeschnitten wird: wer an Tag 2 ausgestoppt wird, hat
    definitionsgemaess eine winzige MFE. Der so gelernte Faktor waere
    systematisch zu klein und wuerde sich selbst weiter verkleinern.

    Der Multiplikator bleibt in [0.7, 1.4] und bewegt sich je Runde hoechstens
    ein Drittel des Wegs.
    """
    nach_sektor: dict[str, list[tuple[bool, float]]] = {}
    for t in trades:
        basis = t.get("basis_p_ziel")
        grund = t.get("exit_reason") or ""
        if basis is None or not grund:
            continue
        nach_sektor.setdefault(t.get("sector") or "Unbekannt", []).append(
            (grund.startswith("ziel"), float(basis)))

    log_lines = []
    for sektor, werte in nach_sektor.items():
        if len(werte) < MIN_TRADES_PER_BUCKET:
            continue
        treffer = sum(1 for tr, _ in werte if tr)
        ist = treffer / len(werte)
        soll = statistics.mean(b for _, b in werte)
        t = _anteil_t(treffer, len(werte), soll)
        if t is None:
            continue
        ziel_mult = _clip(1 + 0.4 * _evidenz(t),
                          config.SECTOR_K_MULT_MIN, config.SECTOR_K_MULT_MAX)
        alt = weights["sector_k_mult"].get(sektor, 1.0)
        schritt = (ziel_mult - alt) / 3 * _damping(len(werte))
        neu = round(_clip(alt + schritt, config.SECTOR_K_MULT_MIN,
                          config.SECTOR_K_MULT_MAX), 3)
        if abs(neu - alt) > 0.005:
            weights["sector_k_mult"][sektor] = neu
            log_lines.append(
                f"Zielweite {sektor}: Ziel in {(ist) * 100:.0f} % der {len(werte)} Trades "
                f"erreicht, Basisquote {(soll) * 100:.0f} % (t = {t:+.2f}) → "
                f"Multiplikator von {alt:.2f} auf {neu:.2f}")
    return log_lines


# -- 4. Multiplikatoren ----------------------------------------------------

def _bucket_multipliers(trades: list[dict], key_fn,
                        aktuell: dict) -> tuple[dict, list[str]]:
    """Branchen und Marktphasen gegen den REST vergleichen, nicht gegen sich selbst.

    Ein Eimer ist Teil des Gesamtschnitts; ihn dagegen zu messen verwaessert
    jeden Unterschied. Verglichen wird deshalb mit allem, was nicht in
    diesem Eimer liegt — und nur so weit, wie die Evidenz reicht.
    """
    paare = [(key_fn(t), t["r_multiple"]) for t in trades
             if t.get("r_multiple") is not None]
    alle_r = [r for _, r in paare]
    if len(alle_r) < config.LEARN_MIN_TRADES:
        return aktuell, []
    gesamt_mittel = statistics.mean(alle_r)

    buckets: dict[str, list[float]] = {}
    for k, r in paare:
        if k:
            buckets.setdefault(k, []).append(r)

    log_lines = []
    for name, werte in buckets.items():
        if len(werte) < MIN_TRADES_PER_BUCKET:
            continue
        rest = [r for k, r in paare if k != name]
        t = _mittelwert_t(werte, rest)
        if t is None:
            continue
        mittel = statistics.mean(werte)
        ziel = _clip(1 + 0.5 * _evidenz(t), config.MULT_MIN, config.MULT_MAX)
        alt = aktuell.get(name, 1.0)
        neu = round(_clip(alt + (ziel - alt) / 3, config.MULT_MIN,
                          config.MULT_MAX), 3)
        if abs(neu - alt) > 0.01:
            aktuell[name] = neu
            richtung = "belohnt" if neu > alt else "bestraft"
            log_lines.append(
                f"«{name}» {richtung}: mittleres R {mittel:+.2f} gegen "
                f"Gesamtschnitt {gesamt_mittel:+.2f} aus {len(werte)} Trades "
                f"(t = {t:+.2f}) → Multiplikator {alt:.2f} auf {neu:.2f}")
    return aktuell, log_lines


def update_multipliers(weights: dict, trades: list[dict]) -> list[str]:
    lines = []
    weights["sector_multiplier"], l1 = _bucket_multipliers(
        trades, lambda t: t.get("sector"), weights["sector_multiplier"])
    lines += [f"Branche {x}" for x in l1]

    def regime_key(t):
        r = t.get("regime_at_entry") or {}
        trend, vix = r.get("trend"), r.get("vix_level")
        return f"{trend}/{vix}" if trend and vix else None

    weights["regime_multiplier"], l2 = _bucket_multipliers(
        trades, regime_key, weights["regime_multiplier"])
    lines += [f"Regime {x}" for x in l2]
    return lines


# -- Hauptfunktion ---------------------------------------------------------

def update(weights: dict, all_trades: list[dict],
           today: Optional[dt.date] = None,
           cal: Optional[dict] = None) -> dict:
    """Alle Gewichte aus den abgeschlossenen Trades neu bestimmen.

    Gibt die aktualisierten Gewichte zurueck; das Protokoll steht in
    weights['history'].
    """
    today = today or dt.date.today()
    trades = ki_trades(all_trades)
    n = len(trades)

    if n < config.LEARN_MIN_TRADES:
        log.info("Lernschleife wartet: %d von %d noetigen Trades",
                 n, config.LEARN_MIN_TRADES)
        weights["trades_seen"] = n
        return weights

    # Nur das rollende Fenster — alte Marktphasen sollen nicht ewig nachwirken.
    fenster = sorted(trades, key=lambda t: t.get("exit_date") or "")[-config.LEARN_WINDOW:]

    lines: list[str] = []
    lines += update_method_weights(weights, fenster, cal)
    lines += update_score_weights(weights, fenster)
    lines += update_sector_k_mult(weights, fenster)
    lines += update_multipliers(weights, fenster)

    weights["trades_seen"] = n
    weights["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    if lines:
        # Der Stand NACH diesem Schritt wandert mit ins Protokoll. Nur so
        # entsteht eine Kurve statt einer Liste von Behauptungen: wer bloss
        # die Aenderungszeilen speichert, kann spaeter nicht mehr zeigen, wo
        # ein Gewicht zu welchem Zeitpunkt stand.
        eintrag = {
            "date": today.isoformat(),
            "trades_total": n,
            "window": len(fenster),
            "damping": round(_damping(len(fenster)), 3),
            "mean_r": round(statistics.mean(
                [t["r_multiple"] for t in fenster
                 if t.get("r_multiple") is not None] or [0]), 3),
            "score_weights": {k: round(v, 4)
                              for k, v in weights["score_weights"].items()},
            "target_method_weights": {
                k: round(v, 4)
                for k, v in weights["target_method_weights"].items()},
            "changes": lines,
        }
        weights["history"].append(eintrag)
        weights["history"] = weights["history"][-200:]
        for line in lines:
            log.info("Lernschleife: %s", line)
    else:
        log.info("Lernschleife: keine Aenderung (%d Trades im Fenster)",
                 len(fenster))
    return weights


def effective_score(base_score: float, sector: str, regime: dict,
                    weights: dict) -> tuple[float, list[str]]:
    """Score mit den gelernten Multiplikatoren, auf 0..1 begrenzt."""
    notes = []
    faktor = 1.0
    sm = weights.get("sector_multiplier", {}).get(sector)
    if sm:
        faktor *= sm
        notes.append(f"Branchen-Multiplikator {sector}: {sm:.2f}")
    key = f"{(regime or {}).get('trend')}/{(regime or {}).get('vix_level')}"
    rm = weights.get("regime_multiplier", {}).get(key)
    if rm:
        faktor *= rm
        notes.append(f"Regime-Multiplikator {key}: {rm:.2f}")
    return _clip(base_score * faktor, 0.0, 1.0), notes
