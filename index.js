const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

console.log('[MASTER LAUNCHER] Starting services...');

// Launch Discord Bot
console.log('[MASTER LAUNCHER] Launching Discord Bot...');
const discordBot = spawn('node', ['botDiscord.js'], { stdio: 'inherit' });

discordBot.on('close', (code) => {
    console.error(`[MASTER LAUNCHER] Discord bot exited with code ${code}`);
});

// Launch Steam Worker
console.log('[MASTER LAUNCHER] Launching Steam Worker...');
const steamWorker = spawn('node', ['StemTokenGen.js'], { stdio: 'inherit' });

steamWorker.on('close', (code) => {
    console.error(`[MASTER LAUNCHER] Steam worker exited with code ${code}`);
});

// Launch Ubisoft Worker (if dll and dotnet are available)
const ubiDir = path.join(__dirname, 'UbisoftBot');
const ubiDll = path.join(ubiDir, 'DenuvoTicket.dll');

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
