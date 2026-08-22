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
  $('#lauf-text').textContent = zeile;

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
  <article class="karte idee">
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

// ── Start ──────────────────────────────────────────────────────────────────

async function start() {
  themaEinrichten();

  const [status, steuerung, latest, equity] = await Promise.all([
    holen('status.json'), holen('control.json'),
    holen('latest.json'), holen('equity.json'),
  ]);

  zeigeKopf(status, steuerung);
  schalterEinrichten(steuerung);
  zeigeIdeen(latest);
  zeigeDepots(equity);

  const kal = latest && latest.kalibrierung;
  $('#kalibrierung').textContent = kal && kal.text ? kal.text
    : 'Die Messlatte stammt aus der wöchentlichen Kalibrierung über das ganze Universum.';

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch(() => { /* dann eben ohne */ });
  }
}

start();
