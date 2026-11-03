// Quiff overlay: one transparent, click-through window over the whole screen.
//
// Quiff (Python) starts this app with --quiff-port=N and sends JSON lines over
// a local socket: {"t":"state"|"target"|"clear"|"config"|"close", ...}. The
// window never takes focus and ignores the mouse, so the apps Quiff drives
// behave exactly as if it weren't there. Screen recorders capture it.
//
// Run without a port (`npm run demo`) to preview the design with a demo script.

const { app, BrowserWindow, ipcMain, screen } = require('electron');
const net = require('net');
const path = require('path');

const arg = (name) => {
  const found = process.argv.find((a) => a.startsWith(`--${name}=`));
  return found ? found.slice(name.length + 3) : '';
};
const port = Number(arg('quiff-port')) || 0;
const token = arg('quiff-token'); // proves to Quiff that this connection is its overlay

let win = null;
let ready = false;
let pending = [];
const debug = (...args) => { if (process.env.QUIFF_OVERLAY_DEBUG) console.log('[overlay]', ...args); };

// Quiff sends physical screen pixels; the page lays out in DIPs relative to this window.
function toPage(rect) {
  const [left, top, right, bottom] = rect;
  const physical = { x: left, y: top, width: right - left, height: bottom - top };
  let dip;
  try {
    dip = screen.screenToDipRect(null, physical);
  } catch {
    const f = screen.getPrimaryDisplay().scaleFactor || 1;
    dip = { x: left / f, y: top / f, width: physical.width / f, height: physical.height / f };
  }
  const bounds = win.getBounds();
  return { x: dip.x - bounds.x, y: dip.y - bounds.y, w: dip.width, h: dip.height };
}

function deliver(msg) {
  if (!win) return;
  if (msg.t === 'close') {
    app.quit();
    return;
  }
  if (msg.t === 'target' && Array.isArray(msg.rect)) msg.box = toPage(msg.rect);
  if (ready) win.webContents.send('cmd', msg);
  else pending.push(msg);
}

function connect() {
  const socket = net.connect(port, '127.0.0.1', () => socket.write(`ready ${token}\n`));
  let buffer = '';
  socket.setEncoding('utf8');
  socket.on('data', (chunk) => {
    buffer += chunk;
    let cut;
    while ((cut = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, cut).trim();
      buffer = buffer.slice(cut + 1);
      if (!line) continue;
      try {
        deliver(JSON.parse(line));
      } catch {
        // a malformed line must never take the overlay down
      }
    }
  });
  // Quiff exited (or crashed): the overlay goes with it.
  socket.on('close', () => app.quit());
  socket.on('error', () => app.quit());
}

app.whenReady().then(() => {
  const display = screen.getPrimaryDisplay();
  win = new BrowserWindow({
    ...display.bounds,
    title: 'Quiff overlay',
    transparent: true,
    backgroundColor: '#00000000',
    frame: false,
    hasShadow: false,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    focusable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      backgroundThrottling: false,
    },
  });
  win.setIgnoreMouseEvents(true);
  win.setAlwaysOnTop(true, 'screen-saver');
  win.webContents.on('will-navigate', (event) => event.preventDefault());
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  win.webContents.on('console-message', (event) => debug('page:', event.message));
  win.webContents.on('preload-error', (_event, file, error) => debug('preload error', file, error));
  win.webContents.on('render-process-gone', (_event, details) => debug('renderer gone', details.reason));
  debug('display', JSON.stringify(display.bounds), 'scale', display.scaleFactor, 'port', port);

  ipcMain.once('page-ready', () => {
    debug('page ready; showing');
    win.showInactive();
    ready = true;
    if (!port) win.webContents.send('cmd', { t: 'demo' });
    for (const msg of pending) win.webContents.send('cmd', msg);
    pending = [];
    if (port) connect();
  });
  win.loadFile(path.join(__dirname, 'index.html'));
});

app.on('window-all-closed', () => app.quit());
