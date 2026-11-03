// `npm run demo`: preview the overlay with its demo script (no Quiff run needed).
// VS Code terminals set ELECTRON_RUN_AS_NODE, which turns Electron into plain
// Node, so it is removed for the child.
const { spawn } = require('child_process');

const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;
const electron = require('electron'); // from plain Node: the path to Electron's binary
spawn(electron, [__dirname], { stdio: 'inherit', env }).on('exit', (code) => process.exit(code ?? 0));
