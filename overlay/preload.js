// The page's only bridge to the main process: receive commands, say when it's ready.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('quiff', {
  onCommand: (handler) => ipcRenderer.on('cmd', (_event, msg) => handler(msg)),
  ready: () => ipcRenderer.send('page-ready'),
});
