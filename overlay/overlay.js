// Quiff overlay behaviour: the v3 pill and the v4 ghost cursor, driven by Quiff's events.
// Timings and colours follow the v4 spec (values at 1x; Electron handles display scaling).
'use strict';

const COL = { working: '#4C8DF6', unsure: '#E5A93B', approval: '#EF6A4C', yourturn: '#3DBE8B' };
const CHIP = { working: '#2D6AD1', unsure: '#E5A93B', approval: '#D24A2E' };
const LABEL = {
  working: 'oklch(0.78 0.10 245)', unsure: 'oklch(0.84 0.11 85)',
  approval: 'oklch(0.76 0.14 32)', yourturn: 'oklch(0.82 0.11 160)',
};
const NAMES = { working: 'Working', unsure: 'Unsure', approval: 'Approval needed', yourturn: 'Your turn' };
const EASE_OUT = 'cubic-bezier(.2,.8,.2,1)';
const EASE_IN = 'cubic-bezier(.4,0,1,1)';
const EASE_IO = 'cubic-bezier(.45,0,.55,1)';

const $ = (selector, scope = document) => scope.querySelector(selector);
const root = document.documentElement;
const pill = $('#pill');
const cursor = $('#cursor');
const highlight = $('#highlight');
const glow = $('#glow');
const measure = { label: $('#measure .label'), line: $('#measure .line'), brand: $('#measure .brand') };

const config = {
  reducedMotion: window.matchMedia('(prefers-reduced-motion: reduce)').matches,
  edgeGlow: true,
};
let pillState = null;
let waitSince = 0;
let visible = false; // the cursor is out of the pill
let pos = { x: 0, y: 0 };
let timers = [];

const later = (ms, fn) => timers.push(setTimeout(fn, ms));
const clearTimers = () => { timers.forEach(clearTimeout); timers = []; };
const tr = (ms, ease) => `left ${ms}ms ${ease}, top ${ms}ms ${ease}, opacity ${ms}ms ${ease}, transform ${ms}ms ${ease}`;
const reflow = (el) => void el.getBoundingClientRect();
const pillOrigin = () => ({ x: window.innerWidth / 2, y: 34 }); // the pill's centre

// ------------------------------------------------------------------ pill

function pillWidth(kind, line) {
  measure.label.textContent = NAMES[kind];
  measure.line.textContent = line;
  const w = (el) => el.getBoundingClientRect().width;
  // padding 12 | chip (10 + dot 7 + gap 8 + label + 10) | gap 12 | line | gap 12 | Quiff | padding 14
  const natural = 12 + (10 + 7 + 8 + w(measure.label) + 10) + 12 + w(measure.line) + 12 + w(measure.brand) + 14 + 2;
  const approval = kind === 'approval';
  const width = Math.max(approval ? 660 : 380, Math.min(approval ? 780 : 740, natural));
  return Math.round(Math.min(width, window.innerWidth - 32));
}

function setState(msg) {
  const kind = NAMES[msg.kind] ? msg.kind : 'working';
  const line = msg.line || '';
  const previous = pillState;
  pillState = kind;
  pill.dataset.state = kind;
  pill.style.setProperty('--label', LABEL[kind]);
  $('.label', pill).textContent = NAMES[kind];
  $('.line', pill).textContent = line;
  $('.guard-text', pill).textContent = msg.guard || '';
  pill.style.width = pillWidth(kind, line) + 'px';
  pill.classList.add('on');

  glow.classList.toggle('on', kind === 'approval' && config.edgeGlow);
  if (kind === 'approval' && previous !== 'approval') {
    waitSince = Date.now();
    tickWait();
  }
  if (kind !== 'yourturn') root.style.setProperty('--halo', COL[kind]);
  cursor.classList.toggle('ringed', kind === 'approval' && visible);

  if (kind === 'unsure' && previous !== 'unsure') drift();
  if (kind === 'yourturn') {
    clearTimers();
    hideHighlight();
    hideCursor();
  }
}

function tickWait() {
  if (pillState !== 'approval') return;
  const s = Math.floor((Date.now() - waitSince) / 1000);
  $('.wait', pill).textContent = Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
}
setInterval(tickWait, 1000);

// ------------------------------------------------------------------ cursor

function place(point, { transition = 'none', opacity, scale } = {}) {
  cursor.style.transition = transition;
  cursor.style.left = point.x + 'px';
  cursor.style.top = point.y + 'px';
  if (opacity !== undefined) cursor.style.opacity = opacity;
  if (scale !== undefined) cursor.style.transform = `scale(${scale})`;
  pos = { x: point.x, y: point.y };
}

// Emerge from the pill (240 ms) or glide (220 ms); reduced motion fades instead.
function moveTo(point, done) {
  if (config.reducedMotion) {
    if (!visible) {
      place(point, { opacity: 0, scale: 1 });
      reflow(cursor);
      place(point, { transition: 'opacity 150ms ease-out', opacity: 1 });
      later(150, done);
    } else if (pos.x === point.x && pos.y === point.y) {
      later(0, done);
    } else {
      cursor.style.transition = 'opacity 120ms ease-in';
      cursor.style.opacity = 0;
      later(120, () => {
        place(point);
        reflow(cursor);
        place(point, { transition: 'opacity 150ms ease-out', opacity: 1 });
      });
      later(270, done);
    }
  } else if (!visible) {
    place(pillOrigin(), { opacity: 0, scale: 0.6 });
    reflow(cursor);
    place(point, { transition: tr(240, EASE_OUT), opacity: 1, scale: 1 });
    later(240, done);
  } else {
    place(point, { transition: tr(220, EASE_OUT), opacity: 1, scale: 1 });
    later(220, done);
  }
  visible = true;
}

// Your turn: 240 ms ease-in back into the pill.
function hideCursor() {
  visible = false;
  cursor.classList.remove('ringed');
  if (config.reducedMotion) {
    cursor.style.transition = 'opacity 150ms ease-out';
    cursor.style.opacity = 0;
  } else {
    place(pillOrigin(), { transition: tr(240, EASE_IN), opacity: 0, scale: 0.6 });
  }
}

function ripple() {
  if (config.reducedMotion) return;
  const ring = document.createElement('span');
  ring.className = 'ripple';
  ring.addEventListener('animationend', () => ring.remove());
  $('.fx', cursor).appendChild(ring);
}

// Unsure: the cursor hesitates in small hops (520 ms each, 250 ms dwell), then settles.
function drift() {
  if (config.reducedMotion || !visible) return;
  const base = { ...pos };
  let t = 250;
  for (const [dx, dy] of [[-18, -10], [0, 0], [-18, -10], [0, 0]]) {
    later(t, () => place({ x: base.x + dx, y: base.y + dy }, { transition: tr(520, EASE_IO), opacity: 1, scale: 1 }));
    t += 520 + 250;
  }
}

// ------------------------------------------------------------------ highlight

function showHighlight(box, kind, chip) {
  const k = CHIP[kind] ? kind : 'working';
  highlight.style.left = box.x - 4 + 'px';
  highlight.style.top = box.y - 4 + 'px';
  highlight.style.width = box.w + 8 + 'px';
  highlight.style.height = box.h + 8 + 'px';
  highlight.style.setProperty('--hl', COL[k]);
  highlight.style.setProperty('--chip', CHIP[k]);
  highlight.style.setProperty('--chip-fg', k === 'unsure' ? '#1c1917' : '#ffffff');
  highlight.classList.toggle('unsure', k === 'unsure');
  highlight.classList.toggle('below', box.y - 4 < 34); // no room above: chip goes below
  $('.chip', highlight).textContent = chip;
  highlight.classList.add('on');
}

function hideHighlight(instant = false) {
  if (instant) {
    highlight.style.transition = 'none';
    highlight.classList.remove('on');
    reflow(highlight);
    highlight.style.transition = '';
  } else {
    highlight.classList.remove('on');
  }
}

// ------------------------------------------------------------------ target

// The hotspot parks at the target's right - 10 px, bottom - 8 px, so the body
// falls outside the target and the chip (above-left) is never covered.
function target(msg) {
  clearTimers();
  const box = msg.box;
  const kind = COL[msg.kind] ? msg.kind : 'working';
  root.style.setProperty('--halo', COL[kind]);
  cursor.classList.remove('ringed');
  hideHighlight(true);
  const point = {
    x: Math.round(box.x + Math.max(box.w - 10, box.w / 2)),
    y: Math.round(box.y + Math.max(box.h - 8, box.h / 2)),
  };
  moveTo(point, () => {
    if (kind === 'approval') { // parks without clicking: ring + pulse
      cursor.classList.add('ringed');
      showHighlight(box, kind, msg.chip || '');
      return;
    }
    if (msg.ripple) ripple();
    later(config.reducedMotion ? 0 : 120, () => showHighlight(box, kind, msg.chip || ''));
  });
}

// ------------------------------------------------------------------ demo (npm run demo)

function demo() {
  const W = window.innerWidth;
  const H = window.innerHeight;
  const at = (fx, fy, w, h) => ({ x: Math.round(W * fx), y: Math.round(H * fy), w, h });
  const attach = at(0.36, 0.70, 84, 30);
  const to = at(0.36, 0.22, 240, 26);
  const row = at(0.36, 0.27, 300, 36);
  const send = at(0.29, 0.70, 76, 30);
  const steps = [
    [1800, () => setState({ kind: 'working', line: 'Reading the screen' })],
    [2400, () => { setState({ kind: 'working', line: 'Clicking "Attach"' }); target({ box: attach, chip: 'Click · Attach', kind: 'working', ripple: true }); }],
    [2400, () => { setState({ kind: 'working', line: 'Typing into "To"' }); target({ box: to, chip: 'Type · "accounts"', kind: 'working', ripple: true }); }],
    [3900, () => setState({ kind: 'unsure', line: 'Rescuer took over · low confidence' })],
    [2000, () => { setState({ kind: 'working', line: 'Clicking "accounts@halden.co"' }); target({ box: row, chip: 'Click · accounts@halden.co', kind: 'working', ripple: true }); }],
    [5200, () => { setState({ kind: 'approval', line: 'Clicking "Send"', guard: 'click on Send (commits an action)' }); target({ box: send, chip: 'Needs approval · Send', kind: 'approval', ripple: false }); }],
    [1400, () => { setState({ kind: 'working', line: 'Clicking "Send"' }); target({ box: send, chip: 'Click · Send', kind: 'working', ripple: true }); }],
    [3600, () => setState({ kind: 'yourturn', line: 'Done · all checks pass' })],
  ];
  let i = 0;
  const next = () => {
    const [ms, run] = steps[i];
    run();
    i = (i + 1) % steps.length;
    setTimeout(next, ms);
  };
  next();
}

// ------------------------------------------------------------------ wiring

window.quiff.onCommand((msg) => {
  try {
    if (msg.t === 'config') {
      if (typeof msg.reducedMotion === 'boolean') config.reducedMotion = msg.reducedMotion;
      if (typeof msg.edgeGlow === 'boolean') config.edgeGlow = msg.edgeGlow;
      document.body.classList.toggle('reduced', config.reducedMotion);
    } else if (msg.t === 'state') {
      setState(msg);
    } else if (msg.t === 'target' && msg.box) {
      target(msg);
    } else if (msg.t === 'clear') {
      hideHighlight();
    } else if (msg.t === 'demo') {
      demo();
    }
  } catch (error) {
    console.error(error);
  }
});

document.body.classList.toggle('reduced', config.reducedMotion);
Promise.all([
  document.fonts.load('400 15px "IBM Plex Sans"'),
  document.fonts.load('600 14px "IBM Plex Sans"'),
  document.fonts.load('400 14px "IBM Plex Mono"'),
]).catch(() => {}).finally(() => window.quiff.ready());
