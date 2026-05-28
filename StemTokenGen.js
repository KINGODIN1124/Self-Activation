const fs = require('fs');
const path = require('path');
const { exec } = require('child_process');
const SteamUser = require('steam-user');
const extract = require('extract-zip');
const archiver = require('archiver');

const BASE = __dirname;
const DATA = path.join(BASE, 'data');
const COMPLETED_DIR = path.join(BASE, 'completed_configs');
const UBI_DIR = path.join(BASE, 'UbisoftBot');
const MAUTH = path.join(DATA, 'cachedlogins');

[DATA, COMPLETED_DIR, UBI_DIR, MAUTH].forEach(d => {
    if (!fs.existsSync(d)) fs.mkdirSync(d, { recursive: true });
});

if (process.platform !== 'win32') {
    const ubiExe = path.join(UBI_DIR, 'DenuvoTicket');
    if (fs.existsSync(ubiExe)) {
        try {
            fs.chmodSync(ubiExe, 0o755);
            console.log('[STEAM FLEET] Configured execute permissions on DenuvoTicket');
        } catch (e) {
            console.log(`[STEAM FLEET] Note: runtime chmod not permitted (${e.message}). Relying on Git executable permissions.`);
        }
    }
}

const ACCOUNTS_FILE = path.join(DATA, 'accounts.json');
const PENDING_FILE = path.join(DATA, 'pending_requests.json');
const GUARDS_FILE = path.join(DATA, 'guards.json');
const UBI_ACCOUNTS_FILE = path.join(DATA, 'ubi_accounts.json');
const UBI_PENDING_FILE = path.join(DATA, 'pending_ubi.json');
const STEAM_STATE_FILE = path.join(DATA, 'steam_state.json');
const OWNERSHIP_CACHE_FILE = path.join(DATA, 'ownership_cache.json');
const TOKENS_FILE = path.join(DATA, 'refresh_tokens.json');

if (!fs.existsSync(ACCOUNTS_FILE)) fs.writeFileSync(ACCOUNTS_FILE, '[]');
if (!fs.existsSync(GUARDS_FILE)) fs.writeFileSync(GUARDS_FILE, '{}');
if (!fs.existsSync(UBI_ACCOUNTS_FILE)) fs.writeFileSync(UBI_ACCOUNTS_FILE, '{}');
if (!fs.existsSync(STEAM_STATE_FILE)) fs.writeFileSync(STEAM_STATE_FILE, '{}');
if (!fs.existsSync(OWNERSHIP_CACHE_FILE)) fs.writeFileSync(OWNERSHIP_CACHE_FILE, '{}');
if (!fs.existsSync(TOKENS_FILE)) fs.writeFileSync(TOKENS_FILE, '{}');

const clients = {};
const states = {};
const guardCallbacks = {};

function readJson(file, def) {
    try {
        return JSON.parse(fs.readFileSync(file, 'utf-8'));
    } catch {
        return def;
    }
}

function writeJson(file, data) {
    fs.writeFileSync(file + '.tmp', JSON.stringify(data, null, 2), 'utf-8');
    fs.renameSync(file + '.tmp', file);
}

function syncState() {
    writeJson(STEAM_STATE_FILE, states);
}

let lastLoginTime = 0;
const LOGIN_DELAY_MS = 8000; // Stagger logins by 8 seconds to avoid Steam rate limit

function doLogin(u, p, code = null) {
    if (!clients[u]) {
        clients[u] = new SteamUser({ dataDirectory: MAUTH, enablePicsCache: true });

        clients[u].on('loggedOn', details => {
            console.log(`[STEAM FLEET] -> Successfully logged in: ${u}`);
            states[u] = { logged_in: true, connecting: false, ownership_cached: false, steam_id: clients[u].steamID.getSteamID64(), guard_needed: false, guard_domain: null };
            lastLoginTime = 0;
            syncState();
        });

        clients[u].on('refreshToken', token => {
            console.log(`[STEAM FLEET] -> Saved refresh token for: ${u}`);
            let tokens = readJson(TOKENS_FILE, {});
            tokens[u] = token;
            writeJson(TOKENS_FILE, tokens);
        });

        clients[u].on('ownershipCached', () => {
            console.log(`[STEAM FLEET] -> Ownership cache ready for: ${u}`);
            states[u] = { ...(states[u] || {}), logged_in: true, connecting: false, ownership_cached: true, steam_id: clients[u].steamID?.getSteamID64?.() || states[u]?.steam_id || null };
            syncState();

            try {
                let cache = readJson(OWNERSHIP_CACHE_FILE, {});
                cache[u] = clients[u].getOwnedApps();
                writeJson(OWNERSHIP_CACHE_FILE, cache);
                console.log(`[STEAM FLEET] -> Saved ${cache[u].length} owned apps for ${u} to cache.`);
            } catch (e) {
                console.log(`[STEAM FLEET] -> Failed to save ownership cache for ${u}: ${e.message}`);
            }
        });

        clients[u].on('steamGuard', (domain, callback, lastCodeWrong) => {
            console.log(`[STEAM FLEET] -> Steam Guard needed for ${u} (domain: ${domain}, lastCodeWrong: ${lastCodeWrong})`);
            states[u] = { logged_in: false, connecting: false, guard_needed: true, guard_domain: domain ? 'email' : '2FA', last_code_wrong: !!lastCodeWrong };
            guardCallbacks[u] = callback;
            lastLoginTime = 0;
            syncState();
        });

        clients[u].on('error', err => {
            console.log(`[STEAM FLEET] -> Error for ${u}: ${err.message}`);
            
            // Set error cooldown to prevent spamming login requests (15 mins for Throttle/RateLimit, 1 min for others)
            let isThrottle = err.message.toLowerCase().includes('throttle') || err.message.toLowerCase().includes('ratelimit') || err.message.toLowerCase().includes('rate limit');
            let backoffMs = isThrottle ? 15 * 60 * 1000 : 60 * 1000;
            let retryAfter = Date.now() + backoffMs;
            
            if (err.message.includes('InvalidPassword') || err.message.includes('AccessDenied') || err.message.toLowerCase().includes('token')) {
                let tokens = readJson(TOKENS_FILE, {});
                if (tokens[u]) {
                    delete tokens[u];
                    writeJson(TOKENS_FILE, tokens);
                    console.log(`[STEAM FLEET] -> Deleted invalid refresh token for ${u}`);
                }
            }

            if (err.message.includes('SteamGuardNeed2FA') || err.message.includes('NeedTwoFactor')) {
                states[u] = { logged_in: false, connecting: false, guard_needed: true, guard_domain: '2FA' };
            } else if (err.message.includes('SteamGuardNeedAuthCode') || err.message.includes('NeedAuthCode')) {
                states[u] = { logged_in: false, connecting: false, guard_needed: true, guard_domain: 'email' };
            } else {
                states[u] = { logged_in: false, connecting: false, guard_needed: false, error: err.message, retry_after: retryAfter };
            }
            lastLoginTime = 0;
            syncState();
        });

        clients[u].on('disconnected', () => {
            if (states[u]) {
                states[u].logged_in = false;
                states[u].connecting = false;
                states[u].ownership_cached = false;
            }
        });
    }

    if (!states[u]) states[u] = { logged_in: false, connecting: false };

    // Prevent login attempts if the account is currently under error cooldown (unless code is provided)
    if (states[u].retry_after && Date.now() < states[u].retry_after && !code) {
        return;
    }

    if (code && states[u].guard_needed && guardCallbacks[u]) {
        console.log(`[STEAM FLEET] Submitting Guard code for ${u} via callback...`);
        states[u].connecting = true;
        states[u].guard_needed = false;
        states[u].error = null;
        states[u].last_code_wrong = false;
        if (states[u].retry_after) delete states[u].retry_after;
        syncState();
        guardCallbacks[u](code);
        delete guardCallbacks[u];
        return;
    }

    if ((states[u].logged_in || states[u].connecting) && !code) return;

    let now = Date.now();
    if (!code && now - lastLoginTime < LOGIN_DELAY_MS) return;
    if (!code) lastLoginTime = now;

    let tokens = readJson(TOKENS_FILE, {});
    let logOnOptions = {};
    
    if (code) {
        logOnOptions.accountName = u;
        logOnOptions.password = p;
        if (states[u]?.guard_domain === 'email') logOnOptions.authCode = code;
        else logOnOptions.twoFactorCode = code;
    } else if (tokens[u]) {
        logOnOptions.refreshToken = tokens[u];
    } else {
        logOnOptions.accountName = u;
        logOnOptions.password = p;
    }

    states[u].connecting = true;
    states[u].error = null;
    states[u].guard_needed = false;
    states[u].guard_domain = null;
    states[u].last_code_wrong = false;
    console.log(`[STEAM FLEET] Establishing connection for worker account: ${u}...`);
    try {
        clients[u].logOn(logOnOptions);
    } catch (e) {
        states[u].connecting = false;
        console.log(`[STEAM FLEET] Exception: ${e.message}`);
    }
}

function getTicket(client, appId) {
    return new Promise((resolve) => {
        client.gamesPlayed([appId]);
        setTimeout(() => {
            client.getEncryptedAppTicket(appId, Buffer.alloc(0), (err, ticket) => {
                client.gamesPlayed([]);
                if (err) resolve({ ok: false, error: err.message });
                else resolve({ ok: true, ticket: ticket.toString('base64') });
            });
        }, 2000);
    });
}

function ownsApp(client, appId) {
    return client.ownsApp(appId);
}

function getSafeUserTag(userTag) {
    return userTag.replace(/[^a-zA-Z0-9]/g, '');
}

async function zipDirectory(sourceDir, outPath) {
    return new Promise((resolve, reject) => {
        const tempOutPath = `${outPath}.tmp`;
        try { if (fs.existsSync(tempOutPath)) fs.unlinkSync(tempOutPath); } catch { }
        const output = fs.createWriteStream(tempOutPath);
        const archive = archiver('zip', { zlib: { level: 9 } });
        output.on('close', () => {
            try {
                if (!fs.existsSync(tempOutPath) || fs.statSync(tempOutPath).size === 0) {
                    return reject(new Error('Generated zip is empty'));
                }
                fs.renameSync(tempOutPath, outPath);
                resolve();
            } catch (err) {
                reject(err);
            }
        });
        archive.on('error', err => reject(err));
        archive.pipe(output);
        archive.directory(sourceDir, false);
        archive.finalize();
    });
}

function countFilesRecursive(dir) {
    let total = 0;
    for (let entry of fs.readdirSync(dir, { withFileTypes: true })) {
        let fullPath = path.join(dir, entry.name);
        if (entry.isDirectory()) total += countFilesRecursive(fullPath);
        else total += 1;
    }
    return total;
}

function copyDirectorySync(src, dest) {
    if (!fs.existsSync(dest)) fs.mkdirSync(dest, { recursive: true });
    let entries = fs.readdirSync(src, { withFileTypes: true });
    for (let entry of entries) {
        let srcPath = path.join(src, entry.name);
        let destPath = path.join(dest, entry.name);
        if (entry.isDirectory()) {
            copyDirectorySync(srcPath, destPath);
        } else {
            fs.copyFileSync(srcPath, destPath);
        }
    }
}

function findSteamSettings(dir) {
    let entries = fs.readdirSync(dir, { withFileTypes: true });
    for (let entry of entries) {
        let fullPath = path.join(dir, entry.name);
        if (entry.isDirectory()) {
            if (entry.name.toLowerCase() === 'steamsettings' || entry.name.toLowerCase() === 'steam_settings') {
                return fullPath;
            }
            let res = findSteamSettings(fullPath);
            if (res) return res;
        }
    }
    return null;
}

let lastQueueLogTime = 0;

async function processSteamQueue() {
    if (!fs.existsSync(PENDING_FILE)) return;
    let reqs = readJson(PENDING_FILE, []);
    if (reqs.length === 0) return;

    let currentReq = reqs[0];
    let appId = parseInt(currentReq.app_id, 10);
    let userTag = currentReq.user_tag || 'UnknownUser';

    let cache = readJson(OWNERSHIP_CACHE_FILE, {});
    let accDict = {};
    for (let a of readJson(ACCOUNTS_FILE, [])) accDict[a.username] = a.password;

    let ownerFound = null;
    let missingCacheAccounts = [];
    let guardNeededAccounts = [];

    let now = Date.now();
    let shouldLog = (now - lastQueueLogTime > 60000);
    if (shouldLog) {
        lastQueueLogTime = now;
    }

    if (shouldLog) {
        console.log(`[STEAM ENGINE] Scanning active accounts for AppID ${appId}...`);
    }
    for (let u in accDict) {
        let isCached = !!cache[u];
        let ownsInCache = isCached && cache[u].includes(appId);
        let isLoggedOn = !!(states[u] && states[u].logged_in);

        if (shouldLog) {
            console.log(`[STEAM ENGINE] -> Account: ${u} | Cached: ${isCached} | Owns (in cache): ${ownsInCache} | Logged On: ${isLoggedOn}`);
        }

        if (ownsInCache) {
            ownerFound = u;
            break;
        }

        // Live check if logged on but cache doesn't show ownership (could be recently purchased/activated)
        if (isLoggedOn && clients[u] && typeof clients[u].getOwnedApps === 'function') {
            try {
                let owned = clients[u].getOwnedApps();
                if (owned && owned.includes(appId)) {
                    console.log(`[STEAM ENGINE] -> Live check SUCCESS: ${u} owns AppID ${appId} (updating cache).`);
                    cache[u] = owned;
                    writeJson(OWNERSHIP_CACHE_FILE, cache);
                    ownerFound = u;
                    break;
                }
            } catch (e) {
                if (shouldLog) {
                    console.log(`[STEAM ENGINE] -> Failed live check for ${u}: ${e.message}`);
                }
            }
        }

        if (!isCached) {
            if (states[u] && states[u].guard_needed) {
                guardNeededAccounts.push(u);
                if (shouldLog) {
                    console.log(`[STEAM ENGINE] -> Account ${u} has no cache but requires Steam Guard. Waiting.`);
                }
                continue;
            }
            if (states[u] && states[u].error) {
                if (shouldLog) {
                    console.log(`[STEAM ENGINE] -> Account ${u} has no cache but has login error: ${states[u].error}. Skipping.`);
                }
                continue;
            }
            missingCacheAccounts.push(u);
            if (shouldLog) {
                console.log(`[STEAM ENGINE] -> Account ${u} has no cache. Added to missing cache list.`);
            }
        }
    }

    if (!ownerFound && missingCacheAccounts.length > 0) {
        let targetUser = missingCacheAccounts[0];
        if (states[targetUser]?.retry_after && Date.now() < states[targetUser].retry_after) {
            if (shouldLog) {
                let remaining = Math.ceil((states[targetUser].retry_after - Date.now()) / 1000);
                console.log(`[STEAM ENGINE] -> Account ${targetUser} has no cache but is cooldowned. Retry in ${remaining}s.`);
            }
            return;
        }
        if (!states[targetUser]?.connecting && !states[targetUser]?.logged_in) {
            doLogin(targetUser, accDict[targetUser]);
        }
        return;
    } else if (!ownerFound && missingCacheAccounts.length === 0) {
        if (guardNeededAccounts.length > 0) {
            // Blocked by Steam Guard. Just wait for the code.
            return;
        }
        console.log(`[STEAM ENGINE] Failure: No worker account lists AppID ${appId} in owned packages.`);
        let safeUserTag = getSafeUserTag(userTag);
        let failFile = path.join(COMPLETED_DIR, `configs_${safeUserTag}_${appId}_failed.txt`);
        fs.writeFileSync(failFile, 'no account found', 'utf-8');
        reqs.shift();
        writeJson(PENDING_FILE, reqs);
        return;
    }

    if (!states[ownerFound]?.logged_in || !states[ownerFound]?.ownership_cached || !clients[ownerFound]?.steamID) {
        if (states[ownerFound]?.guard_needed) {
            return; // Wait for guard code
        }
        if (states[ownerFound]?.retry_after && Date.now() < states[ownerFound].retry_after) {
            if (shouldLog) {
                let remaining = Math.ceil((states[ownerFound].retry_after - Date.now()) / 1000);
                console.log(`[STEAM ENGINE] -> Account ${ownerFound} owns the game but is cooldowned/throttled. Retry in ${remaining}s.`);
            }
            return;
        }
        if (!states[ownerFound]?.connecting) {
            console.log(`[STEAM ENGINE] AppID ${appId} is owned by ${ownerFound}. Logging them in...`);
            doLogin(ownerFound, accDict[ownerFound]);
        }
        return;
    }

    let c = clients[ownerFound];
    let u = ownerFound;
    reqs.shift();
    writeJson(PENDING_FILE, reqs);

    console.log(`\n[STEAM ENGINE] ----------------------------------------`);
    console.log(`[STEAM ENGINE] New generation payload received.`);
    console.log(`[STEAM ENGINE] Target User: ${userTag}`);
    console.log(`[STEAM ENGINE] Target AppID: ${appId}`);
    console.log(`[STEAM ENGINE] Generating ticket with known owner: ${u}...`);

    let resp = await getTicket(c, appId);
    let success = false;

    if (resp.ok) {
        let safeUserTag = getSafeUserTag(userTag);
        let searchPaths = [
            path.join(BASE, 'template'),
            path.join(BASE, 'templates'),
            path.join(BASE, '..', 'template'),
            path.join(BASE, '..', 'templates')
        ];

        let tempDir = fs.mkdtempSync(path.join(require('os').tmpdir(), 'steam-gen-'));
        let templateFound = false;

        for (let sp of searchPaths) {
            let zPath = path.join(sp, `${appId}.zip`);
            let dPath = path.join(sp, String(appId));

            if (fs.existsSync(zPath)) {
                await extract(zPath, { dir: tempDir });
                templateFound = true;
                break;
            } else if (fs.existsSync(dPath)) {
                copyDirectorySync(dPath, tempDir);
                templateFound = true;
                break;
            }
        }

        if (!templateFound || countFilesRecursive(tempDir) === 0) {
            fs.rmSync(tempDir, { recursive: true, force: true });
            console.log(`[STEAM ENGINE] ⚠️ Template payload for ${appId} is missing or empty.`);
            let failFile = path.join(COMPLETED_DIR, `configs_${safeUserTag}_${appId}_failed.txt`);
            fs.writeFileSync(failFile, 'template missing or empty', 'utf-8');
            success = true;
        } else {
            let sPath = findSteamSettings(tempDir);

            if (sPath) {
                let oldFile = path.join(sPath, 'configs.user');
                let oldIni = path.join(sPath, 'configs.user.ini');
                if (fs.existsSync(oldFile)) {
                    try { fs.unlinkSync(oldFile); } catch (e) { console.log(`[STEAM ENGINE] Could not delete old configs.user: ${e.message}`); }
                }
                if (fs.existsSync(oldIni)) {
                    try { fs.unlinkSync(oldIni); } catch (e) { console.log(`[STEAM ENGINE] Could not delete old configs.user.ini: ${e.message}`); }
                }

                let content = `[user::general]\naccount_steamid=${c.steamID.getSteamID64()}\naccount_name=${u}\nticket=${resp.ticket}\ntoken=${resp.ticket}\n`;
                fs.writeFileSync(oldFile, content, 'utf-8');
                fs.writeFileSync(oldIni, content, 'utf-8');

                let finalZip = path.join(COMPLETED_DIR, `configs_${safeUserTag}_${appId}.zip`);
                await zipDirectory(tempDir, finalZip);
                console.log(`[STEAM ENGINE] Success! Archive secured for ${userTag}`);
                success = true;
            } else {
                console.log(`[STEAM ENGINE] ⚠️ No steamsettings folder found for ${appId}. Not placing configs.user and failing the request.`);
                let failFile = path.join(COMPLETED_DIR, `configs_${safeUserTag}_${appId}_failed.txt`);
                fs.writeFileSync(failFile, 'steam_settings folder missing', 'utf-8');
                success = true;
            }
            fs.rmSync(tempDir, { recursive: true, force: true });
        }
    } else {
        console.log(`[STEAM ENGINE] -> ${u} owns AppID ${appId}, but ticket generation failed: ${resp.error}`);
    }

    if (!success) {
        let failFile = path.join(COMPLETED_DIR, `configs_${getSafeUserTag(userTag)}_${appId}_failed.txt`);
        console.log(`[STEAM ENGINE] Failure: Account ${u} owns AppID ${appId}, but encrypted ticket generation failed.`);
        fs.writeFileSync(failFile, 'ticket generation failed', 'utf-8');
    }
}

async function processUbiQueue() {
    if (!fs.existsSync(UBI_PENDING_FILE)) return;
    let ureqs = readJson(UBI_PENDING_FILE, []);
    if (ureqs.length === 0) return;

    let req = ureqs.shift();
    let userId = req.user_id;
    let tVal = req.t_val;
    let targetAppid = req.app_id;

    console.log(`\n[Ubisoft Engine] Processing Denuvo Request for Discord ID ${userId} (Denuvo ID: ${targetAppid})...`);

    let ubiDb = readJson(UBI_ACCOUNTS_FILE, {});
    let accounts = ubiDb[targetAppid];

    let success = false;
    if (accounts) {
        for (let acc of accounts) {
            console.log(`[Ubisoft Engine] Generating with ${acc.email}...`);

            let exeName = process.platform === 'win32' ? 'DenuvoTicket.exe' : './DenuvoTicket';
            let cmd = `${exeName} -username "${acc.email}" -password "${acc.password}" -denuvorequesttoken "${tVal}" -denuvoappid "${targetAppid}"`;

            try {
                await new Promise((resolve) => {
                    let child = exec(cmd, { cwd: UBI_DIR, timeout: 15000 }, (error, stdout, stderr) => {
                        resolve();
                    });
                    child.stdin.write('\n');
                });

                let generatedToken = path.join(UBI_DIR, 'token.txt');
                if (fs.existsSync(generatedToken) && fs.statSync(generatedToken).size > 0) {
                    let finalTokenPath = path.join(COMPLETED_DIR, `ubi_token_${userId}.txt`);
                    fs.renameSync(generatedToken, finalTokenPath);
                    console.log(`[Ubisoft Engine] Success! Token locked for User ${userId}.`);
                    success = true;
                    break;
                }
            } catch (e) {
                console.log(e);
            }
        }
    }

    if (!success) {
        let failPath = path.join(COMPLETED_DIR, `ubi_token_${userId}_failed.txt`);
        fs.writeFileSync(failPath, 'failed', 'utf-8');
        console.log(`[Ubisoft Engine] Generation Failed. Fleet out of licenses for this title.`);
    }

    writeJson(UBI_PENDING_FILE, ureqs);
}

async function main() {
    let guardSnapshot = '';
    try {
        let initialAccs = readJson(ACCOUNTS_FILE, []);
        console.log(`[STEAM FLEET] Fleet database loaded. Registered accounts: ${initialAccs.map(a => a.username).join(', ') || 'None (Empty)'}`);
    } catch (e) {}

    while (true) {
        try {
            let accs = readJson(ACCOUNTS_FILE, []);
            let accDict = {};
            for (let a of accs) accDict[a.username] = a.password;

            // Check for manual login requests
            const loginReqPath = path.join(DATA, 'login_requests.json');
            if (fs.existsSync(loginReqPath)) {
                try {
                    let reqs = readJson(loginReqPath, []);
                    if (reqs.length > 0) {
                        for (let u of reqs) {
                            if (accDict[u]) {
                                console.log(`[STEAM FLEET] Manual login request received for: ${u}`);
                                doLogin(u, accDict[u]);
                            }
                        }
                        writeJson(loginReqPath, []); // Clear requests
                    }
                } catch (e) {
                    console.error('[STEAM FLEET] Failed to process manual login requests:', e.message);
                }
            }

            // Bulk logins removed! Logins are now strictly on-demand based on the queue or guard requirements.

            if (fs.existsSync(GUARDS_FILE)) {
                let guards = readJson(GUARDS_FILE, {});
                let nextSnapshot = JSON.stringify(guards);
                if (Object.keys(guards).length > 0 && nextSnapshot !== guardSnapshot) {
                    for (let u in guards) {
                        if (accDict[u]) doLogin(u, accDict[u], guards[u]);
                    }
                    guardSnapshot = nextSnapshot;
                    writeJson(GUARDS_FILE, {});
                } else if (Object.keys(guards).length === 0) {
                    guardSnapshot = '';
                }
            }

            await processSteamQueue();
            await processUbiQueue();
        } catch (err) {
            console.error('[MAIN LOOP ERROR]', err);
        }
        await new Promise(r => setTimeout(r, 1000));
    }
}

process.on('uncaughtException', err => {
    console.error('[UNCAUGHT EXCEPTION]', err);
});
process.on('unhandledRejection', err => {
    console.error('[UNHANDLED REJECTION]', err);
});

main().catch(err => {
    console.error('[FATAL MAIN ERROR]', err);
});
