# Aktien-Analyse

Tägliche Analyse des US-Aktienmarkts über alle elf Branchen, mit
nachvollziehbar berechneten Kurszielen, zwei virtuellen Vergleichsdepots und
einer Lernschleife, die aus den eigenen Ergebnissen Gewichte anpasst.

Alles läuft auf Gratis-Diensten: Kursdaten von Alpaca (IEX-Feed),
Fundamentaldaten von Yahoo, Nachrichten aus vier RSS-Feeds, Sprachmodell
lokal über Ollama, Betrieb über GitHub Actions, Push über ntfy.sh.

**Simulation zu Lernzwecken, keine Anlageberatung. Kein echtes Geld ist im
Spiel.**

---

## Schnellstart

```powershell
pip install -r requirements.txt

python -m unittest discover -s tests      # 225 Tests
python scripts/screen.py                  # Tagesanalyse ansehen
python scripts/kalibrieren.py --gegenprobe # Messlatte neu vermessen
python scripts/backtest.py --tage 250     # Ehrlichkeitstest
python scripts/trennschaerfe.py           # hat der Score Vorhersagekraft?
```

Die Alpaca-Schlüssel werden lokal aus `Desktop\Trading Bot\.alpaca_credentials`
gelesen, in der Cloud aus GitHub Secrets. Sie stehen nirgends im Code.

---

## Aufbau

```
Universum (532 Titel)          universe.py   S&P 500 + liquideste Nicht-Index-Werte
    │                                        ETFs über Yahoo-Sektor aussortiert
    ▼
Tagesbars, Indikatoren         alpaca.py, indicators.py
    │                                        EMA, RSI, ATR, ADX, Bollinger, Donchian
    ▼
Harte Ausschlüsse              scoring.py    Kurs < 5 USD, Volumen, Datenlücken
    ▼
Vorbewertung (nur Kurs)        scoring.py    schnell, ohne Netz
    ▼
Fundamentaldaten               yahoo.py      wöchentlich, ein Speicher im Repo
    ▼
Vollbewertung, 7 Komponenten   scoring.py    + Branchenmediane
    ▼
Sprachmodell auf die Top 25    llm.py        Sentiment, These, Katalysatoren
    ▼
Kursziele + Basisquote         calibration.py, targets.py
    ▼
Ideen  →  zwei Depots          portfolio.py  KI gegen Zufall, gleiche Regeln
    ▼
Belohnung / Bestrafung         learning.py   R-Multiple → Gewichte
```

Zwei Läufe täglich auf GitHub Actions, dazwischen liegt alles versioniert im
Repo:

| Lauf | UTC | Schweiz | Was passiert |
|---|---|---|---|
| **Vorbörse** | 12:15 | 14:15 | analysieren, 3 Käufe je Depot vormerken, Push senden |
| **Abrechnung** | 22:00 | 00:00 | füllen, Ausstiege, lernen |
| **Kalibrierung** | Sa 06:30 | Sa 08:30 | die Messlatte nachführen |

---

## Die Messlatte: was Kurse wirklich tun

Der wichtigste Baustein ist keine Formel, sondern eine Messung.
`scripts/kalibrieren.py` misst über das **gesamte** Universum, wie weit Kurse
binnen 15 Handelstagen tatsächlich laufen — in ATR-Einheiten, damit ruhige und
wilde Aktien vergleichbar sind.

**Gemessen am 21.08.2026 über 212'871 Beobachtungen aus 532 Titeln:**

| | Median | 75. Perzentil | 90. Perzentil |
|---|---|---|---|
| aufwärts | **2.02 ATR** | 3.47 | 5.19 |
| abwärts | **1.65 ATR** | 2.99 | 4.72 |

Daraus folgen Ziel und Stop — je Branche, aus dem Median der jeweiligen
Richtung. Kein geschätzter Faktor mehr.

### Berührungsquote ist nicht Trefferquote

Zwei Zahlen, die ständig verwechselt werden:

- **Berührungsquote** — wie oft wird ein Niveau überhaupt erreicht. Ziel *und*
  Stop können beide in der Hälfte der Fälle berührt werden; die Summe ist
  nicht 1.
- **Erstpassage** — was kam *zuerst*. Nur das entscheidet über den Ausgang.

Dafür misst `calibration.py` ein Gitter: für jede Kombination aus Ziel- und
Stop-Abstand die gemessene Häufigkeit von Ziel, Stop und Zeitablauf. Bei den
kalibrierten Marken:

```
Ziel zuerst 43.0 %   Stop zuerst 45.2 %   Zeitablauf 11.7 %
Erwartungswert: +0.098 R
```

**Das ist die Basisquote — was eine zufällige Auswahl unter denselben Regeln
erreicht.** Sie steht auf jeder Karte. Alles darüber ist echter
Auswahlvorteil, alles darunter ist schlechter als Würfeln.

Das Gitter wird gegengerechnet: `--gegenprobe` läuft jeden Tag ein zweites
Mal einzeln nach, inklusive Eröffnungslücken. Grösste Abweichung über alle
Branchen: **0.5 Prozentpunkte**.

> Diese Basisquote ist zeitraumabhängig. Sie stammt aus einem Jahr mit
> SPY +20 %; in einem fallenden Markt wäre sie niedriger. Genau deshalb wird
> sie wöchentlich neu gemessen und nicht als Konstante behandelt.

---

## Was beim Bauen gemessen wurde

Diese Zahlen stammen aus echten Läufen, nicht aus Annahmen. Sie haben den
Entwurf an sieben Stellen korrigiert.

### 1. Analystenziele dürfen kein gemitteltes Kursziel sein

Der ursprüngliche Entwurf mittelte vier Kursziel-Methoden. Ein
12-Monats-Analystenziel, mit 15/252 auf den Horizont skaliert, liegt fast
immer auf Kursniveau und zieht jeden Mittelwert zum Kurs.

**Gemessen an Apple:** der naive Mittelwert der vier Niveaus lag **unter dem
aktuellen Kurs**. Analysten und Bewertung wirken jetzt als *Neigung*, nicht
als gemitteltes Niveau.

Festgehalten in `tests/test_targets.py::test_analysten_ziehen_ziel_nicht_zum_kurs`.

### 2. Die Liquiditätsschwelle war 7× zu hoch

Der Plan sah 20 Mio USD Tagesvolumen vor. Der Gratis-Tarif liefert aber nur
den IEX-Feed, rund 3–5 % des Gesamtmarkts.

**Gemessen:** 20 Mio hätte **46 % der S&P-500-Mitglieder** ausgeschlossen.
Bei 3 Mio bleiben 498 von 501.

### 3. Die Positionsgrösse war rechnerisch unmöglich

3 Käufe täglich × bis zu 20 Handelstage Haltedauer = bis zu 60 gleichzeitige
Positionen. Bei den geplanten 5 % je Position wären das **300 % des Depots**.
Korrigiert auf 2 % mit Deckel bei 40 Positionen; im Backtest gemessene
Belegung: Median 17, Maximum 32.

### 4. Die Kursziele waren unerreichbar weit

| Ziel | wird binnen 15 Handelstagen berührt |
|---|---|
| 1.5 ATR | 62 % |
| 2.0 ATR | 50 % |
| 3.0 ATR | 32 % |
| 5.5 ATR | 8 % |

Die Strukturmethode war pauschal bei 6 ATR gedeckelt — ein Niveau, das in
**6 %** der Fälle erreicht wird. Der Deckel kommt jetzt aus der Messung: das
Niveau, das historisch noch in 30 % der Fälle erreicht wurde (branchenweise
2.97 bis 3.41 ATR).

### 5. Eine Neigung am Kursniveau trifft ruhige Aktien viel härter

Die Neigungen verschoben das *Kursniveau* um wenige Prozent. Bei Apple sind
3 % rund 0.4 ATR — bei einer ruhigen Aktie wie XOM aber **1.5 ATR**.

**Gemessen am 21.08.2026:** XOM sprang durch die Bewertungsneigung von 2.75
auf 4.33 ATR, die Trefferwahrscheinlichkeit fiel von 39 % auf **18 %**. Die
Neigungen wirken jetzt auf den *Abstand*, nicht auf das Niveau — dieselbe
Neigung hat damit bei jeder Aktie dieselbe Wirkung in ATR.

Festgehalten in `test_targets.py::test_neigung_wirkt_in_atr_einheiten_gleich`.

### 6. Die Depots kauften unterschiedlich viel

Das KI-Depot hält dieselben Titel tagelang oben — ein Kauf wurde deshalb oft
verworfen, weil die Position schon offen war. **Gemessen: 588 gegen 685
Trades.** Ein Vergleich der Depotrenditen war damit ein Vergleich des
eingesetzten Kapitals, nicht der Auswahl.

Belegte Titel werden jetzt *übersprungen* statt verworfen: die Auswahl rückt
zum nächsten Kandidaten nach. Danach: **726 gegen 721 Trades.**

### 7. Die Lernschleife lernte Rauschen

Der schwerwiegendste Fund. Drei Fehler in einer Regel:

**a) Sie reagierte auf die Grösse eines Unterschieds statt auf seine
Sicherheit.** Über 130'535 Beobachtungen ist die Komponente `trend` der klar
beste Trenner (+1.25 Prozentpunkte). Im 100-Trade-Fenster der Lernschleife
erschien dieselbe Komponente als schädlich und wurde bestraft. Bei 100 Trades
beträgt der Standardfehler eines solchen Vorsprungs rund 0.26 R — wer auf
Unterschiede dieser Grössenordnung reagiert, lernt Rauschen.
**Gemessen: die Rendite fiel dadurch von +5.45 % auf +2.97 %.**

Der Schritt richtet sich jetzt nach dem **t-Wert**: bei |t| = 1 halber
Ausschlag, ab |t| = 2 voller. In den ersten Monaten passiert damit fast
nichts — richtig so, vorher gibt es nichts zu wissen.

**b) Die Kursziel-Methoden wurden gegen 50 % gemessen.** Seit Ziel und Stop
kalibriert sind, liegt die Trefferquote bauartbedingt bei rund 40 %. Beide
Methoden wurden deshalb in *jeder* Runde bestraft — eine Einbahnstrasse nach
unten statt einer Lernschleife. Verglichen wird jetzt gegen die gemessene
Erstpassage beim tatsächlichen Stop, und die Methoden werden gegeneinander
gewichtet: eine Verzerrung, die alle gleich trifft, fällt heraus.

**c) Die Zielweite wurde aus der eigenen grössten günstigen Bewegung
gelernt.** Die ist vom Stop abgeschnitten — wer an Tag 2 ausgestoppt wird,
hat definitionsgemäss eine winzige Bewegung. Der so gelernte Faktor
schrumpfte sich selbst. Gelernt wird jetzt aus dem Vergleich der
Trefferquote mit der Basisquote, und nur als begrenzter Multiplikator
[0.7, 1.4] auf den kalibrierten Faktor. **Der Stop wird gar nicht gelernt.**

---

## Ergebnisse des Backtests

250 Handelstage (25.08.2025 – 21.08.2026), nur technische Komponenten,
Marken je Branche kalibriert:

|  | Depot KI | Depot Zufall | SPY |
|---|---|---|---|
| Rendite ohne Lernschleife | +5.40 % | +2.98 % | +20.57 % |
| **Rendite mit Lernschleife** | **+5.64 %** | **+0.47 %** | +20.58 % |
| Erwartungswert je Trade (mit) | **+0.104 R** | −0.011 R | — |
| Trefferquote | 47.0 % | 41.3 % | — |
| Trades | 726 | 715 | — |
| Max. Rückgang | −5.0 % | −5.1 % | −8.9 % |

Gegen die eigentliche Messlatte, die gemessene Basisquote:

| | Ziel zuerst | gegen Basisquote | Erwartung R | gegen Basisquote |
|---|---|---|---|---|
| Depot KI | 46.3 % | **+4.7 pp** | +0.104 | **−0.007** |
| Depot Zufall | 37.1 % | +0.6 pp | −0.011 | −0.125 |

Das KI-Depot trifft sein Ziel deutlich häufiger als eine Zufallsauswahl und
liegt beim Erwartungswert praktisch auf der Basisquote — das Zufallsdepot
bleibt klar darunter. Der Rückstand von 0.007 R gegenüber der Basisquote ist
erklärbar: die Basisquote rechnet mit Ausstiegen exakt auf der Marke, das
Depot mit Eröffnungslücken und 5 Basispunkten Schlupf je Seite.

**Beide Depots bleiben deutlich hinter SPY.** In einem Jahr mit +20 % im
Index ist Kaufen-und-Halten schwer zu schlagen — erst recht mit einer
Strategie, die im Schnitt nur zu einem Drittel investiert ist.

### Warum die Rendite die schlechtere Kennzahl ist

Das KI-Depot hält im Schnitt 6.8 Tage, das Zufallsdepot 7.8 — weil die
Analyse ihr Ziel schneller erreicht. In einem steigenden Markt ist kürzeres
Halten ein Nachteil für die Prozentrendite, obwohl jeder einzelne Trade
besser ist. Für die Frage „taugt die Auswahl?" ist deshalb der
**Erwartungswert je Trade** die ehrlichere Zahl.

### Und ist der Unterschied belastbar?

`scripts/signifikanz.py`, mit Lernschleife:

```
Unterschied KI minus Zufall: +0.116 R
Standardfehler:              0.069 R
t-Wert:                      +1.68        NICHT belegt (Schwelle 1.96)
Nötig: rund 974 Trades je Depot ≈ 15 Monate Livebetrieb.
```

Nahe dran, aber nicht belegt. Das ist die ehrliche Auskunft.

> **Einschränkung des Vergleichs:** das Zufallsdepot zieht aus demselben Topf
> handelbarer Titel wie die Analyse. Ändert die Lernschleife die Kursziele,
> ändert sich auch dieser Topf — die Zeile „ohne Lernschleife" und die Zeile
> „mit Lernschleife" haben deshalb nicht exakt dieselbe Kontrollgruppe.
> Aussagekräftig ist der Vergleich *innerhalb* einer Zeile.

---

## Warum drei Trades pro Tag zu wenig sind

Deshalb gibt es `scripts/trennschaerfe.py`. Es wertet **alle** Kandidaten
jedes Tages aus statt nur der drei gekauften — rund 520 statt 3, also über
das Hundertfache an Beobachtungen. Nach Fama-MacBeth: je Tag ein Wert
(bestes Fünftel minus schlechtestes), Standardfehler wegen überlappender
15-Tage-Fenster mit √15 vergrössert.

**Ergebnis über 250 Tage und 130'535 Beobachtungen** (nur technisch):

| Score-Fünftel | mittlere Rendite über 15 Handelstage |
|---|---|
| schlechtestes | +1.112 % |
| 2. | +1.260 % |
| 3. | +1.581 % |
| 4. | +1.479 % |
| **bestes** | **+1.899 %** |

Die Reihenfolge stimmt fast durchgehend — höherer Score, höhere Rendite. Der
Abstand zwischen bestem und schlechtestem Fünftel beträgt **+0.80
Prozentpunkte** über 15 Handelstage.

**Aber t = +0.79 — nicht belegt.** Die Tagesstreuung liegt bei 4.14
Prozentpunkten; um einen Abstand von 0.8 Punkten nachzuweisen, bräuchte es
rund hundert überlappungsfreie 15-Tage-Fenster, also etwa vier Jahre.

Je Komponente, über dieselben 130'535 Beobachtungen:

| Komponente | Spread bestes minus schlechtestes Fünftel | t |
|---|---|---|
| `trend` | **+1.249 %** | +0.98 |
| `volumen` | −0.020 % | −0.04 |
| `setup` | −0.596 % | −0.87 |

`trend` trägt, `volumen` tut nichts, `setup` schadet eher — keines davon
belegt, aber alle drei über hunderttausend Beobachtungen gemessen statt über
siebenhundert Trades. Diese Messung ist das eigentliche statistische
Instrument; die zwei Depots sind das anschauliche Spiel.

Das ist die ehrliche Lage bei Aktienprognosen: Signale sind schwach, Rauschen
ist gross. Ein Muster, das plausibel und in der richtigen Reihenfolge ist,
aber nach einem Jahr noch nicht belegt.

---

## Ehrliche Grenzen

- **Fundamentaldaten sind aktuell, nicht historisch.** Yahoo liefert die
  Analystenziele von heute. Im Rückblick verwendet wären sie ein Blick in die
  Zukunft. Der Backtest läuft deshalb standardmässig nur technisch —
  `--mit-fundamentals` existiert, um den Unterschied zu zeigen, und ist
  verzerrt.
- **Überlebensverzerrung:** Das Universum sind die heutigen Indexmitglieder.
  Der Livebetrieb ab Tag 1 ist davon frei.
- **Tagesbars lösen die Reihenfolge nicht auf.** Werden Ziel und Stop am
  selben Tag berührt, zählt der Stop. Das macht die Statistik eher zu
  pessimistisch — die richtige Richtung.
- **Kursbewegungen sind fast ein Martingal.** Ohne Auswahlvorteil ist die
  Erwartung jeder Stop-Ziel-Kombination praktisch null. Kein
  Chance-Risiko-Verhältnis erzeugt Rendite; nur die Auswahl kann das.
- **Die Filter filtern kaum.** Weil Ziel und Stop aus der Messung kommen,
  bestehen rund 500 von 520 bewerteten Titeln die Handelbarkeitsprüfung. Das
  ist beabsichtigt: die Auswahl trifft der Score, nicht ein willkürliches
  Chance-Risiko-Tor. Die Tore fangen nur noch echte Ausreisser ab.
- **Die Trefferquote der Kursziel-Methoden ist nicht sauber messbar.** Die
  eigene Beobachtung endet am Ausstieg — eine Methode, deren Niveau über dem
  Gesamtziel liegt, kann nach einem Zielausstieg nicht mehr recht bekommen.
  Deshalb werden die Methoden nur *gegeneinander* gewichtet.
- **Simulation, keine Anlageberatung.** Kein echtes Geld ist im Spiel.

---

## Betrieb in der Cloud

### Was Fabian einmalig einrichten muss

1. **GitHub-Konto** anlegen (gratis, keine Kreditkarte), Repo `aktien-analyse`
   **öffentlich** anlegen — nur dort sind die Actions-Minuten unbegrenzt.
2. `winget install GitHub.cli`, danach im Chat `! gh auth login`.
3. **ntfy-App** installieren und das Benachrichtigungs-Thema abonnieren.
4. Unter *Settings → Secrets and variables → Actions* eintragen:

   | Art | Name | Inhalt |
   |---|---|---|
   | Secret | `ALPACA_KEY` | aus `.alpaca_credentials` |
   | Secret | `ALPACA_SECRET` | aus `.alpaca_credentials` |
   | Secret | `NTFY_TOPIC` | das Thema fürs Handy — **geheim** |
   | Variable | `NTFY_CONTROL_TOPIC` | das Steuer-Thema — steht auf der Seite |
   | Variable | `OLLAMA_MODEL` | optional, sonst `qwen2.5:7b-instruct-q4_K_M` |

5. *Settings → Pages* auf `main` / `/docs` stellen.
6. **Zuerst den Workflow „Machbarkeit" von Hand starten.** Er misst Ollama auf
   dem Runner und entscheidet über die Modellgrösse — auf Messdaten, nicht auf
   Schätzung. Erst wenn er durchläuft, werden die beiden Zeitpläne scharf
   geschaltet.

### Der Aus-Schalter

Die Seite sendet `PAUSE` oder `RESUME` an das Steuer-Thema. Jeder Lauf fragt
das Thema als **allererstes** ab und bricht bei `paused` sofort ab. Der
Zustand liegt in `docs/data/control.json` — im Repo, nicht in ntfys
12-Stunden-Cache. Die Pause hält deshalb beliebig lange.

Harter Not-Aus: Workflow in der GitHub-Oberfläche deaktivieren, ein Klick,
auch vom Handy.

*Bekannte Einschränkung:* das Steuer-Thema steht im Quelltext der
öffentlichen Seite. Wer die Adresse kennt, kann die Simulation pausieren. Der
Schaden wäre eine ausgefallene Analyse — Benachrichtigungen und
Alpaca-Schlüssel sind davon getrennt.

### Wenn etwas ausfällt

| Ausfall | Folge |
|---|---|
| Ollama | kein Sentiment, keine These; die Push zeigt stattdessen die rohen Schlagzeilen |
| Yahoo | keine Fundamentaldaten; Gewichte werden neu normiert, die Karte weist es aus |
| ein RSS-Feed | die anderen drei liefern weiter |
| ntfy | keine Push; Analyse und Depots sind zu dem Zeitpunkt schon geschrieben |
| `calibration.json` | Rückfallwerte aus `config.py`, Karten ohne Wahrscheinlichkeiten |
| **Alpaca-Bars** | **Abbruch** — ohne Kurse gibt es nichts zu rechnen |
| ein ganzer Lauf | `Abrechnung` von Hand mit `--tag JJJJ-MM-TT` nachholen |

---

## Dateien

| Datei | Zweck |
|---|---|
| `src/config.py` | alle Stellschrauben, keine Geheimnisse |
| `src/net.py` | HTTP mit Wiederholversuchen, schneidet Schlüssel aus Logs |
| `src/alpaca.py` | Uhr, Kalender, Wertpapiere, Bars, Nachrichten |
| `src/yahoo.py` | Crumb-Handshake, Fundamentaldaten, ein Speicher |
| `src/universe.py` | S&P 500 + liquide Zugänge, Branchen |
| `src/indicators.py` | Indikatoren + Vorberechnung für den Backtest |
| `src/calibration.py` | **die Messlatte**: wie weit Kurse laufen, Basisquoten |
| `src/targets.py` | zwei Niveau-Methoden, zwei Neigungen, Wahrscheinlichkeiten |
| `src/scoring.py` | sieben Komponenten, Ausschlüsse, Abzüge |
| `src/llm.py` | Ollama, striktes JSON, Ausfall unschädlich |
| `src/news.py` | Alpaca-News + vier RSS-Feeds |
| `src/portfolio.py` | beide Depots, Füllen, Ausstiege, Schlupf |
| `src/learning.py` | R-Multiple → Gewichte, Schritt nach Evidenz |
| `src/notify.py` | ntfy-Push und Aus-Schalter |
| `src/analysis.py` | die Pipeline |
| `src/run_premarket.py` | Lauf A — Vorbörse |
| `src/run_settle.py` | Lauf B — Abrechnung |
| `scripts/machbarkeit.py` | misst Ollama und die Datenquellen auf dem Runner |
| `scripts/kalibrieren.py` | misst die Messlatte, mit Gegenprobe |
| `scripts/screen.py` | Tagesanalyse ansehen |
| `scripts/backtest.py` | Ehrlichkeitstest über 250 Tage |
| `scripts/signifikanz.py` | ist der Unterschied belastbar? |
| `scripts/trennschaerfe.py` | hat der Score Vorhersagekraft? |
| `tests/test_ablauf.py` | der ganze Weg an künstlichen Kursen |

Alles, was das System weiss, liegt unter `docs/data/` — versioniert im
Git-Verlauf und gleichzeitig das, was die Seite ausliest. Ein Zustand, der
nicht im Verlauf steht, ist später nicht mehr nachvollziehbar.

---

## Was noch fehlt

Die Seite unter `docs/`. Das visuelle Design entsteht mit Claude Design;
danach wird die PWA gebaut und GitHub Pages aktiviert. Die Daten dafür
schreiben die beiden Läufe bereits vollständig:
`latest.json`, `news.json`, `equity.json`, `trades.json`, `weights.json`,
`portfolio_ki.json`, `portfolio_zufall.json`, `calibration.json`,
`status.json`, `control.json` und ein Tagesarchiv unter `archive/`.
