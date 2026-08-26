/* Aktien-Analyse — die Seite.
 *
 * Kein Backend, kein Framework, keine Abhaengigkeit: die Seite liest die
 * JSON-Dateien, die die beiden GitHub-Actions-Laeufe in dasselbe Repo
 * schreiben, und stellt sie dar. Faehlt eine Datei, fehlt der Abschnitt —
 * am ersten Tag existiert noch keine Handelshistorie, und das soll die
 * Seite sagen statt zu bruchlanden.
 *
 *   status.json    wann lief was, und mit welchem Ergebnis
 *   control.json   pausiert oder nicht
 *   latest.json    Analyse des Tages: Ideen mit voller Herleitung
 *   equity.json    Depotkurven und Kennzahlen (erst nach der ersten Abrechnung)
 *   weights.json   gelernte Gewichte und das Protokoll jedes Lernschritts
 *
 * Dieselben fuenf Dateien speisen die Fragezeile am Fuss der Seite.
 */
'use strict';

// Oeffentlich mit Absicht: ueber dieses Thema schaltet der Aus-Schalter.
// Das Benachrichtigungs-Thema ist ein anderes und steht nur in den
// GitHub Secrets — wer diese Adresse kennt, kann pausieren, aber nicht
// mitlesen.
const STEUER_THEMA = 'aktien-steuer-cjy706dczq1uxfij';

const DATEN = 'data/';

// ── Werkzeug ───────────────────────────────────────────────────────────────

const $ = (auswahl) => document.querySelector(auswahl);

function esc(wert) {
  if (wert === null || wert === undefined) return '';
  return String(wert).replace(/[&<>"']/g, (z) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[z]));
}

/** Schweizer Zahlensatz: Hochkomma als Tausendertrenner, Punkt als Komma.
 *  Dieselbe Schreibweise wie in den Rechenschritten aus dem Python-Teil. */
function zahl(wert, stellen = 2) {
  if (wert === null || wert === undefined || Number.isNaN(wert)) return '—';
  const fest = Number(wert).toFixed(stellen);
  const [ganz, rest] = fest.split('.');
  const vorzeichen = ganz.startsWith('-') ? '-' : '';
  const ziffern = vorzeichen ? ganz.slice(1) : ganz;
  const getrennt = ziffern.replace(/\B(?=(\d{3})+(?!\d))/g, "'");
  return vorzeichen + getrennt + (rest ? '.' + rest : '');
}

function prozent(wert, stellen = 1, vorzeichen = false) {
  if (wert === null || wert === undefined) return '—';
  const z = zahl(wert, stellen);
  return (vorzeichen && wert > 0 ? '+' : '') + z + ' %';
}

/** Anteil 0..1 als Prozent — so stehen die gemessenen Quoten in den Daten. */
function anteil(wert, stellen = 0) {
  if (wert === null || wert === undefined) return '—';
  return zahl(wert * 100, stellen) + ' %';
}

function rWert(wert) {
  if (wert === null || wert === undefined) return '—';
  return (wert > 0 ? '+' : '') + zahl(wert, 3) + ' R';
}

function datumKurz(iso) {
  if (!iso) return '';
  const t = String(iso).slice(0, 10).split('-');
  return t.length === 3 ? `${t[2]}.${t[1]}.` : String(iso);
}

function datumLang(iso) {
  if (!iso) return '';
  const t = String(iso).slice(0, 10).split('-');
  return t.length === 3 ? `${t[2]}.${t[1]}.${t[0]}` : String(iso);
}

function zeitpunkt(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const zwei = (n) => String(n).padStart(2, '0');
  return `${zwei(d.getDate())}.${zwei(d.getMonth() + 1)}. ${zwei(d.getHours())}:${zwei(d.getMinutes())}`;
}

/** Eine Datendatei holen. Fehlt sie, ist das kein Fehler, sondern ein
 *  Zustand: der Lauf, der sie schreibt, hat noch nicht stattgefunden. */
async function holen(name) {
  try {
    const antwort = await fetch(DATEN + name + '?v=' + Date.now(),
                               { cache: 'no-store' });
    if (!antwort.ok) return null;
    return await antwort.json();
  } catch (fehler) {
    return null;
  }
}

// ── Kopf: Zustand und Aus-Schalter ─────────────────────────────────────────

const LAUF_NAME = { vorboerse: 'Vorbörse', abrechnung: 'Abrechnung' };

function zeigeKopf(status, steuerung) {
  const pausiert = !!(steuerung && steuerung.paused);
  const punkt = $('#punkt');
  const schalter = $('#schalter');

  // Drei Zustaende, nicht zwei: bevor der erste Lauf stattgefunden hat, ist
  // "laeuft" ebenso falsch wie "pausiert" — das System hat schlicht noch
  // nichts getan.
  const gelaufen = !!(status && status.letzter_lauf);
  punkt.dataset.zustand = pausiert ? 'pausiert' : gelaufen ? 'laeuft' : 'unbekannt';
  $('#zustand-text').textContent = pausiert ? 'Pausiert'
    : gelaufen ? 'System läuft' : 'Noch nicht gestartet';

  let zeile = 'Der erste Lauf startet werktags um 14:15.';
  if (gelaufen) {
    const name = LAUF_NAME[status.lauf] || status.lauf || 'Lauf';
    zeile = `Letzter Lauf: ${name}, ${zeitpunkt(status.letzter_lauf)}`;
    if (status.ergebnis && status.ergebnis !== 'fertig') {
      zeile += ` (${status.ergebnis})`;
    }
  }
  // Ob das Sprachmodell gelaufen ist, steht seit jeher in status.json. Ohne
  // es kommen die Ideen ohne Nachrichten-These und ohne die Komponente
  // News-Sentiment zustande — das gehoert auf die Seite und nicht ins Log.
  const modell = status ? status.sprachmodell : undefined;
  if (gelaufen && modell === true) zeile += ' · Sprachmodell bereit';
  $('#lauf-text').textContent = zeile;

  const modellHinweis = $('#modell-hinweis');
  if (gelaufen && modell === false) {
    modellHinweis.hidden = false;
    modellHinweis.textContent = 'Das Sprachmodell war beim letzten Lauf nicht '
      + 'erreichbar. Die Ideen sind ohne Nachrichten-Einschätzung entstanden — '
      + 'ohne These auf der Karte und ohne die Komponente News-Sentiment in '
      + 'der Bewertung. Gerechnet wurde alles Übrige normal.';
  } else {
    modellHinweis.hidden = true;
  }

  schalter.hidden = false;
  schalter.setAttribute('aria-checked', pausiert ? 'false' : 'true');

  // Ein gesendeter Befehl wirkt erst beim naechsten Lauf — bis dahin zeigt
  // control.json noch den alten Zustand. Ohne diesen Hinweis waere der
  // Schalter scheinbar wirkungslos.
  const offen = gemerkterBefehl();
  const hinweis = $('#kopf-hinweis');
  if (offen && offen.pausiert !== pausiert) {
    hinweis.hidden = false;
    hinweis.textContent = offen.pausiert
      ? `Pause um ${zeitpunkt(offen.zeit)} angefordert — sie greift beim nächsten Lauf.`
      : `Fortsetzen um ${zeitpunkt(offen.zeit)} angefordert — greift beim nächsten Lauf.`;
  } else {
    if (offen) localStorage.removeItem('befehl');
    hinweis.hidden = true;
  }
}

function gemerkterBefehl() {
  try {
    const roh = localStorage.getItem('befehl');
    return roh ? JSON.parse(roh) : null;
  } catch (fehler) {
    return null;
  }
}

function schalterEinrichten(steuerung) {
  const schalter = $('#schalter');
  const kasten = $('#rueckfrage');
  const text = $('#rueckfrage-text');
  const ja = $('#bestaetigen');

  schalter.addEventListener('click', () => {
    const pausiertJetzt = schalter.getAttribute('aria-checked') === 'false';
    const willPausieren = !pausiertJetzt;
    text.textContent = willPausieren
      ? 'Analyse und Käufe pausieren?'
      : 'Analyse wieder aufnehmen?';
    ja.textContent = willPausieren ? 'Pausieren' : 'Fortsetzen';
    ja.classList.toggle('stark', willPausieren);
    ja.dataset.befehl = willPausieren ? 'PAUSE' : 'RESUME';
    kasten.hidden = false;
    ja.focus();
  });

  $('#abbrechen').addEventListener('click', () => { kasten.hidden = true; });

  ja.addEventListener('click', async () => {
    const befehl = ja.dataset.befehl;
    ja.disabled = true;
    const gesendet = await sendeBefehl(befehl);
    ja.disabled = false;
    kasten.hidden = true;
    const hinweis = $('#kopf-hinweis');
    hinweis.hidden = false;
    if (!gesendet) {
      hinweis.textContent = 'Befehl konnte nicht gesendet werden — keine Verbindung zu ntfy.sh.';
      return;
    }
    const zustand = { pausiert: befehl === 'PAUSE', zeit: new Date().toISOString() };
    try { localStorage.setItem('befehl', JSON.stringify(zustand)); } catch (f) { /* egal */ }
    $('#schalter').setAttribute('aria-checked', zustand.pausiert ? 'false' : 'true');
    $('#punkt').dataset.zustand = zustand.pausiert ? 'pausiert' : 'laeuft';
    $('#zustand-text').textContent = zustand.pausiert ? 'Pause angefordert' : 'Fortsetzen angefordert';
    hinweis.textContent = zustand.pausiert
      ? 'Der nächste Lauf liest den Befehl und bricht ab. Bereits offene Positionen laufen weiter bis Ziel, Stop oder Zeitablauf.'
      : 'Der nächste Lauf nimmt die Analyse wieder auf.';
  });
}

async function sendeBefehl(befehl) {
  try {
    const antwort = await fetch('https://ntfy.sh/' + STEUER_THEMA,
                                { method: 'POST', body: befehl });
    return antwort.ok;
  } catch (fehler) {
    return false;
  }
}

// ── Ideen des Tages ────────────────────────────────────────────────────────

function zeigeMesswerte(daten) {
  const r = daten.regime || {};
  const b = daten.basisquote_gesamt || {};
  const werte = [
    ['Markt', r.trend === 'aufwaerts' ? 'aufwärts' : r.trend === 'abwaerts' ? 'abwärts' : '—',
     r.trend === 'aufwaerts' ? 'auf' : r.trend === 'abwaerts' ? 'ab' : ''],
    ['VIX', r.vix === null || r.vix === undefined ? '—' : zahl(r.vix, 1), ''],
    ['Bewertet', zahl(daten.scored, 0), ''],
    ['Basisquote', b.p_ziel === undefined ? '—' : anteil(b.p_ziel, 1), ''],
  ];
  const feld = $('#messwerte');
  feld.hidden = false;
  feld.innerHTML = werte.map(([marke, wert, klasse]) => `
    <div class="messwert">
      <span class="marke">${esc(marke)}</span>
      <span class="zahl ${klasse}">${esc(wert)}</span>
    </div>`).join('');
}

/** Die drei gemessenen Ausgaenge als ein Balken.
 *  Was zuerst kam, entscheidet — die Beruehrungsquoten allein sagen nichts,
 *  weil Ziel und Stop beide beruehrt werden koennen.
 *
 *  Chance-Risiko-Verhaeltnis und Basiserwartung haengen an derselben Zeile:
 *  auf einem 320 Pixel breiten Display bricht sonst die Beschriftung um und
 *  schiebt die Zahlen durcheinander. */
function ausgangsBalken(bq, t) {
  if (!bq) return '';
  const teile = [
    ['b-ziel', 'auf', bq.p_ziel, 'Ziel zuerst'],
    ['b-stop', 'ab', bq.p_stop, 'Stop zuerst'],
    ['b-zeit', 'zeit', bq.p_zeit, 'Zeitablauf'],
  ];
  const balken = teile.map(([k, , p]) =>
    `<i class="${k}" style="width:${((p || 0) * 100).toFixed(1)}%"></i>`).join('');
  const legende = teile.map(([k, farbe, p, name]) =>
    `<span><i class="${k}" style="background:var(--${farbe})"></i>${esc(name)} ${anteil(p)}</span>`).join('');
  const kennzahlen =
    `<span class="zahl">CRV ${zahl(t.reward_risk, 2)}</span>` +
    `<span class="zahl">Basis ${rWert(bq.erwartung_r)}</span>`;
  return `
    <span class="marke" style="display:block;margin-top:11px">Gemessener Ausgang dieser Marken</span>
    <div class="balken">${balken}</div>
    <div class="legende">${legende}${kennzahlen}</div>`;
}

function methodeHtml(m) {
  const wert = m.value === null ? '—' : zahl(m.value) + ' USD';
  const rolle = m.role === 'neigung' ? 'Neigung' : 'Niveau';
  return `
    <div class="methode">
      <div class="methode-kopf">
        <span class="titel">${esc(m.label)}</span>
        <span class="zahl">${esc(wert)}</span>
      </div>
      <span class="marke">${esc(rolle)}</span>
      ${m.steps && m.steps.length
        ? `<div class="schritt">${esc(m.steps.join('\n'))}</div>` : ''}
      ${m.note ? `<p class="notiz">${esc(m.note)}</p>` : ''}
    </div>`;
}

function komponenteHtml(c) {
  const wert = c.score === null ? 'keine Daten' : zahl(c.score, 2);
  const breite = c.score === null ? 0 : c.score * 100;
  return `
    <div class="komponente">
      <div class="komponente-kopf">
        <span>${esc(c.label)}</span>
        <span class="zahl leise">${esc(wert)} × ${zahl(c.weight, 2)}</span>
      </div>
      <div class="schiene"><i style="width:${breite.toFixed(1)}%"></i></div>
      ${c.reasons && c.reasons.length
        ? `<ul>${c.reasons.map((g) => `<li>${esc(g)}</li>`).join('')}</ul>` : ''}
    </div>`;
}

function fundamentalHtml(f) {
  if (!f || f._ok === false) return '';
  const paare = [
    ['Marge', f.profit_margin === undefined ? null : prozent(f.profit_margin * 100)],
    ['ROE', f.return_on_equity === undefined ? null : prozent(f.return_on_equity * 100)],
    ['Umsatzwachstum', f.revenue_growth === undefined ? null : prozent(f.revenue_growth * 100, 1, true)],
    ['Forward-KGV', f.forward_pe === undefined ? null : zahl(f.forward_pe, 1)],
    ['Beta', f.beta === undefined ? null : zahl(f.beta, 2)],
    ['Nächste Zahlen', f.next_earnings ? datumKurz(f.next_earnings) : null],
  ].filter(([, wert]) => wert !== null && wert !== undefined && wert !== '—');
  if (!paare.length) return '';
  return `
    <div class="block">
      <h3>Fundamentaldaten</h3>
      <div class="paare">
        ${paare.map(([marke, wert]) => `
          <div class="paar">
            <span class="marke">${esc(marke)}</span>
            <span class="zahl">${esc(wert)}</span>
          </div>`).join('')}
      </div>
    </div>`;
}

function ideeHtml(idee) {
  const t = idee.targets || {};
  const s = idee.scoring || {};
  const l = idee.llm || null;
  const bq = t.basisquote || null;
  const richtung = (idee.upside_pct || 0) >= 0 ? 'auf' : 'ab';

  const listen = [];
  if (l && l.katalysatoren && l.katalysatoren.length) {
    listen.push(['Katalysatoren', l.katalysatoren]);
  }
  if (l && l.risiken && l.risiken.length) listen.push(['Risiken', l.risiken]);

  return `
  <article class="karte idee" id="idee-${esc(idee.symbol)}">
    <div class="idee-kopf">
      <div class="idee-titel">
        <span class="zahl symbol">${esc(idee.symbol)}</span>
        <span class="name">${esc(idee.name || '')}${idee.sector ? ' · ' + esc(idee.sector) : ''}</span>
      </div>
      <div class="idee-rechts">
        <span class="zahl upside ${richtung}">${prozent(idee.upside_pct, 1, true)}</span>
        <span class="marke">Score ${zahl(idee.score, 2)}</span>
      </div>
    </div>

    <div class="marken">
      <div><span class="marke">Kurs</span><span class="zahl">${zahl(idee.price)}</span></div>
      <div><span class="marke">Ziel</span><span class="zahl auf">${zahl(idee.target)}</span></div>
      <div><span class="marke">Stop</span><span class="zahl ab">${zahl(idee.stop)}</span></div>
    </div>

    ${bq ? ausgangsBalken(bq, t) : `
    <div class="ausgang-zeile">
      <span class="marke">Chance gegen Risiko</span>
      <span class="zahl">CRV ${zahl(t.reward_risk, 2)}</span>
    </div>`}

    ${l && l.these ? `<p class="these">${esc(l.these)}</p>` : ''}

    <details class="herleitung">
      <summary>Herleitung aufklappen</summary>

      ${listen.map(([titel, punkte]) => `
        <div class="block">
          <h3>${esc(titel)}</h3>
          <ul class="punktliste">
            ${punkte.map((p) => `<li>${esc(p)}</li>`).join('')}
          </ul>
        </div>`).join('')}

      <div class="block">
        <h3>Wie das Ziel entsteht</h3>
        ${(t.methods || []).map(methodeHtml).join('')}
        ${t.blend_steps && t.blend_steps.length ? `
          <div class="block">
            <h3>Zusammenführung</h3>
            <div class="schritt">${esc(t.blend_steps.join('\n'))}</div>
          </div>` : ''}
        ${t.stop_steps && t.stop_steps.length ? `
          <div class="block">
            <h3>Stop</h3>
            <div class="schritt">${esc(t.stop_steps.join('\n'))}</div>
          </div>` : ''}
      </div>

      ${t.probability_steps && t.probability_steps.length ? `
        <div class="block">
          <h3>Was diese Marken historisch bedeuten</h3>
          <div class="schritt">${esc(t.probability_steps.join('\n'))}</div>
        </div>` : ''}

      <div class="block">
        <h3>Wie der Score entsteht</h3>
        ${(s.components || []).map(komponenteHtml).join('')}
        <div class="schritt" style="margin-top:8px">${esc(scoreZeilen(s).join('\n'))}</div>
      </div>

      ${fundamentalHtml(idee.fundamentals)}
    </details>
  </article>`;
}

function scoreZeilen(s) {
  const zeilen = [];
  if (s.raw_score !== undefined && s.raw_score !== null) {
    zeilen.push(`Gewichteter Mittelwert der Komponenten = ${zahl(s.raw_score, 4)}`);
  }
  (s.penalties || []).forEach((p) => zeilen.push('Abzug: ' + p));
  if (s.score !== undefined && s.score !== null) {
    zeilen.push(`Score = ${zahl(s.score, 4)}`);
  }
  if (s.coverage !== undefined && s.coverage !== null) {
    zeilen.push(`Datenabdeckung ${anteil(s.coverage)} der Komponenten`);
  }
  return zeilen;
}

function zeigeIdeen(daten) {
  const feld = $('#ideen');
  if (!daten) {
    $('#ideen-anzahl').textContent = '';
    feld.innerHTML = `
      <div class="karte leer">
        <p><strong>Noch keine Analyse.</strong></p>
        <p>Der Vorbörsenlauf startet werktags um 14:15 Schweizer Zeit,
           rund 75 Minuten vor der Eröffnung in New York. Danach stehen
           hier die Ideen des Tages.</p>
      </div>`;
    return;
  }

  const ideen = daten.ideen || [];
  $('#ideen-anzahl').textContent =
    `${ideen.length} von ${zahl(daten.scored, 0)} · ${datumKurz(daten.date)}`;
  zeigeMesswerte(daten);

  if (!ideen.length) {
    feld.innerHTML = `
      <div class="karte leer">
        <p><strong>Heute keine Idee, die alle drei Hürden nimmt.</strong></p>
        <p>Verlangt sind ein Chance-Risiko-Verhältnis über 1.0, eine
           Trefferwahrscheinlichkeit über 25 % und eine Basiserwartung über
           null. Keine Auswahl ist auch eine Auswahl.</p>
      </div>`;
    return;
  }

  const anteil_pct = daten.position_pct ? zahl(daten.position_pct * 100, 0) : null;
  feld.innerHTML = ideen.map(ideeHtml).join('') + `
    <div class="fuss" style="margin-top:14px">
      <p>Alle ${ideen.length} werden zur nächsten Eröffnung virtuell gekauft${
        anteil_pct ? `, je ${anteil_pct} % des Depots` : ''}. Das Zufallsdepot
        zieht gleichzeitig ${ideen.length} Titel aus demselben Topf — gleiche
        Regeln, andere Auswahl.</p>
    </div>`;
}

// ── Depotvergleich ─────────────────────────────────────────────────────────

// Der Index bekommt eine gestrichelte Linie, nicht nur einen anderen Grauton:
// zwei Grautoene sind auf einem Handydisplay bei Sonnenlicht nicht mehr zu
// unterscheiden, ein Strichmuster schon.
const KURVEN = [
  { schluessel: 'ki', name: 'Analyse', farbe: 'var(--akzent)', breite: 1.8, strich: '' },
  { schluessel: 'zufall', name: 'Zufall', farbe: 'var(--tinte-leise)', breite: 1.2, strich: '' },
  { schluessel: 'spy', name: 'S&P 500 (SPY)', farbe: 'var(--tinte-leise)', breite: 1.2, strich: '5 4' },
];

/** Equity-Kurven als reines SVG. Eine Diagrammbibliothek waere fuer drei
 *  Linien mehr Ballast als Nutzen — und muesste von einem fremden Server
 *  kommen. */
function kurvenSvg(equity) {
  const B = 640, H = 200;
  const links = 6, rechts = 46, oben = 10, unten = 20;
  const start = equity.start_capital || 100000;

  const reihen = KURVEN
    .map((k) => ({ ...k, punkte: equity[k.schluessel] || [] }))
    .filter((k) => k.punkte.length > 1);
  if (!reihen.length) return '';

  const alleDaten = [...new Set(reihen.flatMap((r) => r.punkte.map((p) => p.date)))].sort();
  const xVon = new Map(alleDaten.map((d, i) => [d, i]));
  const n = Math.max(1, alleDaten.length - 1);

  const werte = reihen.flatMap((r) => r.punkte.map((p) => p.equity / start - 1));
  let lo = Math.min(0, ...werte), hi = Math.max(0, ...werte);
  const luft = Math.max((hi - lo) * 0.08, 0.005);
  lo -= luft; hi += luft;

  const x = (datum) => links + (xVon.get(datum) / n) * (B - links - rechts);
  const y = (v) => oben + (1 - (v - lo) / (hi - lo)) * (H - oben - unten);

  // Nulllinie, Ober- und Untergrenze, bei genug Platz die Mitte — mehr
  // braucht ein Messblatt nicht. Doppelte Beschriftungen fallen ueber den
  // gerundeten Text weg, nicht ueber den Zahlenwert: sonst verschwindet
  // ausgerechnet die oberste Marke, wenn sie sich beim Runden nach oben
  // aus dem Wertebereich schiebt.
  const marken = [hi, 0, lo];
  if (hi - lo > 0.12) marken.push((hi + lo) / 2);
  const gesehen = new Set();
  const gitter = marken.map((v) => {
    const text = (v * 100).toFixed(0) + ' %';
    if (gesehen.has(text)) return '';
    gesehen.add(text);
    return `
      <line x1="${links}" y1="${y(v).toFixed(1)}" x2="${B - rechts}" y2="${y(v).toFixed(1)}"
            stroke="${v === 0 ? 'var(--linie)' : 'var(--linie-fein)'}" stroke-width="1"/>
      <text x="${B - rechts + 5}" y="${(y(v) + 3.5).toFixed(1)}" font-size="10"
            fill="var(--tinte-leise)" font-family="var(--mono)">${esc(text)}</text>`;
  }).join('');

  const linien = reihen.map((r) => {
    const d = r.punkte
      .map((p, i) => `${i ? 'L' : 'M'}${x(p.date).toFixed(1)},${y(p.equity / start - 1).toFixed(1)}`)
      .join(' ');
    return `<path d="${d}" fill="none" stroke="${r.farbe}" stroke-width="${r.breite}"
                  ${r.strich ? `stroke-dasharray="${r.strich}"` : ''}
                  stroke-linejoin="round" stroke-linecap="round"/>`;
  }).join('');

  const beschriftung = `
    <text x="${links}" y="${H - 5}" font-size="10" fill="var(--tinte-leise)"
          font-family="var(--mono)">${esc(datumKurz(alleDaten[0]))}</text>
    <text x="${B - rechts}" y="${H - 5}" font-size="10" fill="var(--tinte-leise)"
          text-anchor="end" font-family="var(--mono)">${esc(datumKurz(alleDaten[alleDaten.length - 1]))}</text>`;

  return `<svg viewBox="0 0 ${B} ${H}" role="img"
               aria-label="Wertentwicklung der beiden Depots im Vergleich zum S&amp;P 500">
    ${gitter}${linien}${beschriftung}
  </svg>`;
}

function kennzahlenTabelle(stat, equity) {
  const start = equity.start_capital || 100000;
  const spy = equity.spy && equity.spy.length
    ? equity.spy[equity.spy.length - 1].equity / start - 1 : null;

  const spalten = [
    ['Depot Analyse', stat.ki],
    ['Depot Zufall', stat.zufall],
  ];
  const zeilen = [
    ['Rendite', (s) => `<span class="${s.return_pct >= 0 ? 'auf' : 'ab'}">${prozent(s.return_pct, 2, true)}</span>`,
     spy === null ? '—' : `<span class="${spy >= 0 ? 'auf' : 'ab'}">${prozent(spy * 100, 2, true)}</span>`],
    ['Abgeschlossene Trades', (s) => zahl(s.trades, 0), '—'],
    ['Trefferquote', (s) => s.win_rate === null ? '—' : prozent(s.win_rate), '—'],
    ['Erwartung je Trade', (s) => rWert(s.expectancy_r), '—'],
    ['Profitfaktor', (s) => s.profit_factor === null ? '—' : zahl(s.profit_factor, 2), '—'],
    ['Grösster Rückgang', (s) => s.max_drawdown_pct === null ? '—'
      : `<span class="ab">${prozent(s.max_drawdown_pct, 2)}</span>`, '—'],
    ['Offene Positionen', (s) => zahl(s.open_positions, 0), '—'],
    ['Mittlere Haltedauer', (s) => s.avg_hold_days === null ? '—' : zahl(s.avg_hold_days, 1) + ' Tage', '—'],
  ];

  return `
    <div class="tabelle-huelle">
      <table>
        <thead>
          <tr>
            <th>Kennzahl</th>
            ${spalten.map(([name]) => `<th>${esc(name)}</th>`).join('')}
            <th>SPY</th>
          </tr>
        </thead>
        <tbody>
          ${zeilen.map(([name, fn, spyWert]) => `
            <tr>
              <td>${esc(name)}</td>
              ${spalten.map(([, s]) => `<td class="zahl">${fn(s)}</td>`).join('')}
              <td class="zahl">${spyWert}</td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}

/** Reicht der Vorsprung ueber den Zufall hinaus, oder ist er Rauschen?
 *  Ohne diese Zeile liesse sich jede Zufallsschwankung als Erfolg lesen. */
function vorsprungText(stat) {
  const a = stat.ki, z = stat.zufall;
  if (!a || !z || !a.trades || !z.trades) return '';
  if (a.expectancy_r === null || z.expectancy_r === null) return '';
  const diff = a.expectancy_r - z.expectancy_r;
  const n = Math.min(a.trades, z.trades);
  // Grobe Einordnung: unter 200 Trades je Depot ist der Vergleich zu
  // verrauscht, um irgendetwas zu behaupten.
  if (n < 200) {
    return `Erst ${zahl(n, 0)} Trades je Depot — zu wenig, um einen Unterschied
            von ${rWert(diff)} je Trade von Zufall zu unterscheiden. Die Frage
            bleibt vorerst offen.`;
  }
  if (Math.abs(diff) < 0.05) {
    return `Die Analyse liegt bei ${rWert(a.expectancy_r)} je Trade, der Zufall
            bei ${rWert(z.expectancy_r)}. Das ist derselbe Wert. Bis hierher
            zahlt sich die Auswahl nicht aus.`;
  }
  return diff > 0
    ? `Die Analyse liegt um ${rWert(diff)} je Trade vor dem Zufall — aus
       ${zahl(n, 0)} Trades je Depot.`
    : `Die Analyse liegt um ${rWert(Math.abs(diff))} je Trade hinter dem
       Zufall — aus ${zahl(n, 0)} Trades je Depot.`;
}

function zeigeDepots(equity) {
  const feld = $('#depots');
  if (!equity || !equity.ki || !equity.ki.length) {
    feld.innerHTML = `
      <div class="karte leer">
        <p><strong>Noch keine Handelshistorie.</strong></p>
        <p>Beide Depots starten mit ${zahl(100000, 0)} USD. Gekauft wird zur
           Eröffnung des Tages nach der Analyse, verkauft bei Kursziel, Stop
           oder nach 20 Handelstagen. Sobald der erste Abrechnungslauf durch
           ist, stehen hier beide Kurven neben dem S&amp;P 500.</p>
      </div>`;
    return;
  }

  const stat = equity.statistik || {};
  const teile = [`<div class="karte diagramm">${kurvenSvg(equity)}
      <div class="kurven-legende">
        ${KURVEN.map((k) => `<span><i style="${k.strich
          ? `background:repeating-linear-gradient(90deg, ${k.farbe} 0 4px, transparent 4px 7px)`
          : `background:${k.farbe}`}"></i>${esc(k.name)}</span>`).join('')}
      </div>
    </div>`];

  if (stat.ki && stat.zufall) {
    teile.push(kennzahlenTabelle(stat, equity));
    const text = vorsprungText(stat);
    if (text) {
      teile.push(`<p class="fuss" style="margin-top:12px;border:none;padding:0">${esc(text)}</p>`);
    }
  }
  feld.innerHTML = teile.join('');
}

// ── Lernkurve ──────────────────────────────────────────────────────────────

/* Belohnung und Bestrafung sichtbar machen. Drei Dinge stehen hier, und die
 * Reihenfolge ist Absicht:
 *
 *   1  In welchem Zustand ist die Lernschleife — wartet sie noch?
 *   2  Wo steht jedes Gewicht heute, verglichen mit seinem Startwert?
 *   3  Was genau wurde wann belohnt oder bestraft?
 *
 * Punkt 1 zuerst, weil er in den ersten Wochen der einzig wahre ist: unter
 * zwanzig abgeschlossenen Trades wird nichts angefasst.
 */

/** Kurve fuer EIN Gewicht. Sieben Linien in ein Diagramm zu legen hiesse,
 *  sieben unterscheidbare Farben zu erfinden — auf einem Handydisplay sind
 *  das drei zu viel. Stattdessen sieben kleine Diagramme mit derselben
 *  Skala: vergleichen laesst sich weiterhin beides, aber die Farbe bleibt
 *  bedeutungstragend. Gestrichelt der Startwert. */
function verlaufSvg(werte, start, lo, hi) {
  const B = 120, H = 26, rand = 3;
  const y = (v) => (rand + (1 - (v - lo) / (hi - lo)) * (H - 2 * rand)).toFixed(1);
  const x = (i) => ((i / (werte.length - 1)) * B).toFixed(1);
  const d = werte.map((v, i) => `${i ? 'L' : 'M'}${x(i)},${y(v)}`).join(' ');
  // preserveAspectRatio="none" streckt das Diagramm auf die Spaltenbreite;
  // non-scaling-stroke haelt die Linie dabei gleich duenn.
  return `<svg class="verlauf" viewBox="0 0 ${B} ${H}" preserveAspectRatio="none"
               aria-hidden="true">
    <line x1="0" y1="${y(start)}" x2="${B}" y2="${y(start)}" stroke="var(--tinte-leise)"
          stroke-width="1" stroke-dasharray="3 3" opacity="0.45"
          vector-effect="non-scaling-stroke"/>
    <path d="${d}" fill="none" stroke="var(--akzent)" stroke-width="1.6"
          stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
  </svg>`;
}

/** Jede Kurve bekommt einen Ausschnitt von mindestens `spanne` um ihren
 *  Startwert. Beide naheliegenden Alternativen sind schlechter:
 *
 *  Eine gemeinsame Skala fuer alle Kurven zeigt die Hoehe richtig — und
 *  macht genau das unsichtbar, wofuer die Kurve da ist. Jede Linie klebte
 *  am oberen oder unteren Rand ihres Kastens, die Bewegung verschwaende.
 *
 *  Eine freie Skala je Kurve zeigt jede Bewegung gross — auch eine von drei
 *  Tausendsteln, die dann aussieht wie eine Kehrtwende.
 *
 *  Der feste Ausschnitt zeigt beides ehrlich: die Bewegung ist zu sehen,
 *  und eine winzige bleibt winzig. Wo das Gewicht steht, sagt die Zahl
 *  daneben. */
function gewichtsBlock(reihen, stellen, spanne) {
  return reihen.map((r) => {
    const anfang = r.werte[0];
    const rand = Math.max(
      spanne / 2,
      ...r.werte.map((v) => Math.abs(v - anfang) * 1.2)
    );
    const lo = anfang - rand, hi = anfang + rand;
    const jetzt = r.werte[r.werte.length - 1];
    const delta = jetzt - r.werte[0];
    const richtung = Math.abs(delta) < 5e-4 ? '' : delta > 0 ? 'auf' : 'ab';
    const deltaText = richtung
      ? (delta > 0 ? '+' : '') + zahl(delta, stellen)
      : 'unverändert';
    return `
      <div class="gewicht">
        <div class="gewicht-kopf">
          <span class="name">${esc(r.name)}</span>
          <span class="zahl">${zahl(jetzt, stellen)}
            <span class="${richtung || 'leise'}">${esc(deltaText)}</span></span>
        </div>
        ${r.werte.length > 1 ? verlaufSvg(r.werte, r.werte[0], lo, hi) : ''}
      </div>`;
  }).join('');
}

/** Werte eines Gewichts vom Startwert bis heute. */
function reiheBauen(key, name, start, jetzt, verlauf, feld) {
  const anfang = start && start[key] !== undefined ? start[key] : jetzt[key];
  if (anfang === undefined) return null;
  const werte = [anfang];
  verlauf.forEach((e) => {
    const g = e[feld];
    if (g && g[key] !== undefined) werte.push(g[key]);
  });
  if (jetzt[key] !== undefined && werte[werte.length - 1] !== jetzt[key]) {
    werte.push(jetzt[key]);
  }
  return { key, name, werte };
}

function multiListe(titel, werte, erklaerung, stellen) {
  const eintraege = Object.entries(werte || {});
  if (!eintraege.length) return '';
  eintraege.sort((a, b) => b[1] - a[1]);
  return `
    <div class="block">
      <h3>${esc(titel)}</h3>
      <p class="notiz">${esc(erklaerung)}</p>
      <div class="paare">
        ${eintraege.map(([name, wert]) => `
          <div class="paar">
            <span class="marke">${esc(name)}</span>
            <span class="zahl ${wert > 1 ? 'auf' : wert < 1 ? 'ab' : ''}">${zahl(wert, stellen || 2)}</span>
          </div>`).join('')}
      </div>
    </div>`;
}

const PROTOKOLL_SICHTBAR = 25;

function protokollHtml(verlauf) {
  if (!verlauf.length) return '';
  const neueste = verlauf.slice().reverse().slice(0, PROTOKOLL_SICHTBAR);
  return `
    <details class="herleitung protokoll">
      <summary>Protokoll aller ${zahl(verlauf.length, 0)} Lernschritte</summary>
      ${neueste.map((e) => `
        <div class="lern-eintrag">
          <div class="lern-kopf">
            <span class="zahl">${esc(datumLang(e.date))}</span>
            <span class="marke">${zahl(e.window, 0)} Trades im Fenster ·
              Schrittweite ${zahl(e.damping, 2)} · Mittel ${rWert(e.mean_r)}</span>
          </div>
          <ul class="punktliste">
            ${(e.changes || []).map((c) => `<li>${esc(c)}</li>`).join('')}
          </ul>
        </div>`).join('')}
      ${verlauf.length > PROTOKOLL_SICHTBAR ? `
        <p class="notiz">Die älteren ${zahl(verlauf.length - PROTOKOLL_SICHTBAR, 0)}
           Schritte stehen im Datenverlauf auf GitHub.</p>` : ''}
    </details>`;
}

function zustandKarte(w, verlauf) {
  const regeln = w.regeln || {};
  const min = regeln.min_trades || 20;
  const gesehen = w.trades_seen || 0;

  let kopf, text;
  if (gesehen < min) {
    kopf = 'Die Lernschleife wartet.';
    text = `Verändert wird erst ab ${zahl(min, 0)} abgeschlossenen Trades des
            Analysedepots — bisher sind es ${zahl(gesehen, 0)}. Darunter wäre
            jede Anpassung Rauschen: bei so wenigen Trades ist der
            Standardfehler eines gemessenen Vorsprungs grösser als der
            Vorsprung selbst.`;
  } else if (!verlauf.length) {
    kopf = `Aus ${zahl(gesehen, 0)} Trades gelernt — ohne eine einzige Änderung.`;
    text = `Kein gemessener Unterschied war deutlich genug. Der Schritt richtet
            sich nach der Sicherheit der Messung, nicht nach ihrer Grösse: erst
            ab einem t-Wert von 2 wird voll reagiert. Dass hier nichts steht,
            ist ein Ergebnis und kein Ausfall.`;
  } else {
    const letzter = verlauf[verlauf.length - 1];
    kopf = `${zahl(verlauf.length, 0)} Lernschritte aus ${zahl(gesehen, 0)} Trades.`;
    text = `Zuletzt am ${datumLang(letzter.date)}. Gerechnet wird über die
            letzten ${zahl(regeln.fenster || 100, 0)} Trades; die Schrittweite
            wächst mit der Stichprobe und ist erst bei
            ${zahl(regeln.volle_schrittweite_ab || 600, 0)} Trades voll.`;
  }

  const balken = gesehen < min ? `
    <div class="fortschritt" role="img"
         aria-label="${zahl(gesehen, 0)} von ${zahl(min, 0)} Trades">
      <i style="width:${Math.min(100, (gesehen / min) * 100).toFixed(1)}%"></i>
    </div>` : '';

  return `
    <div class="karte lern-karte">
      <p class="lern-satz"><strong>${esc(kopf)}</strong></p>
      ${balken}
      <p class="lern-satz">${esc(text)}</p>
      <p class="lern-satz leise">Gelernt wird ausschliesslich aus dem Depot der
         Analyse. Das Zufallsdepot ist die Kontrollgruppe und fliesst nirgends
         ein — sonst wäre der ganze Vergleich wertlos.</p>
    </div>`;
}

function zeigeLernen(w) {
  const feld = $('#lernen');
  const anzahl = $('#lernen-anzahl');

  if (!w) {
    anzahl.textContent = '';
    feld.innerHTML = `
      <div class="karte leer">
        <p><strong>Noch nichts gelernt.</strong></p>
        <p>Nach jedem abgeschlossenen Trade zählt das R-Multiple als Belohnung
           oder Bestrafung: Es verschiebt das Gewicht der Score-Komponenten,
           der Kursziel-Methoden und der Branchen. Sobald der erste
           Abrechnungslauf durch ist, steht hier, was sich dadurch bewegt hat.</p>
      </div>`;
    return;
  }

  const verlauf = (w.history || []).filter((e) => e && e.date);
  const labels = w.labels || {};
  const start = w.start || {};
  const gesehen = w.trades_seen || 0;

  // "64 von 20 Trades" waere Unsinn: die Schwelle interessiert nur, solange
  // sie noch nicht erreicht ist.
  const mindest = (w.regeln || {}).min_trades || 20;
  anzahl.textContent = verlauf.length
    ? `${zahl(verlauf.length, 0)} Schritte · ${zahl(gesehen, 0)} Trades`
    : gesehen < mindest
      ? `${zahl(gesehen, 0)} von ${zahl(mindest, 0)} Trades`
      : `${zahl(gesehen, 0)} Trades`;

  const teile = [zustandKarte(w, verlauf)];

  const scoreReihen = Object.keys(w.score_weights || {})
    .map((k) => reiheBauen(k, (labels.score || {})[k] || k,
                           start.score_weights, w.score_weights,
                           verlauf, 'score_weights'))
    .filter(Boolean)
    .sort((a, b) => b.werte[b.werte.length - 1] - a.werte[a.werte.length - 1]);

  // Solange nichts gelernt wurde, gibt es keine Kurven — dann sind die
  // Erklaerungen dazu nur Wortlaerm.
  const gelernt = verlauf.length > 0;

  if (scoreReihen.length) {
    teile.push(`
      <div class="karte lern-karte">
        <h3>Gewicht der Score-Komponenten</h3>
        <p class="notiz">${gelernt
          ? `Was eine Aktie überhaupt auf die Liste bringt. Gestrichelt der
             Startwert, die Linie der Verlauf — ein Punkt je Lernschritt,
             nicht zeitproportional.`
          : `Was eine Aktie überhaupt auf die Liste bringt. Das sind die
             Startgewichte; sobald gelernt wird, steht hier ihr Verlauf.`}</p>
        <div class="gewichte">${gewichtsBlock(scoreReihen, 3, 0.06)}</div>
        ${gelernt ? `
          <p class="notiz">Jede Kurve zeigt einen Ausschnitt von mindestens
             &pm; 0.03 um ihren Startwert — eine winzige Bewegung sieht
             damit auch winzig aus. Nach jedem Schritt wird auf Summe 1
             normiert. Deshalb bewegt sich auch, was gar nicht bewertet wurde
             — eine Verschiebung allein ist noch keine Belohnung. Was
             tatsächlich belohnt oder bestraft wurde, steht unten im
             Protokoll.</p>` : ''}
      </div>`);
  }

  const methodenReihen = Object.keys(w.target_method_weights || {})
    .map((k) => reiheBauen(k, (labels.methode || {})[k] || k,
                           start.target_method_weights, w.target_method_weights,
                           verlauf, 'target_method_weights'))
    .filter(Boolean);

  if (methodenReihen.length) {
    teile.push(`
      <div class="karte lern-karte">
        <h3>Gewicht der Kursziel-Methoden</h3>
        <p class="notiz">Für das Ziel zählt allein das Verhältnis der beiden
           Gewichte zueinander, nicht ihre Höhe.</p>
        <div class="gewichte">${gewichtsBlock(methodenReihen, 3, 0.6)}</div>
      </div>`);
  }

  const multis = [
    multiListe('Zielweite je Branche', w.sector_k_mult,
               'Multiplikator auf den kalibrierten Zielfaktor. Über 1 heisst: das '
               + 'Ziel wurde häufiger erreicht, als die Basisquote erwarten liess '
               + '— es darf also weiter hinaus.', 3),
    multiListe('Branchen', w.sector_multiplier,
               'Multiplikator auf den Score. Über 1 heisst: Trades dieser Branche '
               + 'liefen besser als alle übrigen zusammen.'),
    multiListe('Marktphasen', w.regime_multiplier,
               'Trend des S&P 500 gegen seine 200-Tage-Linie, verbunden mit dem '
               + 'Niveau des VIX.'),
  ].filter(Boolean);

  if (multis.length) {
    teile.push(`<div class="karte lern-karte">${multis.join('')}</div>`);
  }

  const prot = protokollHtml(verlauf);
  if (prot) teile.push(`<div class="karte lern-karte">${prot}</div>`);

  feld.innerHTML = teile.join('');
}

// ── Darstellung ────────────────────────────────────────────────────────────

function themaEinrichten() {
  let gewaehlt = 'auto';
  try { gewaehlt = localStorage.getItem('thema') || 'auto'; } catch (f) { /* egal */ }
  setzeThema(gewaehlt);
  document.querySelectorAll('[data-thema-wahl]').forEach((knopf) => {
    knopf.addEventListener('click', () => setzeThema(knopf.dataset.themaWahl));
  });
}

function setzeThema(wert) {
  document.documentElement.dataset.thema = wert;
  try { localStorage.setItem('thema', wert); } catch (f) { /* egal */ }
  document.querySelectorAll('[data-thema-wahl]').forEach((knopf) => {
    knopf.setAttribute('aria-pressed', String(knopf.dataset.themaWahl === wert));
  });
}

// ── Als App aufs Gerät ─────────────────────────────────────────────────────

/* Eine PWA installiert man je nach Browser voellig verschieden, und keiner
 * sagt es von selbst deutlich. Chrome auf Android meldet sich mit
 * `beforeinstallprompt` — dort gibt es einen echten Knopf. Safari auf dem
 * iPhone kennt das Ereignis nicht und verlangt den Weg ueber "Teilen"; dort
 * hilft nur die Anleitung. Laeuft die Seite schon als App, steht hier
 * nichts. */
let installAufforderung = null;

function installEinrichten() {
  const feld = $('#installieren');
  const text = $('#installieren-text');
  const knopf = $('#installieren-knopf');

  const alsApp = window.matchMedia('(display-mode: standalone)').matches
    || window.navigator.standalone === true;
  if (alsApp) return;

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    installAufforderung = e;
    text.textContent = 'Diese Seite lässt sich als App auf den Startbildschirm '
      + 'legen — sie startet dann ohne Adresszeile und zeigt die letzten Daten '
      + 'auch ohne Netz.';
    knopf.hidden = false;
    feld.hidden = false;
  });

  knopf.addEventListener('click', async () => {
    if (!installAufforderung) return;
    knopf.disabled = true;
    installAufforderung.prompt();
    const wahl = await installAufforderung.userChoice;
    installAufforderung = null;
    text.textContent = wahl.outcome === 'accepted'
      ? 'Installiert — die App liegt jetzt auf dem Startbildschirm.'
      : 'Abgebrochen. Der Knopf erscheint beim nächsten Besuch wieder.';
    knopf.hidden = true;
  });

  window.addEventListener('appinstalled', () => { feld.hidden = true; });

  // Apple: kein Ereignis, kein Knopf, nur der Weg über das Teilen-Menü.
  const apple = /iPad|iPhone|iPod/.test(navigator.userAgent)
    || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  if (apple) {
    text.textContent = 'Als App aufs iPhone: unten auf Teilen tippen, dann '
      + '„Zum Home-Bildschirm“. Die Seite startet danach ohne Adresszeile und '
      + 'zeigt die letzten Daten auch ohne Netz.';
    feld.hidden = false;
  }
}

// ── Fragen an die Zahlen ───────────────────────────────────────────────────

/* Eine Zeile, in die man tippt, was man wissen will.
 *
 * Die Antworten entstehen HIER im Browser, aus genau den Dateien, die die
 * Seite ohnehin geladen hat. Das ist eine Entscheidung und keine
 * Sparmassnahme: Ollama laeuft nur im GitHub-Runner, zweimal am Tag, und ist
 * danach weg — es gibt keinen Endpunkt, den ein Handy fragen koennte. Und ein
 * Sprachmodell, das aus dem Gedaechtnis antwortet, wuerde Zahlen erfinden.
 * Auf einer Seite mit Kurszielen ist eine erfundene Zahl schlimmer als keine
 * Antwort, weil sie genauso aussieht wie eine gemessene.
 *
 * Deshalb gilt hier durchgehend: jede genannte Zahl steht so in den Daten.
 * Was nicht in den Daten steht, wird nicht beantwortet, sondern gesagt.
 */

const WISSEN = { latest: null, equity: null, gewichte: null, status: null };

/** Kleinschreibung, Umlaute aufgeloest, Satzzeichen weg. Damit findet
 *  "Gesundheitswesen" die Branche Gesundheit und "CRV?" scheitert nicht am
 *  Fragezeichen. */
function schlicht(text) {
  return String(text === null || text === undefined ? '' : text).toLowerCase()
    .replace(/ä/g, 'ae').replace(/ö/g, 'oe').replace(/ü/g, 'ue')
    .replace(/ß/g, 'ss')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

/** Woerter, die kein Kuerzel sein duerfen, obwohl es sie als Kuerzel gibt.
 *  ALL ist Allstate, aber "alle Aktien" fragt nicht nach Allstate. Ohne
 *  diese Liste beantwortet die Zeile die haeufigsten Fragen mit dem
 *  falschen Titel. */
const KEIN_KUERZEL = new Set(schlicht(
  'all alle keine kein was wie wer wo warum wieso weshalb welche welcher '
  + 'welches ist sind war das die der den dem des ein eine einen und oder '
  + 'mit von aus bei fuer ueber auf ab nur noch auch schon sehr mehr viel '
  + 'viele gut besser best beste besten bester gross klein hoch tief lang '
  + 'kurz '
  + 'heute morgen gestern jetzt dann denn doch aber wenn weil dass ohne '
  + 'gegen zwischen kann soll muss wird habe hast hat haben mir mich ich '
  + 'man sie ihm ihr uns tag jahr geld kauf kaufen ziel stop kurs score '
  + 'depot news top rang liste zahl aktie aktien titel firma').split(' '));

/** Namensbestandteile, die nichts unterscheiden. "Group" faende sonst
 *  vierzig Titel gleich gut. */
const KEIN_NAME = new Set(schlicht(
  'inc corp corporation company co the and of plc ltd limited holdings '
  + 'holding group class series international industries technologies '
  + 'technology systems solutions services partners enterprises global '
  + 'american united national general new').split(' '));

/** Was man sagt, und was in den Daten steht. */
const KUERZEL_ALIAS = {
  google: 'GOOGL', alphabet: 'GOOGL', facebook: 'META',
  berkshire: 'BRK.B', buffett: 'BRK.B',
};

const BRANCHEN_ALIAS = {
  tech: 'Technologie', technik: 'Technologie', halbleiter: 'Technologie',
  chip: 'Technologie', chips: 'Technologie', software: 'Technologie',
  pharma: 'Gesundheit', medizin: 'Gesundheit', gesundheitswesen: 'Gesundheit',
  bank: 'Finanzen', banken: 'Finanzen', versicherung: 'Finanzen',
  oel: 'Energie', erdoel: 'Energie', gas: 'Energie',
  rohstoff: 'Grundstoffe', rohstoffe: 'Grundstoffe', chemie: 'Grundstoffe',
  telekom: 'Kommunikation', medien: 'Kommunikation',
  strom: 'Versorger', energieversorger: 'Versorger',
  bau: 'Industrie', maschinen: 'Industrie', ruestung: 'Industrie',
  handel: 'Konsum zyklisch', auto: 'Konsum zyklisch',
  lebensmittel: 'Konsum defensiv', nahrung: 'Konsum defensiv',
};

/* Das Nachschlagewerk. Die Seite ist voller Fachwoerter — CRV, R, Basisquote,
 * t-Wert —, und wer sie nicht kennt, liest die Zahlen falsch herum. Jeder
 * Eintrag sagt drei Dinge: was es ist, wie es hier gerechnet wird, und was
 * daraus folgt. `k` sind die Woerter, unter denen man sucht. */
const GLOSSAR = [
  { k: 'crv chance risiko chancerisiko reward risk verhaeltnis',
    t: 'Chance-Risiko-Verhältnis (CRV)',
    b: 'Wie weit das Kursziel entfernt liegt, geteilt durch den Abstand zum '
     + 'Stop. CRV 2.0 heisst: geht die Idee auf, ist der Gewinn doppelt so '
     + 'gross wie der Verlust, wenn sie schiefgeht. Das CRV sagt nichts '
     + 'darüber, wie <em>wahrscheinlich</em> das Ziel erreicht wird — dafür '
     + 'steht daneben die gemessene Trefferquote. Ein hohes CRV mit einer '
     + 'sehr tiefen Trefferquote ist kein guter Handel, sondern ein weit '
     + 'entferntes Ziel.' },
  { k: 'r multiple rmultiple erwartung je trade expectancy',
    t: 'R und R-Multiple',
    b: 'Ein R ist der Betrag, den ein Trade kostet, wenn der Stop hält: der '
     + 'Abstand vom Einstieg zum Stop. Alles wird in dieser Einheit gemessen, '
     + 'damit ein 30-USD-Titel und ein 900-USD-Titel vergleichbar bleiben. '
     + '+2 R heisst: doppelt so viel gewonnen wie im Verlustfall riskiert. '
     + 'Die «Erwartung je Trade» ist der Durchschnitt aller R-Werte — über '
     + 'null verdient das Depot, unter null verliert es.' },
  { k: 'basisquote basiserwartung grundquote',
    t: 'Basisquote und Basiserwartung',
    b: 'Was diese Marken historisch bedeuten, ganz ohne Auswahl: über das '
     + 'ganze Universum wurde gemessen, wie oft ein Ziel in dieser '
     + 'Entfernung zuerst erreicht wurde, wie oft zuerst der Stop, und wie '
     + 'oft nach 20 Tagen weder noch. Die Basiserwartung ist das R, das eine '
     + '<em>zufällige</em> Auswahl mit derselben Geometrie abwirft. Sie ist '
     + 'die Messlatte: Erst was darüber liegt, hat die Auswahl verdient.' },
  { k: 'atr average true range schwankungsbreite volatilitaet vola',
    t: 'ATR',
    b: 'Die mittlere Tagesspanne der letzten 14 Tage, in USD. Sie ist das '
     + 'Mass, in dem hier alles gedacht wird: Das Ziel liegt rund 2.2 ATR '
     + 'über dem Kurs, der Stop 1.1 ATR darunter. Dadurch bekommt ein ruhiger '
     + 'Titel enge Marken und ein wilder weite — beide mit demselben '
     + 'Chance-Risiko-Verhältnis.' },
  { k: 'stop stoploss verlustbegrenzung',
    t: 'Stop',
    b: 'Der Kurs, bei dem die Position verkauft wird, wenn es schiefläuft. '
     + 'Er liegt 1.1 ATR unter dem Einstieg. Berühren Tageshoch und Tagestief '
     + 'am selben Tag Ziel und Stop, zählt hier immer der Stop zuerst — '
     + 'Tagesbalken können die Reihenfolge nicht auflösen, und die '
     + 'pessimistische Annahme ist die einzige ehrliche.' },
  { k: 'kursziel ziel target zielkurs',
    t: 'Kursziel',
    b: 'Wohin der Kurs läuft, wenn die Bewegung kommt — nicht, wo er in '
     + 'einem Jahr steht. Der Horizont sind 15 Handelstage. Zwei Methoden '
     + 'liefern das Niveau (ATR-Projektion und gemessene Bewegung), zwei '
     + 'weitere verschieben es als Neigung (Analystenkonsens und '
     + 'Bewertungsanker). Die vollständige Rechnung mit allen eingesetzten '
     + 'Zahlen steht auf jeder Ideenkarte unter «Herleitung aufklappen».' },
  { k: 'score bewertung punktzahl',
    t: 'Score',
    b: 'Der gewichtete Mittelwert aus sieben Komponenten: Trend und relative '
     + 'Stärke, Setup-Qualität, Volumenbestätigung, fundamentale Qualität, '
     + 'Bewertung gegen die Branche, Analysten-Rückenwind und News-Sentiment. '
     + 'Davon gehen Abzüge ab — etwa für Quartalszahlen in den nächsten fünf '
     + 'Handelstagen. Der Score entscheidet die Reihenfolge, nicht die '
     + 'Kursziele: die stehen davon unabhängig fest.' },
  { k: 'upside potenzial aufwaertspotenzial',
    t: 'Potenzial',
    b: 'Der Abstand vom heutigen Kurs zum Kursziel, in Prozent. Er sagt '
     + 'nichts darüber, wie wahrscheinlich das Ziel erreicht wird — ein '
     + 'weites Ziel hat viel Potenzial und eine tiefe Trefferquote. Beide '
     + 'Zahlen stehen deshalb nebeneinander.' },
  { k: 'trefferquote win rate gewinnquote',
    t: 'Trefferquote',
    b: 'Der Anteil abgeschlossener Trades mit Gewinn. Allein sagt sie wenig: '
     + 'Mit einem CRV von 2 reicht eine Trefferquote von 34 %, um über null '
     + 'zu liegen. Deshalb steht daneben immer die Erwartung je Trade.' },
  { k: 'profitfaktor profit factor',
    t: 'Profitfaktor',
    b: 'Summe aller Gewinne geteilt durch die Summe aller Verluste. Über 1.0 '
     + 'verdient das Depot, unter 1.0 verliert es. 1.5 gilt bei wenigen '
     + 'hundert Trades noch als gut vereinbar mit reinem Zufall — die Zahl '
     + 'wird erst mit der Stichprobe aussagekräftig.' },
  { k: 'drawdown rueckgang groesster rueckgang',
    t: 'Grösster Rückgang',
    b: 'Der tiefste Absturz vom bisherigen Höchststand des Depots bis zum '
     + 'folgenden Tief, in Prozent. Er misst nicht das Ergebnis, sondern was '
     + 'man unterwegs aushalten musste. Zwei Depots mit derselben Rendite '
     + 'sind nicht gleich gut, wenn eines zwischendurch 40 % verloren hat.' },
  { k: 'marktphase regime marktregime marktrichtung',
    t: 'Marktphase',
    b: 'Steht der S&P 500 über oder unter seiner 200-Tage-Linie, und wie '
     + 'hoch ist der VIX. Die Phase ist ausdrücklich <em>kein</em> Filter: '
     + 'Es werden immer gleich viele Titel gekauft, sonst wäre der Vergleich '
     + 'mit dem Zufallsdepot wertlos. Sie ist ein Merkmal für die '
     + 'Lernschleife — sie darf lernen, in welcher Phase die Auswahl trägt.' },
  { k: 'vix angstindex volatilitaetsindex',
    t: 'VIX',
    b: 'Die vom Optionsmarkt erwartete Schwankung des S&P 500 für die '
     + 'nächsten 30 Tage. Unter 15 ist der Markt ruhig, über 25 nervös, über '
     + '35 im Ausnahmezustand. Er fliesst hier in die Marktphase ein.' },
  { k: 'spy sp500 500 index benchmark vergleichsindex',
    t: 'SPY',
    b: 'Der Fonds auf den S&P 500, hier der dritte Vergleich: einfach kaufen '
     + 'und liegen lassen. Er ist die härteste Messlatte — wer den Index '
     + 'nicht schlägt, hätte sich die ganze Auswahl sparen können.' },
  { k: 'slippage ausfuehrungskosten spread',
    t: 'Slippage',
    b: 'Fünf Basispunkte (0.05 %) werden bei jedem Kauf und jedem Verkauf '
     + 'abgezogen, weil man in Wirklichkeit nie exakt zum notierten Kurs '
     + 'handelt. Ohne diesen Abzug sähe jede Simulation besser aus, als sie '
     + 'ist.' },
  { k: 't wert twert tvalue signifikanz zufall statistisch',
    t: 't-Wert',
    b: 'Wie deutlich ein gemessener Unterschied gegen das Rauschen steht: der '
     + 'Unterschied geteilt durch seinen Standardfehler. Über 2 ist er '
     + 'schwer als Zufall zu erklären. Die Lernschleife richtet ihre '
     + 'Schrittweite nach dem t-Wert, nicht nach der Grösse des Unterschieds '
     + '— ein grosser Vorsprung aus fünf Trades bewegt hier fast nichts.' },
  { k: 'lernschritt belohnung bestrafung belohnt bestraft lernrate',
    t: 'Belohnung und Bestrafung',
    b: 'Nach jedem Abrechnungslauf wird geprüft, ob die Trades mit hohem Wert '
     + 'einer Komponente besser liefen als die mit tiefem. War der '
     + 'Unterschied deutlich, steigt das Gewicht dieser Komponente, sonst '
     + 'sinkt es. Dasselbe gilt für die Kursziel-Methoden und die Branchen. '
     + 'Alle Grenzen sind hart, jeder Schritt steht im Protokoll unter «Was '
     + 'das System gelernt hat».' },
  { k: 'kontrollgruppe zufallsdepot vergleichsdepot',
    t: 'Warum es zwei Depots gibt',
    b: 'Beide starten mit 100 000 USD, kaufen gleich viele Titel aus '
     + 'demselben Topf, mit derselben Positionsgrösse, denselben Stops und '
     + 'denselben Ausstiegsregeln. Der einzige Unterschied ist die Auswahl. '
     + 'Damit misst der Vergleich wirklich die Analyse und nicht die '
     + 'Handelsmechanik. Gelernt wird ausschliesslich aus dem Analysedepot — '
     + 'das Zufallsdepot muss unberührt bleiben, sonst misst es nichts mehr.' },
  { k: 'kgv forward pe kursgewinnverhaeltnis bewertung',
    t: 'Forward-KGV',
    b: 'Kurs geteilt durch den für das nächste Jahr geschätzten Gewinn je '
     + 'Aktie. Verglichen wird immer gegen den Median der eigenen Branche, '
     + 'nie absolut: Ein KGV von 30 ist für einen Versorger teuer und für '
     + 'einen Halbleiterhersteller normal.' },
  { k: 'roe eigenkapitalrendite',
    t: 'ROE',
    b: 'Gewinn im Verhältnis zum Eigenkapital — wie viel das Unternehmen aus '
     + 'dem Geld macht, das ihm gehört. Eine der vier Zahlen hinter der '
     + 'Komponente «Fundamentale Qualität», neben Marge, Umsatzwachstum und '
     + 'Verschuldung.' },
  { k: 'marge gewinnmarge nettomarge margin',
    t: 'Marge',
    b: 'Wie viel vom Umsatz als Gewinn übrig bleibt. Hohe Margen sind ein '
     + 'Zeichen für Preissetzungsmacht — der Anbieter kann Kosten '
     + 'weitergeben, ohne Kunden zu verlieren.' },
  { k: 'beta schwankung relativ',
    t: 'Beta',
    b: 'Wie stark der Titel gegenüber dem Gesamtmarkt schwankt. Beta 1.5 '
     + 'heisst: Bewegt sich der Markt um 1 %, bewegt sich dieser Titel '
     + 'historisch um 1.5 %. Über 2 gibt es hier einen Abzug auf den Score.' },
  { k: 'datenabdeckung coverage abdeckung',
    t: 'Datenabdeckung',
    b: 'Der Anteil der sieben Score-Komponenten, für die überhaupt Daten '
     + 'vorlagen. Fehlen zu viele — meist weil Yahoo keine Fundamentaldaten '
     + 'liefert —, wird der Titel gar nicht erst als Idee zugelassen. Ein '
     + 'Score aus drei von sieben Komponenten wäre eine Zahl ohne Deckung.' },
  { k: 'zeitablauf haltedauer horizont handelstage',
    t: 'Zeitablauf',
    b: 'Wird nach 20 Handelstagen weder Ziel noch Stop berührt, wird zum '
     + 'Schlusskurs verkauft. Das ist der dritte mögliche Ausgang neben Ziel '
     + 'und Stop, und er ist häufiger, als man denkt — deshalb steht er im '
     + 'Balken auf jeder Ideenkarte mit seiner gemessenen Quote.' },
  { k: 'analystenkonsens analysten analystenziel',
    t: 'Analystenkonsens (Methode 3)',
    b: 'Das mittlere 12-Monats-Kursziel der Analysten, erst ab drei '
     + 'Schätzungen verwendet. Es wirkt hier als <em>Neigung</em> von '
     + 'höchstens ±30 %, nicht als eigenes Ziel. Grund: Ein Jahresziel auf '
     + '15 Handelstage heruntergerechnet liegt fast immer auf Kursniveau und '
     + 'zöge jeden Mittelwert zum Kurs — bei einem Test an echten Apple-Daten '
     + 'fiel das CRV dadurch von 2.6 auf 0.84.' },
  { k: 'bewertungsanker fairwert fairer value',
    t: 'Bewertungsanker (Methode 4)',
    b: 'Geschätzter Gewinn je Aktie mal dem Median-Forward-KGV der Branche '
     + 'ergibt einen fairen Kurs. Der Abstand zum heutigen Kurs wirkt als '
     + 'Neigung von höchstens ±30 % — aus demselben Grund wie beim '
     + 'Analystenkonsens.' },
  { k: 'gemessene bewegung measured move struktur widerstand',
    t: 'Struktur / gemessene Bewegung (Methode 2)',
    b: 'Der nächste Widerstand ist das 55-Tage-Hoch, aber nur, wenn er weiter '
     + 'als eine ATR entfernt liegt — näher ist er Rauschen und kein '
     + 'Widerstand. Sonst wird die Spanne der letzten 20 Tage an das '
     + '55-Tage-Hoch angesetzt, gekappt bei 6 ATR.' },
  { k: 'atrprojektion projektion methode 1',
    t: 'ATR-Projektion (Methode 1)',
    b: 'Kurs plus k mal ATR. Das k ist nicht geraten, sondern je Branche aus '
     + 'der wöchentlichen Kalibrierung gemessen und wird von der Lernschleife '
     + 'nachgeführt: Wurde das Ziel häufiger erreicht, als die Basisquote '
     + 'erwarten liess, darf es weiter hinaus.' },
  { k: 'normierung summe 1 normiert',
    t: 'Normierung auf Summe 1',
    b: 'Nach jedem Lernschritt werden die sieben Score-Gewichte so skaliert, '
     + 'dass sie zusammen 1 ergeben. Deshalb bewegt sich auch ein Gewicht, '
     + 'das gar nicht bewertet wurde — eine Verschiebung allein ist noch '
     + 'keine Belohnung. Was tatsächlich belohnt oder bestraft wurde, steht '
     + 'im Protokoll.' },
  { k: 'relative staerke rs',
    t: 'Relative Stärke',
    b: 'Wie sich der Titel im Vergleich zum S&P 500 über dieselbe Zeit '
     + 'geschlagen hat. Ein Kurs, der nur mit dem ganzen Markt gestiegen ist, '
     + 'hat nichts bewiesen — die relative Stärke trennt das eine vom '
     + 'anderen.' },
  { k: 'sentiment stimmung nachrichten news',
    t: 'News-Sentiment',
    b: 'Die einzige Stelle, an der das Sprachmodell mitrechnet. Es liest die '
     + 'Tagesnachrichten je Kandidat und gibt einen Wert von −1 bis +1, dazu '
     + 'Katalysatoren, Risiken und einen Satz These. Kursziele rechnet es '
     + 'ausdrücklich nicht: Sprachmodelle sind bei Zahlen unzuverlässig.' },
  { k: 'aus schalter pause pausieren abschalten',
    t: 'Aus-Schalter',
    b: 'Der Schalter oben auf der Seite sendet einen Befehl an ein '
     + 'öffentliches ntfy-Thema. Jeder Lauf fragt dieses Thema zuerst ab und '
     + 'bricht bei «pausiert» sofort ab. Weil der Zustand danach im Repo '
     + 'liegt und nicht im Zwischenspeicher von ntfy, hält die Pause '
     + 'beliebig lange.' },
];

// ── Suche in den Daten ─────────────────────────────────────────────────────

function universum() {
  return (WISSEN.latest && WISSEN.latest.universum) || [];
}

function ideenListe() {
  return (WISSEN.latest && WISSEN.latest.ideen) || [];
}

/** Enthaelt die Frage dieses Wort? Kurze Woerter nur als ganzes Wort —
 *  sonst faende "r" jede Frage und "pe" jedes "Kompetenz". */
function enthaelt(frageNorm, wort) {
  if (wort.length >= 4) return frageNorm.indexOf(wort) >= 0;
  return (' ' + frageNorm + ' ').indexOf(' ' + wort + ' ') >= 0;
}

/** Den gemeinten Titel finden. Kuerzel schlagen Namen, Namen schlagen
 *  nichts. */
function findeTitel(roh, frageNorm) {
  const alle = universum();
  if (!alle.length) return null;

  const nachKuerzel = {};
  alle.forEach((e) => { nachKuerzel[String(e.symbol).toUpperCase()] = e; });

  // 1. Grossgeschrieben in der Frage — das ist eindeutig gemeint.
  const gross = String(roh).match(/\b[A-Z][A-Z0-9.]{0,5}\b/g) || [];
  for (const wort of gross) {
    if (nachKuerzel[wort]) return { eintrag: nachKuerzel[wort], punkte: 120 };
  }

  // 2. Kleingeschrieben, ab drei Zeichen und kein deutsches Wort.
  const worte = frageNorm.split(' ');
  for (const wort of worte) {
    if (wort.length < 3 || KEIN_KUERZEL.has(wort)) continue;
    const treffer = nachKuerzel[wort.toUpperCase()];
    if (treffer) return { eintrag: treffer, punkte: 100 };
    const alias = KUERZEL_ALIAS[wort];
    if (alias && nachKuerzel[alias]) {
      return { eintrag: nachKuerzel[alias], punkte: 100 };
    }
  }

  // 3. Ueber den Firmennamen — als ganzes Wort. Als Teilzeichenkette steckt
  //    "dell" in "sprachmodell" und "ford" in "gefordert": die Frage nach dem
  //    Sprachmodell landete so bei Dell Technologies.
  let beste = null;
  const gefragt = new Set(worte);
  alle.forEach((e) => {
    schlicht(e.name).split(' ').forEach((teil) => {
      if (teil.length < 4 || KEIN_NAME.has(teil)
          || KEIN_KUERZEL.has(teil)) return;
      if (!gefragt.has(teil)) return;
      const punkte = 70 + teil.length * 2;
      if (!beste || punkte > beste.punkte) beste = { eintrag: e, punkte };
    });
  });
  return beste;
}

// ── Antworten ──────────────────────────────────────────────────────────────

function paarBlock(paare) {
  const gefuellt = paare.filter((p) => p[1] !== null && p[1] !== undefined
                                       && p[1] !== '' && p[1] !== '—');
  if (!gefuellt.length) return '';
  return `<div class="paare antwort-paare">${gefuellt.map(([marke, wert]) => `
    <div class="paar">
      <span class="marke">${esc(marke)}</span>
      <span class="zahl">${wert}</span>
    </div>`).join('')}</div>`;
}

function antwortTitel(e) {
  const alle = universum();
  const bewertet = alle
    .filter((x) => x.score !== null && x.score !== undefined)
    .slice()
    .sort((a, b) => b.score - a.score);
  const rang = bewertet.findIndex((x) => x.symbol === e.symbol) + 1;

  const ideen = ideenListe();
  const idee = ideen.filter((i) => i.symbol === e.symbol)[0] || null;

  const richtung = (e.upside_pct || 0) >= 0 ? 'auf' : 'ab';
  // Erst faerben, wenn es etwas zu faerben gibt: ein <span> um einen
  // Gedankenstrich rutscht sonst durch den Leerfilter und die Karte zeigt
  // "Ziel —" statt die Zeile wegzulassen.
  const gefaerbt = (wert, klasse) =>
    wert === null || wert === undefined ? null
      : `<span class="${klasse}">${wert}</span>`;
  const paare = paarBlock([
    ['Kurs', e.price === null || e.price === undefined ? null : zahl(e.price)],
    ['Ziel', gefaerbt(e.target === null || e.target === undefined
      ? null : zahl(e.target), 'auf')],
    ['Potenzial', gefaerbt(e.upside_pct === null || e.upside_pct === undefined
      ? null : prozent(e.upside_pct, 1, true), richtung)],
    ['Stop', gefaerbt(e.stop === null || e.stop === undefined
      ? null : zahl(e.stop), 'ab')],
    ['CRV', e.reward_risk === null || e.reward_risk === undefined
      ? null : zahl(e.reward_risk, 2)],
    ['Score', e.score === null || e.score === undefined
      ? null : zahl(e.score, 3)],
    ['Ziel erreicht', e.p_ziel === null || e.p_ziel === undefined ? null : anteil(e.p_ziel)],
    ['Basiserwartung', e.basis_erwartung_r === null || e.basis_erwartung_r === undefined
      ? null : rWert(e.basis_erwartung_r)],
  ]);

  const teile = [];
  const wo = rang ? ` Unter den ${zahl(bewertet.length, 0)} bewerteten Titeln
                     steht er auf Rang ${zahl(rang, 0)}.` : '';

  if (idee) {
    const anzahl = ideen.length;
    teile.push(`<p><strong>${esc(e.symbol)} steht heute auf der Liste</strong> —
      eine von ${zahl(anzahl, 0)} Ideen.${wo} Gekauft wird zur nächsten
      Eröffnung.</p>`);
    if (idee.llm && idee.llm.these) {
      teile.push(`<p class="these">${esc(idee.llm.these)}</p>`);
    }
    teile.push(`<p class="antwort-verweis"><a href="#idee-${esc(e.symbol)}">Zur
      Karte mit der vollständigen Herleitung</a></p>`);
  } else if (e.tradeable === false) {
    teile.push(`<p><strong>${esc(e.symbol)} ist heute keine Idee.</strong>
      Grund: ${esc(e.reject_reason || 'nicht angegeben')}.${wo}</p>`);
  } else if (e.tradeable === true) {
    teile.push(`<p><strong>${esc(e.symbol)} hätte heute alle Hürden genommen,
      steht aber nicht auf der Liste.</strong>${wo} Genommen werden die besten
      nach Score, und der reichte nicht so weit. Möglich ist auch die
      Branchengrenze: aus einer Branche kommen nicht beliebig viele Titel.</p>`);
  } else {
    const abd = e.coverage === null || e.coverage === undefined
      ? null : anteil(e.coverage);
    teile.push(`<p><strong>${esc(e.symbol)} wurde bewertet, aber nicht als Idee
      geprüft.</strong> ${abd ? `Die Datenabdeckung liegt bei ${abd} der sieben
      Komponenten — unter der Schwelle wird ein Titel gar nicht erst
      zugelassen.` : 'Es fehlten Daten für die Prüfung.'}${wo}</p>`);
  }

  return {
    titel: `${e.symbol}${e.name ? ' · ' + e.name : ''}${e.sector ? ' · ' + e.sector : ''}`,
    html: teile.join('') + paare,
  };
}

function antwortUnbekannt(roh) {
  const d = WISSEN.latest;
  // Vor dem ersten Lauf ist NICHTS bekannt. Dann "gehoert nicht zum
  // Universum" zu antworten waere eine Behauptung ueber den Titel, wo in
  // Wahrheit nur die Analyse fehlt.
  if (!d || !(d.universum || []).length) {
    return {
      titel: 'Noch keine Analyse',
      html: `<p>Zu «${esc(String(roh).slice(0, 60))}» kann ich nichts sagen:
        Es liegt noch keine Tagesanalyse vor, aus der ich antworten könnte.
        Der Vorbörsenlauf startet werktags um 14:15 Schweizer Zeit.</p>
        <p class="notiz">Fachwörter der Seite lassen sich trotzdem
        nachschlagen — etwa «was heisst CRV».</p>`,
    };
  }
  const geprueft = d.scored ? zahl(d.scored, 0) : null;
  const raus = d.excluded ? zahl(d.excluded, 0) : null;
  return {
    titel: 'Nicht im geprüften Universum',
    html: `<p>Zu «${esc(String(roh).slice(0, 60))}» steht in den Daten dieser
      Seite nichts.${geprueft ? ` Geprüft werden die ${geprueft} Titel aus
      S&amp;P 500 und Nasdaq 100, für die genug Kurshistorie vorliegt.` : ''}
      ${raus ? ` Weitere ${raus} fielen vorher an einem harten Ausschluss
      heraus: Kurs unter 5 USD, zu geringes Handelsvolumen oder Lücken in den
      Kursdaten.` : ''}</p>
      <p class="notiz">Ein Titel ausserhalb dieser beiden Indizes wird hier gar
      nicht analysiert.</p>`,
  };
}

function antwortBranche(branche) {
  const alle = universum().filter((e) => e.sector === branche);
  if (!alle.length) return null;
  const bewertet = alle
    .filter((e) => e.score !== null && e.score !== undefined)
    .slice()
    .sort((a, b) => b.score - a.score);
  const handelbar = alle.filter((e) => e.tradeable === true).length;
  const ideen = ideenListe().filter((i) => i.sector === branche);

  const zeilen = bewertet.slice(0, 6).map((e) => `
    <tr>
      <td class="zahl">${esc(e.symbol)}</td>
      <td class="zahl">${zahl(e.score, 3)}</td>
      <td class="zahl ${(e.upside_pct || 0) >= 0 ? 'auf' : 'ab'}">${prozent(e.upside_pct, 1, true)}</td>
      <td class="links">${esc(e.name || '')}</td>
    </tr>`).join('');

  const w = WISSEN.gewichte || {};
  const mult = (w.sector_multiplier || {})[branche];
  const gelernt = mult === undefined || mult === null ? '' : `
    <p class="notiz">Gelernter Multiplikator auf den Score dieser Branche:
       ${zahl(mult, 3)}. ${mult > 1 ? 'Über 1 heisst: Trades dieser Branche '
       + 'liefen besser als alle übrigen zusammen.'
       : mult < 1 ? 'Unter 1 heisst: Trades dieser Branche liefen schlechter '
       + 'als alle übrigen zusammen.' : 'Genau 1 heisst: noch kein '
       + 'gemessener Unterschied.'}</p>`;

  return {
    titel: `Branche ${branche}`,
    html: `<p>${zahl(alle.length, 0)} Titel im Universum, davon
      ${zahl(bewertet.length, 0)} bewertet und ${zahl(handelbar, 0)} heute
      handelbar. ${ideen.length === 1 ? 'Auf der Ideenliste steht einer davon.'
        : ideen.length === 0 ? 'Auf der Ideenliste steht keiner davon.'
        : `Auf der Ideenliste stehen ${zahl(ideen.length, 0)} davon.`}</p>
      ${zeilen ? `<div class="tabelle-huelle"><table>
        <thead><tr><th>Kürzel</th><th>Score</th><th>Potenzial</th>
          <th class="links">Name</th></tr></thead>
        <tbody>${zeilen}</tbody></table></div>` : ''}
      ${gelernt}`,
  };
}

const RANGLISTEN = {
  score: { feld: 'score', name: 'Höchster Score', stellen: 3,
           format: (e) => zahl(e.score, 3) },
  upside: { feld: 'upside_pct', name: 'Grösstes Potenzial', stellen: 1,
            format: (e) => prozent(e.upside_pct, 1, true) },
  crv: { feld: 'reward_risk', name: 'Bestes Chance-Risiko-Verhältnis',
         stellen: 2, format: (e) => zahl(e.reward_risk, 2) },
  treffer: { feld: 'p_ziel', name: 'Höchste gemessene Trefferquote',
             stellen: 0, format: (e) => anteil(e.p_ziel) },
};

function antwortRangliste(art) {
  const def = RANGLISTEN[art];
  const alle = universum().filter((e) => {
    const v = e[def.feld];
    return v !== null && v !== undefined;
  });
  if (!alle.length) return null;
  const sortiert = alle.slice().sort((a, b) => b[def.feld] - a[def.feld]);
  const zeilen = sortiert.slice(0, 8).map((e, i) => `
    <tr>
      <td class="zahl leise">${i + 1}</td>
      <td class="zahl">${esc(e.symbol)}</td>
      <td class="zahl">${def.format(e)}</td>
      <td class="links">${esc(e.name || '')}</td>
      <td class="zahl leise">${e.tradeable === true ? 'handelbar' : '—'}</td>
    </tr>`).join('');

  return {
    titel: def.name,
    html: `<div class="tabelle-huelle"><table>
        <thead><tr><th>#</th><th>Kürzel</th>
          <th>${esc(def.name.split(' ').slice(1).join(' ') || 'Wert')}</th>
          <th class="links">Name</th><th>Status</th></tr></thead>
        <tbody>${zeilen}</tbody></table></div>
      <p class="notiz">Aus allen ${zahl(alle.length, 0)} bewerteten Titeln.
        «Handelbar» heisst: alle drei Hürden genommen — es heisst nicht, dass
        der Titel auf der Ideenliste steht, dorthin kommen nur die besten nach
        Score.</p>`,
  };
}

function antwortDepots() {
  const eq = WISSEN.equity;
  if (!eq || !eq.statistik || !eq.statistik.ki) {
    return {
      titel: 'Depotvergleich',
      html: `<p>Noch keine Handelshistorie. Beide Depots starten mit
        ${zahl(100000, 0)} USD; sobald der erste Abrechnungslauf durch ist,
        stehen hier Rendite, Trefferquote und Erwartung je Trade.</p>`,
    };
  }
  const a = eq.statistik.ki, z = eq.statistik.zufall || {};
  const start = eq.start_capital || 100000;
  const spy = eq.spy && eq.spy.length
    ? (eq.spy[eq.spy.length - 1].equity / start - 1) * 100 : null;

  return {
    titel: 'Schlägt die Analyse den Zufall?',
    html: `${paarBlock([
      ['Analyse', a.return_pct === null || a.return_pct === undefined ? null
        : `<span class="${a.return_pct >= 0 ? 'auf' : 'ab'}">${prozent(a.return_pct, 2, true)}</span>`],
      ['Zufall', z.return_pct === undefined ? null
        : `<span class="${z.return_pct >= 0 ? 'auf' : 'ab'}">${prozent(z.return_pct, 2, true)}</span>`],
      ['SPY', spy === null ? null
        : `<span class="${spy >= 0 ? 'auf' : 'ab'}">${prozent(spy, 2, true)}</span>`],
      ['Trades Analyse', zahl(a.trades, 0)],
      ['Trefferquote', a.win_rate === null ? null : prozent(a.win_rate)],
      ['Erwartung je Trade', rWert(a.expectancy_r)],
      ['Profitfaktor', a.profit_factor === null ? null : zahl(a.profit_factor, 2)],
      ['Grösster Rückgang', a.max_drawdown_pct === null ? null
        : `<span class="ab">${prozent(a.max_drawdown_pct, 2)}</span>`],
    ])}
    <p>${esc(vorsprungText(eq.statistik) || 'Der Vergleich steht oben im '
      + 'Abschnitt zum Depotvergleich.')}</p>`,
  };
}

function antwortLernen() {
  const w = WISSEN.gewichte;
  if (!w) {
    return {
      titel: 'Was das System gelernt hat',
      html: `<p>Noch nichts. Die Datei mit den gelernten Gewichten entsteht
        beim ersten Abrechnungslauf.</p>`,
    };
  }
  const verlauf = (w.history || []).filter((e) => e && e.date);
  const gesehen = w.trades_seen || 0;
  const min = (w.regeln || {}).min_trades || 20;

  if (!verlauf.length) {
    return {
      titel: 'Was das System gelernt hat',
      html: gesehen < min
        ? `<p>Noch nichts — und das ist Absicht. Verändert wird erst ab
           ${zahl(min, 0)} abgeschlossenen Trades des Analysedepots, bisher
           sind es ${zahl(gesehen, 0)}. Darunter wäre jede Anpassung Rauschen.</p>`
        : `<p>Aus ${zahl(gesehen, 0)} Trades gelernt, ohne eine einzige
           Änderung: Kein gemessener Unterschied war deutlich genug. Die
           Schrittweite richtet sich nach der Sicherheit der Messung, nicht
           nach ihrer Grösse.</p>`,
    };
  }

  // Welches Gewicht hat sich seit dem Start am weitesten bewegt? Das ist
  // die Antwort, die man eigentlich meint, wenn man "was hat es gelernt"
  // fragt — nicht die Liste aller Zahlen.
  const labels = (w.labels || {}).score || {};
  const start = (w.start || {}).score_weights || {};
  const bewegung = Object.keys(w.score_weights || {})
    .map((k) => ({ k, name: labels[k] || k,
                   von: start[k], nach: w.score_weights[k],
                   d: (w.score_weights[k] || 0) - (start[k] || 0) }))
    .filter((r) => r.von !== undefined)
    .sort((a, b) => Math.abs(b.d) - Math.abs(a.d));

  const letzte = verlauf[verlauf.length - 1];
  const zeilen = (letzte.changes || []).slice(0, 6);

  return {
    titel: 'Was das System gelernt hat',
    html: `<p>${zahl(verlauf.length, 0)} Lernschritte aus
      ${zahl(gesehen, 0)} abgeschlossenen Trades, zuletzt am
      ${datumLang(letzte.date)}.</p>
      ${bewegung.length ? `<p>Am weitesten bewegt hat sich das Gewicht der
        Komponente <strong>${esc(bewegung[0].name)}</strong>: von
        ${zahl(bewegung[0].von, 3)} auf ${zahl(bewegung[0].nach, 3)}
        (${bewegung[0].d >= 0 ? '+' : ''}${zahl(bewegung[0].d, 3)}).</p>` : ''}
      ${zeilen.length ? `<p class="marke">Der letzte Schritt</p>
        <div class="schritt">${esc(zeilen.join('\n'))}</div>` : ''}
      <p class="notiz">Achtung beim Lesen: Nach jedem Schritt wird auf Summe 1
        normiert, deshalb bewegt sich auch, was gar nicht bewertet wurde. Nur
        das Protokoll sagt, was wirklich belohnt oder bestraft wurde.</p>`,
  };
}

function antwortZustand() {
  const s = WISSEN.status;
  const d = WISSEN.latest;
  if (!s) {
    return { titel: 'Zustand',
             html: '<p>Es liegt noch kein Laufprotokoll vor.</p>' };
  }
  const name = LAUF_NAME[s.lauf] || s.lauf || 'Lauf';
  return {
    titel: 'Zustand des Systems',
    html: `${paarBlock([
      ['Letzter Lauf', esc(name)],
      ['Zeitpunkt', esc(zeitpunkt(s.letzter_lauf))],
      ['Ergebnis', esc(s.ergebnis || '—')],
      ['Dauer', s.sekunden === undefined ? null : zahl(s.sekunden, 0) + ' s'],
      ['Sprachmodell', s.sprachmodell === true ? '<span class="auf">bereit</span>'
        : s.sprachmodell === false ? '<span class="ab">ausgefallen</span>' : null],
      ['Bewertete Titel', d && d.scored ? zahl(d.scored, 0) : null],
    ])}
    <p class="notiz">Der Vorbörsenlauf startet werktags um 14:15 Schweizer
      Zeit, die Abrechnung um 00:00. Der Schalter oben auf der Seite hält beide
      an.</p>`,
  };
}

function antwortIdeen() {
  const ideen = ideenListe();
  const d = WISSEN.latest;
  if (!d) {
    return { titel: 'Ideen des Tages',
             html: '<p>Noch keine Analyse — der erste Vorbörsenlauf steht aus.</p>' };
  }
  if (!ideen.length) {
    return {
      titel: 'Ideen des Tages',
      html: `<p>Für den ${datumLang(d.date)} keine einzige Idee, die alle drei
        Hürden nimmt. Keine Auswahl ist auch eine Auswahl.</p>`,
    };
  }
  const zeilen = ideen.map((e) => `
    <tr>
      <td class="zahl"><a href="#idee-${esc(e.symbol)}">${esc(e.symbol)}</a></td>
      <td class="zahl">${zahl(e.price)}</td>
      <td class="zahl auf">${zahl(e.target)}</td>
      <td class="zahl ${(e.upside_pct || 0) >= 0 ? 'auf' : 'ab'}">${prozent(e.upside_pct, 1, true)}</td>
      <td class="links">${esc(e.name || '')}</td>
    </tr>`).join('');
  return {
    titel: `Ideen vom ${datumLang(d.date)}`,
    html: `<div class="tabelle-huelle"><table>
        <thead><tr><th>Kürzel</th><th>Kurs</th><th>Ziel</th><th>Potenzial</th>
          <th class="links">Name</th></tr></thead>
        <tbody>${zeilen}</tbody></table></div>
      <p class="notiz">Aus ${zahl(d.scored || 0, 0)} bewerteten Titeln. Alle
        werden zur nächsten Eröffnung virtuell gekauft.</p>`,
  };
}

function antwortBegriff(g) {
  return { titel: g.t, html: `<p>${g.b}</p>` };
}

function antwortHilfe(roh) {
  return {
    titel: 'Das habe ich nicht verstanden',
    html: `<p>Zu «${esc(String(roh).slice(0, 60))}» finde ich in den Daten
      dieser Seite nichts. Diese Zeile durchsucht keine Nachrichten und kennt
      keine Kurse ausserhalb der letzten Analyse — sie antwortet aus den
      Dateien, die oben dargestellt sind.</p>
      <p>Was geht: ein Kürzel oder Firmenname (<em>NVDA</em>, <em>Apple</em>),
      eine Branche (<em>Technologie</em>), eine Rangliste (<em>bestes
      CRV</em>), der Stand der Depots, was gelernt wurde — und jedes
      Fachwort der Seite (<em>was heisst Basisquote</em>).</p>`,
  };
}

// ── Die Frage auf einen Antwortgeber abbilden ──────────────────────────────

/** Alle plausiblen Antworten mit Punkten. Die beste wird gezeigt, die
 *  zweitbeste als Verweis angeboten — wer nach "CRV von AAPL" fragt, meint
 *  vielleicht das eine oder das andere. */
function kandidaten(roh) {
  const f = schlicht(roh);
  if (!f) return [];
  const aus = [];
  const zaehle = (worte) => worte.filter((wo) => enthaelt(f, wo)).length;

  const titel = findeTitel(roh, f);
  if (titel) {
    aus.push({ punkte: titel.punkte,
               kurz: titel.eintrag.symbol,
               bauen: () => antwortTitel(titel.eintrag) });
  }

  // Branche
  let branche = null;
  const branchen = {};
  universum().forEach((e) => { if (e.sector) branchen[e.sector] = true; });
  Object.keys(branchen).forEach((b) => {
    if (enthaelt(f, schlicht(b))) branche = b;
  });
  if (!branche) {
    Object.keys(BRANCHEN_ALIAS).forEach((wo) => {
      if (enthaelt(f, wo) && branchen[BRANCHEN_ALIAS[wo]]) {
        branche = BRANCHEN_ALIAS[wo];
      }
    });
  }
  if (branche) {
    const knapp = f.split(' ').length <= 2 ? 30 : 0;
    aus.push({ punkte: 65 + knapp, kurz: branche,
               bauen: () => antwortBranche(branche) });
  }

  // Rangliste — der Superlativ entscheidet, ob jemand eine Liste will oder
  // eine Erklaerung. "was ist der score" und "bester score" sind zwei Fragen.
  const superlativ = zaehle(['beste', 'besten', 'bester', 'top', 'hoechste',
                             'hoechsten', 'groesste', 'groessten', 'meiste',
                             'staerkste', 'rangliste', 'ranking']);
  if (superlativ) {
    const arten = [
      ['crv', ['crv', 'chance', 'risiko', 'reward']],
      ['upside', ['potenzial', 'upside', 'aufwaerts', 'rendite']],
      ['treffer', ['trefferquote', 'wahrscheinlich', 'treffer']],
      ['score', ['score', 'bewertung', 'aktie', 'aktien', 'titel']],
    ];
    for (const [art, worte] of arten) {
      const n = zaehle(worte);
      if (n) {
        aus.push({ punkte: 40 + superlativ * 20 + n * 10, kurz: RANGLISTEN[art].name,
                   bauen: () => antwortRangliste(art) });
        break;
      }
    }
  }

  const gruppen = [
    [['depot', 'depots', 'rendite', 'performance', 'zufall', 'schlaegt',
      'vorsprung', 'trefferquote', 'profitfaktor', 'drawdown', 'rueckgang',
      'gewinn', 'verlust', 'kurve'], 'Depotvergleich', antwortDepots],
    [['gelernt', 'lernen', 'lernkurve', 'lernschritt', 'gewicht', 'gewichte',
      'belohnt', 'bestraft', 'belohnung', 'bestrafung'],
     'Gelerntes', antwortLernen],
    [['zustand', 'status', 'laeuft', 'lief', 'pausiert', 'letzter',
      'zuletzt', 'wann', 'aktualisiert', 'sprachmodell', 'ollama',
      'aktuell'], 'Zustand', antwortZustand],
    [['idee', 'ideen', 'heute', 'vorschlag', 'vorschlaege', 'empfehlung',
      'kaufen', 'gekauft'], 'Ideen des Tages', antwortIdeen],
  ];
  gruppen.forEach(([worte, kurz, fn]) => {
    const n = zaehle(worte);
    if (n) aus.push({ punkte: 38 + n * 14, kurz, bauen: fn });
  });

  // Nachschlagewerk
  const erklaerfrage = zaehle(['was ist', 'was heisst', 'was bedeutet',
                               'erklaer', 'erklaere', 'bedeutet', 'definition',
                               'wofuer']) ? 35 : 0;
  GLOSSAR.forEach((g) => {
    const n = g.k.split(' ').filter((wo) => enthaelt(f, wo)).length;
    if (n) {
      aus.push({ punkte: 42 + n * 9 + erklaerfrage, kurz: g.t,
                 bauen: () => antwortBegriff(g) });
    }
  });

  return aus.sort((a, b) => b.punkte - a.punkte);
}

function beantworte(roh) {
  const liste = kandidaten(roh);
  if (!liste.length) {
    // Ein Kuerzel, das es nicht gibt, ist etwas anderes als eine unklare
    // Frage — und verdient eine andere Antwort.
    const w = schlicht(roh).split(' ').filter(Boolean);
    const inhalt = w.filter((x) => !KEIN_KUERZEL.has(x));
    const nachTitel = w.length <= 3 && inhalt.length >= 1 && inhalt.length <= 2;
    return { antwort: nachTitel ? antwortUnbekannt(roh) : antwortHilfe(roh),
             weitere: [], notfall: true };
  }
  let antwort = null, i = 0;
  while (i < liste.length && !antwort) {
    antwort = liste[i].bauen();       // Kann null sein, wenn Daten fehlen.
    i += 1;
  }
  if (!antwort) return { antwort: antwortHilfe(roh), weitere: [], notfall: true };

  const weitere = [];
  liste.slice(i).forEach((k) => {
    if (k.punkte >= 45 && weitere.length < 3
        && !weitere.some((x) => x.kurz === k.kurz)) {
      weitere.push(k);
    }
  });
  return { antwort, weitere };
}

// ── Frei gestellte Fragen ──────────────────────────────────────────────────

/* Die Fragezeile oben trifft acht feste Ziele: Titel, Branche, Rangliste,
 * Depot, Gelerntes, Zustand, Ideen, Fachwort. Das deckt Nachschlagefragen ab
 * und scheitert an allem, was wirklich eine Frage ist — "ist MU riskanter als
 * TSM", "warum steht ANET drin und NVDA nicht", "erklaer mir das einfacher".
 *
 * Dafuer dieser zweite Weg. Er aendert nicht, woher die Zahlen kommen: die
 * Seite sucht wie bisher heraus, worum es geht, und schickt die gefundenen
 * Zahlen zusammen mit der Frage an einen kleinen Cloudflare-Worker. Dort
 * formuliert ein Sprachmodell — mehr nicht. Es bekommt die Zahlen vorgesetzt
 * und ist angewiesen, keine eigenen zu nennen.
 *
 * Der Umweg ueber den Worker ist noetig, weil diese Seite statisch auf GitHub
 * Pages liegt: ein Schluessel in ihrem Quelltext waere ein veroeffentlichter
 * Schluessel. Der Worker haelt die Verbindung zum Modell, und weil Workers AI
 * ueber eine Bindung laeuft statt ueber einen Schluessel, gibt es dort gar
 * kein Geheimnis.
 *
 * Faellt der Worker aus oder ist das Handy offline, bleibt die Karte mit den
 * Zahlen stehen. Sie ist der verlaessliche Teil, die Prosa die Zugabe.
 */

/** Adresse des Vermittlers. Leer heisst: nur die Zahlen-Antworten, genau wie
 *  vorher. Die Seite bleibt so auch ohne Worker vollstaendig benutzbar.
 *
 *  Dass die Adresse hier offen steht, ist kein Versehen: Es gibt hinter ihr
 *  kein Geheimnis. Der Worker nimmt nur Anfragen von dieser Seite an, hat ein
 *  Anfragelimit je Adresse und gibt nichts zurueck ausser Text. */
const KI_ADRESSE = 'https://aktien-frage.kursziele.workers.dev';

const KI_FRIST = 45000;   // ms, bis eine Antwort abgebrochen wird

function kiAn() { return Boolean(KI_ADRESSE); }

// ── Die Zahlen zur Frage einsammeln ────────────────────────────────────────

/** Wie findeTitel, aber sammelnd statt entscheidend. Fuer Vergleichsfragen
 *  ("MU oder TSM") braucht es beide Titel, nicht den staerkeren Treffer. */
function findeTitelAlle(roh, frageNorm, hoechstens) {
  const alle = universum();
  if (!alle.length) return [];
  const nachKuerzel = {};
  alle.forEach((e) => { nachKuerzel[String(e.symbol).toUpperCase()] = e; });

  const aus = [], gesehen = {};
  const nimm = (e) => {
    if (!e || gesehen[e.symbol]) return;
    gesehen[e.symbol] = true;
    aus.push(e);
  };

  (String(roh).match(/\b[A-Z][A-Z0-9.]{0,5}\b/g) || [])
    .forEach((w) => nimm(nachKuerzel[w]));

  const worte = frageNorm.split(' ');
  worte.forEach((w) => {
    if (w.length < 3 || KEIN_KUERZEL.has(w)) return;
    nimm(nachKuerzel[w.toUpperCase()]);
    if (KUERZEL_ALIAS[w]) nimm(nachKuerzel[KUERZEL_ALIAS[w]]);
  });

  const gefragt = new Set(worte);
  alle.forEach((e) => {
    schlicht(e.name).split(' ').forEach((teil) => {
      if (teil.length < 4 || KEIN_NAME.has(teil) || KEIN_KUERZEL.has(teil)) return;
      if (gefragt.has(teil)) nimm(e);
    });
  });

  return aus.slice(0, hoechstens || 3);
}

/** Der schlanke Eintrag aus dem Universum hat 13 Felder, die volle Herleitung
 *  steht nur bei den Ideen und der Vorauswahl. */
function vollstaendig(symbol) {
  const d = WISSEN.latest || {};
  const such = (liste) => (liste || []).filter((x) => x.symbol === symbol)[0];
  return such(d.ideen) || such(d.vorauswahl) || such(d.universum) || null;
}

/** Zahl oder ein ehrliches "fehlt" — niemals eine stille Null. Was das Modell
 *  nicht sieht, kann es nicht behaupten. */
function fz(wert, formatierer) {
  if (wert === null || wert === undefined || Number.isNaN(wert)) return 'fehlt';
  return formatierer(wert);
}

const fUsd = (v) => zahl(v, 2) + ' USD';
const fPct = (v) => prozent(v, 1, true);
const fAnt = (v) => anteil(v, 1);
const f3 = (v) => zahl(v, 3);
const f2 = (v) => zahl(v, 2);
const f1 = (v) => zahl(v, 1);
const fR = (v) => rWert(v);
const fPz = (v) => zahl(v * 100, 1) + ' %';
const fPzS = (v) => (v > 0 ? '+' : '') + zahl(v * 100, 1) + ' %';

/** Die Glossartexte sind fuer die Seite geschrieben und tragen Auszeichnung.
 *  Das Modell soll den Inhalt sehen, nicht die Tags. */
function ohneTags(text) {
  return String(text).replace(/<[^>]+>/g, '')
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/\s+/g, ' ').trim();
}

function faktenTitel(symbol, tief) {
  const e = vollstaendig(symbol);
  if (!e) return '';
  const bewertet = universum()
    .filter((x) => x.score !== null && x.score !== undefined)
    .slice().sort((a, b) => b.score - a.score);
  const rang = bewertet.findIndex((x) => x.symbol === symbol) + 1;
  const idee = ideenListe().filter((i) => i.symbol === symbol)[0];

  const z = [];
  z.push(e.symbol + ' — ' + (e.name || 'Name fehlt')
         + ' (Branche: ' + (e.sector || 'unbekannt') + ')');
  z.push('  Heute auf der Ideenliste: ' + (idee ? 'JA' : 'NEIN')
         + (!idee && e.reject_reason ? ' (Ausschlussgrund: ' + e.reject_reason + ')' : '')
         + (!idee && !e.reject_reason && e.tradeable === true
            ? ' (alle Hürden genommen, aber der Score reichte nicht unter die'
              + ' besten; möglich ist auch die Branchengrenze)' : ''));
  z.push('  Kurs ' + fz(e.price, fUsd) + ', Kursziel ' + fz(e.target, fUsd)
         + ' (' + fz(e.upside_pct, fPct) + '), Stop ' + fz(e.stop, fUsd));
  z.push('  Chance-Risiko-Verhältnis ' + fz(e.reward_risk, f2)
         + ', Score ' + fz(e.score, f3)
         + (rang ? ', Rang ' + rang + ' von ' + bewertet.length
                   + ' bewerteten Titeln' : '')
         + ', Datenabdeckung ' + fz(e.coverage, fAnt));
  z.push('  Gemessene Wahrscheinlichkeit, dass zuerst das Ziel berührt wird: '
         + fz(e.p_ziel, fAnt) + '; Erwartung aus der Basisquote '
         + fz(e.basis_erwartung_r, fR));

  const l = e.llm || {};
  if (l.these) z.push('  These des Sprachmodells: ' + l.these);
  if ((l.katalysatoren || []).length) {
    z.push('  Katalysatoren: ' + l.katalysatoren.join('; '));
  }
  if ((l.risiken || []).length) z.push('  Risiken: ' + l.risiken.join('; '));
  if (l.sentiment !== undefined && l.sentiment !== null) {
    z.push('  News-Sentiment ' + zahl(l.sentiment, 2) + ' aus '
           + (l.news_count || 0) + ' Meldungen');
  }

  if (!tief) return z.join('\n');

  const t = e.targets || {}, s = e.snapshot || {}, sc = e.scoring || {};
  if (t.band_low !== undefined) {
    z.push('  Erwartungsbereich (1 Sigma) ' + fz(t.band_low, fUsd) + ' bis '
           + fz(t.band_high, fUsd) + '; Streuung zwischen den Methoden '
           + fz(t.method_spread, fUsd));
  }
  (t.methods || []).forEach((m) => {
    z.push('  Kursziel-Methode ' + m.label + ' (' + m.role + '): '
           + fz(m.value, fUsd));
    (m.steps || []).forEach((sr) => z.push('      ' + sr));
  });
  (t.blend_steps || []).forEach((sr) => z.push('    ' + sr));
  (t.stop_steps || []).forEach((sr) => z.push('    ' + sr));
  (t.probability_steps || []).forEach((sr) => z.push('    ' + sr));
  if (t.analyst_target_12m) {
    z.push('  Analysten: 12-Monats-Ziel ' + fz(t.analyst_target_12m, fUsd)
           + ' aus ' + (t.analyst_count || 0) + ' Schätzungen, Konsens '
           + (t.analyst_recommendation || 'unbekannt')
           + '. Wirkt nur als Neigung (' + fz(t.analyst_tilt, f2)
           + '), nicht als Niveau.');
  }
  (sc.components || []).forEach((c) => {
    z.push('  Score-Komponente ' + c.label + ': ' + fz(c.score, f2)
           + ' bei Gewicht ' + fz(c.weight, f2)
           + ((c.reasons || []).length ? ' — ' + c.reasons.join('; ') : ''));
  });
  (sc.penalties || []).forEach((p) => z.push('  Abzug: ' + p));
  if (s.close !== undefined) {
    z.push('  Technik am ' + datumLang(s.date) + ': ATR(14) ' + fz(s.atr, fUsd)
           + ' (' + fz(s.atr_pct, fPz) + ' vom Kurs)'
           + ', RSI ' + fz(s.rsi, f1) + ', ADX ' + fz(s.adx, f1)
           + ', EMA21 ' + fz(s.ema21, fUsd) + ', SMA200 ' + fz(s.sma200, fUsd)
           + ', 63 Tage ' + fz(s.chg_63d, fPzS)
           + ', relative Stärke zu SPY ' + fz(s.rel_strength_63d, fPzS));
  }
  return z.join('\n');
}

function faktenBranche(branche) {
  const alle = universum().filter((e) => e.sector === branche);
  if (!alle.length) return '';
  const bewertet = alle.filter((e) => e.score !== null && e.score !== undefined)
                       .slice().sort((a, b) => b.score - a.score);
  const handelbar = alle.filter((e) => e.tradeable === true).length;
  const ideen = ideenListe().filter((i) => i.sector === branche);
  const w = WISSEN.gewichte || {};
  const mult = (w.sector_multiplier || {})[branche];
  const pe = ((WISSEN.latest || {}).sector_median_pe || {})[branche];

  const z = ['Branche ' + branche + ': ' + alle.length + ' Titel, davon '
             + bewertet.length + ' bewertet, ' + handelbar + ' heute handelbar, '
             + ideen.length + ' auf der Ideenliste.'];
  if (pe !== undefined) {
    z.push('  Median-Forward-KGV der Branche: ' + zahl(pe, 2));
  }
  if (mult !== undefined && mult !== null) {
    z.push('  Gelernter Score-Multiplikator: ' + zahl(mult, 3)
           + ' (über 1 = Trades dieser Branche liefen besser als alle übrigen)');
  }
  bewertet.slice(0, 8).forEach((e, i) => {
    z.push('  ' + (i + 1) + '. ' + e.symbol + ' ' + (e.name || '')
           + ' — Score ' + fz(e.score, f3)
           + ', Potenzial ' + fz(e.upside_pct, fPct)
           + ', CRV ' + fz(e.reward_risk, f2));
  });
  return z.join('\n');
}

function faktenRangliste(art) {
  const def = RANGLISTEN[art];
  const alle = universum().filter((e) => {
    const v = e[def.feld];
    return v !== null && v !== undefined;
  });
  if (!alle.length) return '';
  const sortiert = alle.slice().sort((a, b) => b[def.feld] - a[def.feld]);
  const z = ['Rangliste «' + def.name + '» aus allen ' + alle.length
             + ' bewerteten Titeln:'];
  sortiert.slice(0, 12).forEach((e, i) => {
    z.push('  ' + (i + 1) + '. ' + e.symbol + ' ' + (e.name || '')
           + ' — ' + def.format(e) + ', Score ' + fz(e.score, f3)
           + ', ' + (e.tradeable === true ? 'handelbar' : 'nicht handelbar'));
  });
  return z.join('\n');
}

/** Was immer mitgeht: Zustand, Marktphase, die Ideen, die Depots, das
 *  Gelernte. Ohne das kann das Modell auch die einfachste Rueckfrage nicht
 *  beantworten. */
function faktenGrundlage() {
  const d = WISSEN.latest, st = WISSEN.status;
  const z = [];

  z.push('## Zustand');
  if (st) {
    z.push('Letzter Lauf: ' + zeitpunkt(st.letzter_lauf)
           + ' (' + (LAUF_NAME[st.lauf] || st.lauf || 'unbekannt')
           + '), Ergebnis ' + (st.ergebnis || 'unbekannt')
           + ', Dauer ' + fz(st.sekunden, (v) => zahl(v, 0) + ' s') + '.');
    z.push('Das Sprachmodell fuer die Thesen lief bei diesem Lauf: '
           + (st.sprachmodell ? 'ja' : 'nein') + '.');
    z.push('Die Simulation ist '
           + ((st.steuerung || {}).paused ? 'PAUSIERT' : 'aktiv') + '.');
  } else {
    z.push('Kein Lauf verzeichnet.');
  }

  if (!d) {
    z.push('Es liegt noch keine Analyse vor. Es gibt keine Kurse, keine Ziele'
           + ' und keine Ideen, über die sich etwas sagen liesse.');
    return z.join('\n');
  }

  z.push('');
  z.push('## Analyse');
  z.push('Stand ' + datumLang(d.date) + ', erzeugt ' + zeitpunkt(d.generated_at) + '.');
  z.push('Universum ' + (d.universe_size || 0) + ' Titel, davon '
         + (d.scored || 0) + ' bewertet und ' + (d.excluded || 0)
         + ' ausgeschlossen.');
  z.push('Gekauft werden pro Tag ' + (d.picks_per_day || 0) + ' Ideen, je '
         + fz(d.position_pct, fPz) + ' des Depots.');
  const r = d.regime || {};
  z.push('Marktphase: ' + (r.benchmark || 'SPY') + ' bei '
         + fz(r.benchmark_close, fUsd) + ', 200-Tage-Linie '
         + fz(r.benchmark_sma200, fUsd) + ', Trend ' + (r.trend || 'unbekannt')
         + ', Abstand ' + fz(r.gap_to_sma200, fPzS)
         + ', VIX ' + fz(r.vix, f2) + ' (' + (r.vix_level || '?') + ')'
         + ', 21 Tage ' + fz(r.chg_21d, fPzS) + '.');
  const bq = d.basisquote_gesamt || {};
  if (bq.n) {
    z.push('Basisquote über ' + zahl(bq.n, 0) + ' historische Beobachtungen'
           + ' bei 15 Handelstagen Horizont: Ziel zuerst ' + fz(bq.p_ziel, fAnt)
           + ', Stop zuerst ' + fz(bq.p_stop, fAnt)
           + ', Zeitablauf ' + fz(bq.p_zeit, fAnt)
           + ', Erwartung ' + fz(bq.erwartung_r, fR) + '.');
  }
  if ((d.kalibrierung || {}).text) z.push(d.kalibrierung.text);

  const ideen = ideenListe();
  z.push('');
  z.push('## Die ' + ideen.length + ' Ideen von heute');
  if (!ideen.length) z.push('Heute keine.');
  ideen.forEach((i, n) => {
    z.push((n + 1) + '. ' + faktenTitel(i.symbol, false));
  });

  z.push('');
  z.push('## Depots');
  const eq = WISSEN.equity;
  const stat = (eq && eq.statistik) || d.statistik || null;
  if (!stat || !stat.ki || !stat.ki.trades) {
    z.push('Noch keine abgeschlossenen Trades. Beide Depots stehen beim'
           + ' Startkapital von 100 000 USD; Rendite, Trefferquote und'
           + ' Erwartung entstehen erst nach dem ersten Abrechnungslauf.');
  } else {
    const start = (eq && eq.start_capital) || 100000;
    ['ki', 'zufall'].forEach((k) => {
      const a = stat[k] || {};
      z.push((a.label || k) + ': Rendite '
             + fz(a.return_pct, (v) => prozent(v, 2, true))
             + ', ' + zahl(a.trades || 0, 0) + ' Trades'
             + ', Trefferquote ' + fz(a.win_rate, (v) => prozent(v, 1))
             + ', Erwartung je Trade ' + fz(a.expectancy_r, fR)
             + ', Profitfaktor ' + fz(a.profit_factor, f2)
             + ', grösster Rückgang ' + fz(a.max_drawdown_pct, (v) => prozent(v, 2))
             + ', offene Positionen ' + zahl(a.open_positions || 0, 0) + '.');
    });
    if (eq && eq.spy && eq.spy.length) {
      const spy = (eq.spy[eq.spy.length - 1].equity / start - 1) * 100;
      z.push('SPY buy-and-hold im selben Zeitraum: ' + prozent(spy, 2, true) + '.');
    }
  }

  z.push('');
  z.push('## Gelerntes');
  const w = WISSEN.gewichte;
  if (!w) {
    z.push('Noch keine Lerndatei — sie entsteht beim ersten Abrechnungslauf.');
  } else {
    const verlauf = (w.history || []).filter((x) => x && x.date);
    const min = (w.regeln || {}).min_trades || 20;
    z.push(verlauf.length + ' Lernschritte aus ' + zahl(w.trades_seen || 0, 0)
           + ' abgeschlossenen Trades. Verändert wird erst ab ' + min
           + ' Trades, darunter wäre jede Anpassung Rauschen.');
    const labels = (w.labels || {}).score || {};
    const start = (w.start || {}).score_weights || {};
    Object.keys(w.score_weights || {}).forEach((k) => {
      z.push('  Gewicht ' + (labels[k] || k) + ': Start ' + fz(start[k], f3)
             + ' -> jetzt ' + fz(w.score_weights[k], f3));
    });
    if (verlauf.length) {
      const letzte = verlauf[verlauf.length - 1];
      z.push('  Letzter Schritt am ' + datumLang(letzte.date) + ': '
             + ((letzte.changes || []).slice(0, 6).join('; ') || 'ohne Änderung'));
    }
  }
  return z.join('\n');
}

const RANG_WORTE = {
  crv: ['crv', 'chance', 'risiko', 'reward'],
  upside: ['potenzial', 'upside', 'rendite'],
  treffer: ['trefferquote', 'treffer', 'wahrscheinlich'],
  score: ['score', 'beste', 'besten', 'bester', 'top', 'rangliste', 'ranking'],
};

/** Alles, was das Modell fuer diese eine Frage braucht — und nichts sonst.
 *  528 Titel mitzuschicken waere weder bezahlbar noch hilfreich. */
function faktenText(roh) {
  const teile = [faktenGrundlage()];
  const f = schlicht(roh);
  const nach = [];

  findeTitelAlle(roh, f, 3).forEach((e) => {
    const t = faktenTitel(e.symbol, true);
    if (t) nach.push(t);
  });

  const branchen = {};
  universum().forEach((e) => { if (e.sector) branchen[e.sector] = true; });
  const genommen = {};
  const nimmBranche = (b) => {
    if (!b || genommen[b] || !branchen[b]) return;
    const t = faktenBranche(b);
    if (t) { genommen[b] = true; nach.push(t); }
  };
  Object.keys(branchen).forEach((b) => {
    if (enthaelt(f, schlicht(b))) nimmBranche(b);
  });
  Object.keys(BRANCHEN_ALIAS).forEach((wo) => {
    if (enthaelt(f, wo)) nimmBranche(BRANCHEN_ALIAS[wo]);
  });

  Object.keys(RANG_WORTE).forEach((art) => {
    if (RANG_WORTE[art].some((wo) => enthaelt(f, wo))) {
      const t = faktenRangliste(art);
      if (t) nach.push(t);
    }
  });

  GLOSSAR.forEach((g) => {
    if (g.k.split(' ').some((wo) => enthaelt(f, wo))) {
      nach.push('Fachwort ' + ohneTags(g.t) + ': ' + ohneTags(g.b));
    }
  });

  if (nach.length) {
    teile.push('');
    teile.push('## Ausdrücklich zur Frage nachgeschlagen');
    teile.push(nach.join('\n\n'));
  }
  return teile.join('\n');
}

// ── Den Vermittler fragen ──────────────────────────────────────────────────

/** Ruft `aufText` mit jedem Stueck, sobald es eintrifft. Zwei Antwortformen,
 *  weil Workers AI je nach Modell mal `response` liefert und mal die
 *  OpenAI-Form mit choices/delta. */
async function frageAnKi(frage, fakten, aufText, abbruch) {
  const antwort = await fetch(KI_ADRESSE.replace(/\/+$/, '') + '/frage', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ frage: frage, fakten: fakten }),
    signal: abbruch.signal,
  });

  if (!antwort.ok) {
    let grund = 'Der Vermittler antwortet mit ' + antwort.status + '.';
    try {
      const j = await antwort.json();
      if (j && j.fehler) grund = j.fehler;
    } catch (e) { /* dann eben die nackte Nummer */ }
    throw new Error(grund);
  }
  if (!antwort.body) throw new Error('Keine Antwort erhalten.');

  const leser = antwort.body.getReader();
  const dekoder = new TextDecoder();
  let puffer = '', etwas = false, sauber = false;

  for (;;) {
    const gelesen = await leser.read();
    if (gelesen.done) break;
    puffer += dekoder.decode(gelesen.value, { stream: true });
    const zeilen = puffer.split('\n');
    puffer = zeilen.pop();
    zeilen.forEach((zeile) => {
      if (zeile.indexOf('data:') !== 0) return;
      const roh = zeile.slice(5).trim();
      if (roh === '[DONE]') { sauber = true; return; }
      if (!roh) return;
      let stueck = '';
      try {
        const j = JSON.parse(roh);
        // Das Token "0" kommt als JSON-Zahl 0 an, nicht als Zeichenkette.
        // Weil 0 falsch-wertig ist, verschluckt jedes `|| ''` und jedes
        // `if (stueck)` genau diese Token: aus "100 %" wird "1 %", aus
        // "212'350" wird "212'35". Auf einer Seite mit Kurszielen ist das
        // der schlimmste denkbare Fehler, weil die Zahl richtig aussieht.
        // Deshalb hier ueberall gegen null/undefined pruefen, nie auf
        // Wahrheitswert.
        let roher = j.response;
        if (roher === undefined || roher === null) {
          roher = (((j.choices || [])[0] || {}).delta || {}).content;
        }
        stueck = roher === undefined || roher === null ? '' : String(roher);
      } catch (e) { return; }
      if (stueck !== '') { etwas = true; aufText(stueck); }
    });
  }
  if (!etwas) throw new Error('Das Modell hat nichts zurueckgegeben.');
  return sauber;
}

/** Freitext des Modells in schlichtes HTML. Absaetze an Leerzeilen,
 *  Aufzaehlung an fuehrenden Strichen — mehr braucht es nicht, und mehr
 *  waere eine Einladung, fremdes HTML durchzulassen. */
function kiHtml(text) {
  return String(text).split(/\n{2,}/).map((b) => {
    const zeilen = b.split('\n').filter((zi) => zi.trim());
    if (!zeilen.length) return '';
    const punkte = zeilen.filter((zi) => /^\s*[-*•]\s+/.test(zi));
    if (punkte.length && punkte.length === zeilen.length) {
      return '<ul>' + zeilen.map((zi) =>
        '<li>' + esc(zi.replace(/^\s*[-*•]\s+/, '')) + '</li>').join('')
        + '</ul>';
    }
    return '<p>' + esc(zeilen.join(' ')) + '</p>';
  }).join('');
}

// ── Die Zeile selbst ───────────────────────────────────────────────────────

const BEISPIELE = ['NVDA', 'Was heisst CRV?', 'Bestes Chance-Risiko',
                   'Branche Technologie', 'Was hat das System gelernt?'];

/* Mit Vermittler lohnen sich ganze Fragen. Ohne ihn waeren sie eine
 * Einladung, die die Zeile nicht einloesen kann — dann bleiben Stichwoerter
 * das ehrlichere Angebot. */
const BEISPIELE_KI = ['Warum ist NVDA heute nicht dabei?',
                      'Ist MU riskanter als TSM?',
                      'Erklär mir die erste Idee einfach',
                      'Schlägt die Analyse den Zufall?',
                      'Was heisst CRV?'];

let vorschlagIndex = -1;

/** Vorschlaege waehrend des Tippens: Titel zuerst, dann Fachwoerter.
 *  Bei 528 Titeln ist die Zeile ohne das eine Ratestunde. */
function vorschlaegeFuer(text) {
  const f = schlicht(text);
  if (f.length < 2) return [];
  const titel = [];
  universum().forEach((e) => {
    const sym = schlicht(e.symbol), nam = schlicht(e.name);
    let rang = -1;
    if (sym === f) rang = 0;
    else if (sym.indexOf(f) === 0) rang = 1;
    else if (nam.indexOf(f) === 0) rang = 2;
    else if (nam.split(' ').some((wo) => wo.indexOf(f) === 0)) rang = 3;
    if (rang >= 0) titel.push({ rang, text: e.symbol,
                                zusatz: e.name || '', art: 'Titel' });
  });

  const begriffe = [];
  GLOSSAR.forEach((g) => {
    const woerter = schlicht(g.t).split(' ').concat(g.k.split(' '));
    if (woerter.some((wo) => wo.indexOf(f) === 0)) {
      begriffe.push({ rang: 0, text: g.t, zusatz: '', art: 'Begriff' });
    }
  });

  // Getrennt begrenzen und dann zusammenlegen. Bei einer gemeinsamen Grenze
  // draengen 528 Titel jedes Fachwort aus der Liste: "cr" zeigte sieben
  // Kuerzel und nicht das CRV, nach dem gefragt war.
  titel.sort((a, b) => a.rang - b.rang);
  return titel.slice(0, begriffe.length ? 5 : 7)
              .concat(begriffe.slice(0, 3))
              .slice(0, 7);
}

function zeigeVorschlaege(liste) {
  const feld = $('#frage-vorschlaege');
  vorschlagIndex = -1;
  if (!liste.length) {
    feld.hidden = true;
    feld.innerHTML = '';
    $('#frage-feld').setAttribute('aria-expanded', 'false');
    return;
  }
  feld.hidden = false;
  feld.innerHTML = liste.map((v, i) => `
    <button type="button" class="vorschlag" role="option" id="vorschlag-${i}"
            data-wert="${esc(v.text)}">
      <span class="${v.art === 'Titel' ? 'zahl kuerzel' : 'begriff'}">${esc(v.text)}</span>
      ${v.zusatz ? `<span class="leise">${esc(v.zusatz)}</span>` : ''}
      <span class="marke">${esc(v.art)}</span>
    </button>`).join('');
  $('#frage-feld').setAttribute('aria-expanded', 'true');
}

function markiereVorschlag(richtung) {
  const knoepfe = Array.prototype.slice.call(
    document.querySelectorAll('#frage-vorschlaege .vorschlag'));
  if (!knoepfe.length) return;
  vorschlagIndex = (vorschlagIndex + richtung + knoepfe.length + 2)
                   % (knoepfe.length + 1) - 1;
  knoepfe.forEach((k, i) => {
    k.setAttribute('aria-selected', i === vorschlagIndex ? 'true' : 'false');
  });
  if (vorschlagIndex >= 0) {
    $('#frage-feld').setAttribute('aria-activedescendant',
                                  'vorschlag-' + vorschlagIndex);
    knoepfe[vorschlagIndex].scrollIntoView({ block: 'nearest' });
  } else {
    $('#frage-feld').removeAttribute('aria-activedescendant');
  }
}

let kiAbbruch = null;

/** Die Prosa des Modells nachtragen, waehrend sie eintrifft. Bricht sie ab,
 *  tritt die Zahlenkarte an ihre Stelle — nie beides weg. */
async function kiStarten(roh, ersatz) {
  const ziel = $('#ki-text');
  if (!ziel) return;
  const abbruch = new AbortController();
  kiAbbruch = abbruch;
  const uhr = setTimeout(() => abbruch.abort(), KI_FRIST);
  let text = '';
  const halb = `<p class="notiz ki-fehler">Die Antwort brach vorzeitig ab —
    was hier steht, ist unvollständig.</p>`;

  try {
    const sauber = await frageAnKi(roh, faktenText(roh), (stueck) => {
      text += stueck;
      ziel.innerHTML = kiHtml(text) + '<span class="ki-strich"></span>';
    }, abbruch);
    ziel.innerHTML = kiHtml(text) + (sauber ? '' : halb);
  } catch (fehler) {
    // Eine neue Frage hat die alte abgeloest: dann ist das kein Fehler,
    // sondern der Nutzer, der weitergetippt hat.
    if (kiAbbruch !== abbruch) return;
    const offline = typeof navigator !== 'undefined' && navigator.onLine === false;
    const roh = String((fehler && fehler.message) || fehler);
    // «Failed to fetch» ist die Standardmeldung des Browsers, wenn die
    // Verbindung gar nicht zustande kam. Sie hier unuebersetzt stehen zu
    // lassen, hiesse dem Leser Browser-Innereien vorzusetzen.
    const netz = /failed to fetch|networkerror|load failed/i.test(roh);
    const grund = offline
      ? 'Du bist offline — das Sprachmodell ist nicht erreichbar.'
      : abbruch.signal.aborted
        ? 'Die Antwort hat zu lange gedauert und wurde abgebrochen.'
        : netz
          ? 'Der Vermittler ist gerade nicht erreichbar.'
          : roh;
    // Was schon eingetroffen ist, bleibt stehen. Es ist nicht falsch,
    // nur unvollstaendig — und das steht dann dabei.
    ziel.innerHTML = kiHtml(text) + `<p class="ki-fehler">${esc(grund)}</p>
      <p class="notiz">Die Zahlen stehen unabhängig davon zur Verfügung —
        sie liegen auf dem Gerät.</p>`;
    if (ersatz) {
      const huelle = document.createElement('div');
      huelle.innerHTML = ersatz;
      $('#frage-antwort').appendChild(huelle.firstElementChild);
      verdrahteMehr();
    }
  } finally {
    clearTimeout(uhr);
    if (kiAbbruch === abbruch) kiAbbruch = null;
  }
}

function verdrahteMehr() {
  $('#frage-antwort').querySelectorAll('.antwort-mehr .knopf').forEach((k) => {
    if (k.dataset.verdrahtet) return;
    k.dataset.verdrahtet = '1';
    k.addEventListener('click', () => {
      $('#frage-feld').value = k.dataset.frage;
      zeigeAntwort(k.dataset.frage);
    });
  });
}

function zeigeAntwort(roh) {
  const feld = $('#frage-antwort');
  const { antwort, weitere, notfall } = beantworte(roh);

  // Eine laufende Antwort auf die vorige Frage ist ab jetzt uninteressant.
  if (kiAbbruch) { const alt = kiAbbruch; kiAbbruch = null; alt.abort(); }

  const mehr = weitere.length ? `
    <div class="antwort-mehr">
      <span class="marke">Auch dazu</span>
      ${weitere.map((w) => `<button type="button" class="knopf klein"
        data-frage="${esc(w.kurz)}">${esc(w.kurz)}</button>`).join('')}
    </div>` : '';

  const zahlenKarte = `
    <div class="karte antwort-karte">
      ${kiAn() ? '<span class="marke">Die Zahlen dazu</span>' : ''}
      <h3>${esc(antwort.titel)}</h3>
      ${antwort.html}
      ${mehr}
    </div>`;

  if (!kiAn()) {
    feld.innerHTML = zahlenKarte;
    verdrahteMehr();
    feld.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    return;
  }

  // Hat die Stichwortsuche nichts gefunden, waere "nicht verstanden" unter
  // einer gelungenen Antwort nur verwirrend. Die Karte kommt dann erst, wenn
  // das Modell ausfaellt.
  feld.innerHTML = `
    <div class="karte antwort-karte ki-karte">
      <span class="marke">Antwort</span>
      <div class="ki-text" id="ki-text"><span class="ki-warte">Denkt nach</span></div>
      <p class="notiz ki-fuss">Formuliert von einem Sprachmodell — aus genau
        den Zahlen dieser Seite, nicht aus seinem Gedächtnis. Simulation zu
        Lernzwecken, keine Anlageberatung.</p>
    </div>` + (notfall ? '' : zahlenKarte);

  verdrahteMehr();
  feld.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  kiStarten(roh, notfall ? zahlenKarte : '');
}

function frageEinrichten() {
  const form = $('#frage-form');
  const feld = $('#frage-feld');
  const vor = $('#frage-vorschlaege');

  if (kiAn()) {
    feld.placeholder = 'Frag etwas — zu einer Aktie, einer Zahl oder der Analyse';
  }

  $('#frage-beispiele').innerHTML = (kiAn() ? BEISPIELE_KI : BEISPIELE).map((b) =>
    `<button type="button" class="beispiel">${esc(b)}</button>`).join('');
  $('#frage-beispiele').querySelectorAll('.beispiel').forEach((k) => {
    k.addEventListener('click', () => {
      feld.value = k.textContent;
      zeigeVorschlaege([]);
      zeigeAntwort(feld.value);
    });
  });

  feld.addEventListener('input', () => {
    zeigeVorschlaege(vorschlaegeFuer(feld.value));
  });

  feld.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); markiereVorschlag(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); markiereVorschlag(-1); }
    else if (e.key === 'Escape') { zeigeVorschlaege([]); }
    else if (e.key === 'Enter' && vorschlagIndex >= 0) {
      const gewaehlt = vor.querySelectorAll('.vorschlag')[vorschlagIndex];
      if (gewaehlt) {
        e.preventDefault();
        feld.value = gewaehlt.dataset.wert;
        zeigeVorschlaege([]);
        feld.blur();
        zeigeAntwort(feld.value);
      }
    }
  });

  vor.addEventListener('click', (e) => {
    const knopf = e.target.closest('.vorschlag');
    if (!knopf) return;
    feld.value = knopf.dataset.wert;
    zeigeVorschlaege([]);
    feld.blur();
    zeigeAntwort(feld.value);
  });

  // Ein Klick irgendwohin schliesst die Liste — sonst bleibt sie auf dem
  // Handy stehen und verdeckt die Antwort.
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.frage-huelle')) zeigeVorschlaege([]);
  });

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    zeigeVorschlaege([]);
    feld.blur();
    const text = feld.value.trim();
    if (text) zeigeAntwort(text);
  });
}

// ── Start ──────────────────────────────────────────────────────────────────

async function start() {
  themaEinrichten();
  installEinrichten();

  const [status, steuerung, latest, equity, gewichte] = await Promise.all([
    holen('status.json'), holen('control.json'),
    holen('latest.json'), holen('equity.json'), holen('weights.json'),
  ]);

  zeigeKopf(status, steuerung);
  schalterEinrichten(steuerung);
  zeigeIdeen(latest);
  zeigeDepots(equity);
  zeigeLernen(gewichte);

  // Die Fragezeile antwortet aus denselben Dateien — erst wenn sie da sind.
  WISSEN.latest = latest;
  WISSEN.equity = equity;
  WISSEN.gewichte = gewichte;
  WISSEN.status = status;
  frageEinrichten();

  const kal = latest && latest.kalibrierung;
  $('#kalibrierung').textContent = kal && kal.text ? kal.text
    : 'Die Messlatte stammt aus der wöchentlichen Kalibrierung über das ganze Universum.';

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch(() => { /* dann eben ohne */ });
  }
}

start();
