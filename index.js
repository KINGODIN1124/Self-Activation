const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const express = require('express');

const app = express();
const PORT = process.env.PORT || 3000;

app.get('/', (req, res) => {
    res.send('Self-Activation Bot is online!');
});

app.get('/health', (req, res) => {
    res.json({ status: 'ok', uptime: process.uptime() });
});

app.listen(PORT, () => {
    console.log(`[MASTER LAUNCHER] Web server bound to port ${PORT} (Keep-alive ready)`);
});

// Initialize empty persistent directories and files if they are blank (common in container volume mounts)
const DATA_DIR = path.join(__dirname, 'data');
const GAMES_FILE = path.join(DATA_DIR, 'games.json');
const GAMES_DEFAULT = path.join(__dirname, 'games.default.json');

if (!fs.existsSync(DATA_DIR)) {
    fs.mkdirSync(DATA_DIR, { recursive: true });
}

if (!fs.existsSync(GAMES_FILE) && fs.existsSync(GAMES_DEFAULT)) {
    console.log('[MASTER LAUNCHER] Initializing games.json from default template...');
    try {
        fs.copyFileSync(GAMES_DEFAULT, GAMES_FILE);
    } catch (e) {
        console.error('[MASTER LAUNCHER] Failed to copy default games list:', e.message);
    }
}

console.log('[MASTER LAUNCHER] Starting services...');

// Launch Ubisoft Worker (if dll and dotnet are available)
const ubiDir = path.join(__dirname, 'UbisoftBot');
const ubiDll = path.join(ubiDir, 'DenuvoTicket.dll');

if (process.platform !== 'win32') {
    const ubiExe = path.join(ubiDir, 'DenuvoTicket');
    if (fs.existsSync(ubiExe)) {
        try {
            fs.chmodSync(ubiExe, 0o755);
            console.log('[MASTER LAUNCHER] Configured execute permissions on DenuvoTicket');
        } catch (e) {
            console.log(`[MASTER LAUNCHER] Note: runtime chmod not permitted (${e.message}). Relying on Git executable permissions.`);
        }
    }
}

function startService(scriptName) {
    console.log(`[MASTER LAUNCHER] Launching ${scriptName}...`);
    const child = spawn('node', [scriptName], { stdio: 'inherit' });
    
    child.on('close', (code) => {
        console.error(`[MASTER LAUNCHER] ${scriptName} exited with code ${code}`);
        console.log(`[MASTER LAUNCHER] Restarting ${scriptName} in 5 seconds...`);
        setTimeout(() => {
            startService(scriptName);
        }, 5000);
    });

    return child;
}

// Launch Discord Bot
startService('botDiscord.js');

// Launch Steam Worker
startService('StemTokenGen.js');

if (fs.existsSync(ubiDll)) {
    console.log('[MASTER LAUNCHER] Ubisoft bot files detected. Checking for dotnet environment...');
    const checkDotnet = spawn('dotnet', ['--version']);
    
    checkDotnet.on('close', (code) => {
        if (code === 0) {
            console.log('[MASTER LAUNCHER] dotnet runtime found. Ubisoft worker is ready (will be invoked dynamically on-demand).');
        } else {
            console.warn('[MASTER LAUNCHER] dotnet command was not found. Ubisoft activations will fail. (Ensure .NET Runtime is installed if hosting Ubisoft titles)');
        }
    });

    checkDotnet.on('error', () => {
        console.warn('[MASTER LAUNCHER] dotnet command failed or was not found. Ubisoft activations will fail.');
    });
}
