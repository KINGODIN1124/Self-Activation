const fs = require('fs');
const path = require('path');
const { Client, GatewayIntentBits, Partials, EmbedBuilder, ActionRowBuilder, ButtonBuilder, ButtonStyle, StringSelectMenuBuilder, ModalBuilder, TextInputBuilder, TextInputStyle, AttachmentBuilder, ChannelType, PermissionsBitField, REST, Routes } = require('discord.js');
const tesseract = require('tesseract.js');
const { createCanvas, loadImage, registerFont } = require('canvas');
const axios = require('axios');
const SteamUser = require('steam-user');

const BASE = __dirname;
const DATA = path.join(BASE, 'data');
const ASSETS = path.join(BASE, 'assets');
const COMPLETED_DIR = path.join(BASE, 'completed_configs');
const MAUTH = path.join(DATA, 'cachedlogins');
const TICKETS_FILE = path.join(DATA, 'tickets.json');
const VERIFICATION_FILE = path.join(DATA, 'verification_queue.json');
const STEAM_STATE_FILE = path.join(DATA, 'steam_state.json');

[DATA, ASSETS, COMPLETED_DIR, MAUTH].forEach(d => {
    if (!fs.existsSync(d)) fs.mkdirSync(d, { recursive: true });
});

let verification_queue = {};

function readJson(p, def) { try { return JSON.parse(fs.readFileSync(p, 'utf-8')); } catch { return def; } }
function writeJson(p, data) { fs.writeFileSync(p + '.tmp', JSON.stringify(data, null, 2), 'utf-8'); fs.renameSync(p + '.tmp', p); }
const fileCFG = readJson(path.join(BASE, 'config.json'), {});
function parseEnvJson(val, fallback) {
    if (!val) return fallback;
    try { return JSON.parse(val); } catch { return fallback; }
}
const CFG = {
    bot_token: process.env.BOT_TOKEN || fileCFG.bot_token,
    guild_id: process.env.GUILD_ID || fileCFG.guild_id,
    vouchChannelId: process.env.VOUCH_CHANNEL_ID || fileCFG.vouchChannelId,
    mainVouchChannelId: process.env.MAIN_VOUCH_CHANNEL_ID || fileCFG.mainVouchChannelId,
    roles: parseEnvJson(process.env.ROLES, fileCFG.roles || {}),
    categories: parseEnvJson(process.env.CATEGORIES, fileCFG.categories || {}),
    cooldowns: parseEnvJson(process.env.COOLDOWNS, fileCFG.cooldowns || { free: 604800000 })
};

function load_games() { return readJson(path.join(DATA, 'games.json'), { games: [] }).games; }
function save_games(g) { writeJson(path.join(DATA, 'games.json'), { games: g }); }
function load_cds() { return readJson(path.join(DATA, 'cooldowns.json'), {}); }
function save_cds(d) { writeJson(path.join(DATA, 'cooldowns.json'), d); }
function load_bans() { return readJson(path.join(DATA, 'bans.json'), { bans: [] }).bans; }
function save_bans(b) { writeJson(path.join(DATA, 'bans.json'), { bans: b }); }
function load_donations(state) { return readJson(path.join(DATA, `donate_${state}.json`), []); }
function save_donations(state, data) { writeJson(path.join(DATA, `donate_${state}.json`), data); }
function load_requests() { return readJson(path.join(DATA, 'pending_requests.json'), []); }
function save_requests(r) { writeJson(path.join(DATA, 'pending_requests.json'), r); }
function load_accounts() { return readJson(path.join(DATA, 'accounts.json'), []); }
function save_accounts(a) { writeJson(path.join(DATA, 'accounts.json'), a); }
function load_guards() { return readJson(path.join(DATA, 'guards.json'), {}); }
function save_guards(g) { writeJson(path.join(DATA, 'guards.json'), g); }
function load_ubi_accounts() { return readJson(path.join(DATA, 'ubi_accounts.json'), {}); }
function save_ubi_accounts(a) { writeJson(path.join(DATA, 'ubi_accounts.json'), a); }
function load_ubi_reqs() { return readJson(path.join(DATA, 'pending_ubi.json'), []); }
function save_ubi_reqs(r) { writeJson(path.join(DATA, 'pending_ubi.json'), r); }
function load_ticket_states() { return readJson(TICKETS_FILE, {}); }
function save_ticket_states(d) { writeJson(TICKETS_FILE, d); }
function load_verification_state() { return readJson(VERIFICATION_FILE, {}); }
function save_verification_state(d) { writeJson(VERIFICATION_FILE, d); }

function now_iso() { return new Date().toISOString(); }
function steam_img(app_id) { return `https://cdn.akamai.steamstatic.com/steam/apps/${app_id}/header.jpg`; }

const CLR = { AMBER: 0xF59E0B, GREEN: 0x22C55E, RED: 0xEF4444, BLUE: 0x3B82F6, DARK: 0x1E1F22, GOLD: 0xFFD700, BRONZE: 0xCD7F32, SILVER: 0xC0C0C0 };
const PANEL_TIERS = ["free", "bronze", "silver", "gold"];
const ROLE_TIER_PRIORITY = ["investor", "gold", "silver", "bronze", "free"];
const TIER_LEVEL = {}; ROLE_TIER_PRIORITY.forEach((t, i) => TIER_LEVEL[t] = i);
const PANEL_META = {
    free: { name: "Free", emoji: "🆓", color: CLR.DARK, banner_url: "https://media.discordapp.net/attachments/1456346084732571649/1474723315170938962/KCgLyPX.gif", tagline: "Community access lane for confirmed subscribers.", access_note: "Open to confirmed YouTube subscribers." },
    bronze: { name: "Bronze", emoji: "🥉", color: CLR.BRONZE, banner_url: "https://media.discordapp.net/attachments/1456346084732571649/1474723315859066931/hCGoD2M.gif", tagline: "Fast-track entry for the Bronze queue.", access_note: "Built for Bronze panel requests." },
    silver: { name: "Silver", emoji: "🥈", color: CLR.SILVER, banner_url: "https://media.discordapp.net/attachments/1456346084732571649/1474723316194349180/W3LSE6S.gif", tagline: "Priority lane with curated Silver-only selections.", access_note: "Built for Silver panel requests." },
    gold: { name: "Gold", emoji: "🥇", color: CLR.GOLD, banner_url: "https://media.discordapp.net/attachments/1456346084732571649/1474723316630552781/DrMpQpn.gif", tagline: "Top-tier access with premium Gold requests.", access_note: "Built for Gold panel requests." },
};

const ROLE_IDS = CFG.roles || {};
const CATEGORY_IDS = CFG.categories || {};
const COOLDOWN_MS = CFG.cooldowns || { free: 604800000 };
const VOUCH_CHANNEL_ID = String(CFG.vouchChannelId);
const MAIN_VOUCH_CHANNEL_ID = String(CFG.mainVouchChannelId);
const STAFF_ROLE_ID = '1472560707420557343';

function normalize_game_tiers(raw) {
    if (!raw) return [...PANEL_TIERS];
    let items = Array.isArray(raw) ? raw.map(String) : String(raw).split(',');
    let cleaned = items.map(t => t.trim().toLowerCase()).filter(t => PANEL_TIERS.includes(t));
    return cleaned.length ? Array.from(new Set(cleaned)) : [...PANEL_TIERS];
}

function format_tier_badges(tiers) { return tiers.filter(t => PANEL_META[t]).map(t => `${PANEL_META[t].emoji} \`${PANEL_META[t].name}\``).join(' '); }
function game_allowed_in_tier(game, tier) { return normalize_game_tiers(game.tiers).includes(tier); }
function get_member_access_tier(member) {
    const role_ids = member.roles.cache.map(r => r.id);
    for (let tier of ROLE_TIER_PRIORITY) if (ROLE_IDS[tier] && role_ids.includes(String(ROLE_IDS[tier]))) return tier;
    return null;
}
function can_access_panel(member, panel_tier) {
    let member_tier = get_member_access_tier(member);
    if (!member_tier || TIER_LEVEL[member_tier] === undefined || TIER_LEVEL[panel_tier] === undefined) return false;
    return TIER_LEVEL[member_tier] <= TIER_LEVEL[panel_tier];
}
function resolve_ticket_category(guild, tier) {
    let catId = CATEGORY_IDS[tier];
    if (catId) { let c = guild.channels.cache.get(String(catId)); if (c && c.type === ChannelType.GuildCategory) return c; }
    return guild.channels.cache.find(c => c.type === ChannelType.GuildCategory && c.name.toLowerCase() === `${tier.toLowerCase()} tickets`);
}
function build_ticket_topic(uid, gid, tier) { return `uid=${uid};game=${gid};tier=${tier};created=${now_iso()}`; }
function parse_ticket_topic(t) {
    if (!t) return {}; if (/^\d+$/.test(t)) return { uid: t };
    let d = {}; t.split(';').forEach(p => { let [k, v] = p.split('='); if (v) d[k.trim()] = v.trim(); });
    return d;
}
function get_game_by_id(id) { return load_games().find(g => g.id === id) || null; }
function get_ticket_state(cid) { return load_ticket_states()[String(cid)]; }
function set_ticket_state(cid, d) { let t = load_ticket_states(); t[String(cid)] = d; save_ticket_states(t); }
function update_ticket_state(cid, u) { let t = load_ticket_states(); let c = t[String(cid)] || {}; Object.assign(c, u); t[String(cid)] = c; save_ticket_states(t); }
function remove_ticket_state(cid) { let t = load_ticket_states(); delete t[String(cid)]; save_ticket_states(t); }
function set_verification_state(cid, d) { verification_queue[String(cid)] = d; let p = load_verification_state(); p[String(cid)] = d; save_verification_state(p); }
function clear_verification_state(cid) { delete verification_queue[String(cid)]; let p = load_verification_state(); delete p[String(cid)]; save_verification_state(p); }
function find_open_ticket(guild, uid, gid = null) {
    let channels = Array.from(guild.channels.cache.values()).filter(c => c.type === ChannelType.GuildText && c.name.startsWith('ticket-'));
    let matches = channels.filter(c => { let m = parse_ticket_topic(c.topic); return m.uid === String(uid) && (!gid || m.game === gid); });
    return matches.sort((a, b) => b.createdTimestamp - a.createdTimestamp)[0] || null;
}
function find_recent_user_ticket(guild, uid) {
    let channels = Array.from(guild.channels.cache.values()).filter(c => c.type === ChannelType.GuildText && c.name.startsWith('ticket-'));
    let matches = channels.filter(c => {
        let meta = parse_ticket_topic(c.topic);
        if (meta.uid !== String(uid)) return false;
        let state = get_ticket_state(c.id) || {};
        return ['awaiting_activation_confirmation', 'awaiting_vouch', 'needs_staff_help', 'verified'].includes(state.stage);
    });
    return matches.sort((a, b) => b.createdTimestamp - a.createdTimestamp)[0] || null;
}
function get_staff_role_ids(guild = null) {
    let ids = [STAFF_ROLE_ID].filter(id => id && id !== 'undefined');
    if (guild) ids = ids.filter(id => guild.roles.cache.has(id));
    return Array.from(new Set(ids));
}
function get_target_guild_ids() {
    let raw = CFG.guild_id;
    if (Array.isArray(raw)) return raw.map(String).map(v => v.trim()).filter(Boolean);
    if (typeof raw === 'string') return raw.split(',').map(v => v.trim()).filter(Boolean);
    if (raw) return [String(raw)];
    return [];
}
function is_bartender(m) {
    return m.permissions.has(PermissionsBitField.Flags.Administrator) || get_staff_role_ids(m.guild).some(id => m.roles.cache.has(id));
}
function get_remaining_cooldown_ms(member, gameId = null) {
    let memberTier = get_member_access_tier(member) || 'free';
    let cooldownMs = Number(COOLDOWN_MS[memberTier] || 0);
    if (cooldownMs <= 0) return 0;

    let cds = load_cds();
    let keys = [`ticket_${member.id}`];
    if (gameId) keys.push(`ticket_game_${member.id}_${gameId}`);

    let latestStartedAt = 0;
    for (let key of keys) {
        let record = cds[key];
        if (!record?.started_at) continue;
        let startedAt = new Date(record.started_at).getTime();
        if (!Number.isFinite(startedAt)) continue;
        latestStartedAt = Math.max(latestStartedAt, startedAt);
    }

    if (!latestStartedAt) return 0;
    let endsAt = latestStartedAt + cooldownMs;
    return Math.max(0, endsAt - Date.now());
}
function format_duration(ms) {
    let totalSeconds = Math.ceil(ms / 1000);
    let days = Math.floor(totalSeconds / 86400);
    let hours = Math.floor((totalSeconds % 86400) / 3600);
    let minutes = Math.floor((totalSeconds % 3600) / 60);
    let parts = [];
    if (days) parts.push(`${days}d`);
    if (hours) parts.push(`${hours}h`);
    if (minutes) parts.push(`${minutes}m`);
    if (!parts.length) parts.push(`${totalSeconds}s`);
    return parts.join(' ');
}
function clear_ticket_cooldown(userId) {
    let cds = load_cds();
    let removed = false;
    for (let key of Object.keys(cds)) {
        if (key === `ticket_${userId}` || key.startsWith(`ticket_game_${userId}_`)) {
            delete cds[key];
            removed = true;
        }
    }
    if (removed) save_cds(cds);
    return removed;
}
function staff_ping() {
    let ids = get_staff_role_ids();
    return ids.length ? ids.map(id => `<@&${id}>`).join(' ') : '@activation-staff';
}
async function send_staff_announcement(interaction, message) {
    await interaction.deferReply({ ephemeral: true });
    if (!is_bartender(interaction.member)) return interaction.followUp({ content: '❌ Access Denied.', ephemeral: true });

    let cleanMessage = String(message || '').trim();
    if (!cleanMessage) return interaction.followUp({ content: '❌ Message cannot be empty.', ephemeral: true });

    let embed = new EmbedBuilder()
        .setTitle('📢 Crack World Notice')
        .setDescription(cleanMessage)
        .setColor(CLR.BLUE)
        .setFooter({ text: 'Crack World Automated Systems' });
    await interaction.channel.send({ embeds: [embed] });
    await interaction.followUp({ content: '✅ Announcement sent.', ephemeral: true });
}
function build_vouch_row() {
    return new ActionRowBuilder().addComponents(
        new ButtonBuilder().setCustomId('activation_working').setLabel('✅ Working').setStyle(ButtonStyle.Success),
        new ButtonBuilder().setCustomId('activation_not_working').setLabel('❌ Not Working').setStyle(ButtonStyle.Danger)
    );
}
async function send_vouch_prompt(channel, userId, gameName) {
    let embed = new EmbedBuilder()
        .setTitle('🎫 Activation Check')
        .setColor(CLR.BLUE)
        .setDescription(
            `Your activation for **${gameName}** has been delivered.\n\n` +
            `Please confirm whether it is working:\n` +
            `• Click **Working** if everything is good, then go leave your vouch in <#${VOUCH_CHANNEL_ID}>.\n` +
            `• Click **Not Working** if you need help from activation staff.`
        );
    await channel.send({ content: `<@${userId}>`, embeds: [embed], components: [build_vouch_row()] });
}
function build_panel_rows(tier) {
    let games = load_games().filter(g => game_allowed_in_tier(g, tier));
    let steamOpts = games
        .filter(g => g.platform !== 'ubi')
        .slice(0, 25)
        .map(g => ({ label: `${g.emoji || '🎮'} ${g.name}`.substring(0, 100), description: `Steam • AppID ${g.app_id}`.substring(0, 100), value: String(g.id) }));
    let ubiOpts = games
        .filter(g => g.platform === 'ubi')
        .slice(0, 25)
        .map(g => ({ label: `${g.emoji || '⚡'} ${g.name}`.substring(0, 100), description: `Ubisoft/Denuvo • AppID ${g.app_id}`.substring(0, 100), value: String(g.id) }));

    if (!steamOpts.length) steamOpts.push({ label: "No Steam titles", value: "none" });
    if (!ubiOpts.length) ubiOpts.push({ label: "No Ubisoft titles", value: "none" });

    let steamRow = new ActionRowBuilder().addComponents(
        new StringSelectMenuBuilder().setCustomId(`direct_steam_select_${tier}`).setPlaceholder("■■■■ ★ Steam ★ ■■■■").addOptions(steamOpts)
    );
    let ubiRow = new ActionRowBuilder().addComponents(
        new StringSelectMenuBuilder().setCustomId(`direct_ubi_select_${tier}`).setPlaceholder("■■■■ 🔥 Ubisoft 🔥 ■■■■").addOptions(ubiOpts)
    );
    return [steamRow, ubiRow];
}
function get_main_vouch_banner() {
    let candidates = ['background.png', 'background.jpg', 'background.jpeg', 'background.webp', 'main_vouch_banner.png', 'main_vouch_banner.jpg'];
    for (let name of candidates) {
        let full = path.join(ASSETS, name);
        if (fs.existsSync(full)) return full;
    }
    return null;
}
function draw_centered_text(ctx, text, y, font, fillStyle, strokeStyle = 'rgba(0,0,0,0.85)', strokeWidth = 4) {
    ctx.font = font;
    ctx.textAlign = 'center';
    ctx.lineWidth = strokeWidth;
    ctx.strokeStyle = strokeStyle;
    ctx.fillStyle = fillStyle;
    ctx.strokeText(text, ctx.canvas.width / 2, y);
    ctx.fillText(text, ctx.canvas.width / 2, y);
}
function draw_text(ctx, text, x, y, font, fillStyle, align = 'left', strokeStyle = 'rgba(0,0,0,0.85)', strokeWidth = 4) {
    ctx.font = font;
    ctx.textAlign = align;
    ctx.lineWidth = strokeWidth;
    ctx.strokeStyle = strokeStyle;
    ctx.fillStyle = fillStyle;
    ctx.strokeText(text, x, y);
    ctx.fillText(text, x, y);
}
function clean_display_name(name) {
    return String(name || 'User')
        .replace(/[^\p{L}\p{N}\s._-]/gu, '')
        .replace(/\s+/g, ' ')
        .trim() || 'User';
}
async function build_main_vouch_image(user, gameName, screenshotBuffer = null) {
    let bannerPath = get_main_vouch_banner();
    if (!bannerPath) return null;
    let displayName = clean_display_name(user.displayName || user.globalName || user.username || 'User');

    let width = 960;
    let height = 540;
    let canvas = createCanvas(width, height);
    let ctx = canvas.getContext('2d');

    let bg = await loadImage(bannerPath);
    ctx.drawImage(bg, 0, 0, width, height);

    const avatarCenterX = 135;
    const avatarCenterY = 244;

    try {
        let avatarUrl = user.displayAvatarURL({ extension: 'png', size: 256 });
        let avatar = await loadImage(avatarUrl);
        let avatarSize = 138;
        let avatarX = avatarCenterX - avatarSize / 2;
        let avatarY = avatarCenterY - avatarSize / 2;

        ctx.save();
        ctx.beginPath();
        ctx.arc(avatarCenterX, avatarCenterY, avatarSize / 2, 0, Math.PI * 2);
        ctx.closePath();
        ctx.clip();
        ctx.drawImage(avatar, avatarX, avatarY, avatarSize, avatarSize);
        ctx.restore();

        ctx.beginPath();
        ctx.arc(avatarCenterX, avatarCenterY, avatarSize / 2 + 4, 0, Math.PI * 2);
        ctx.lineWidth = 4;
        ctx.strokeStyle = 'rgba(255,255,255,0.9)';
        ctx.stroke();
    } catch { }

    if (screenshotBuffer) {
        try {
            const shotX = 300;
            const shotY = 186;
            const shotW = 360;
            const shotH = 202;
            const shot = await loadImage(screenshotBuffer);

            ctx.save();
            ctx.beginPath();
            const radius = 18;
            ctx.moveTo(shotX + radius, shotY);
            ctx.lineTo(shotX + shotW - radius, shotY);
            ctx.quadraticCurveTo(shotX + shotW, shotY, shotX + shotW, shotY + radius);
            ctx.lineTo(shotX + shotW, shotY + shotH - radius);
            ctx.quadraticCurveTo(shotX + shotW, shotY + shotH, shotX + shotW - radius, shotY + shotH);
            ctx.lineTo(shotX + radius, shotY + shotH);
            ctx.quadraticCurveTo(shotX, shotY + shotH, shotX, shotY + shotH - radius);
            ctx.lineTo(shotX, shotY + radius);
            ctx.quadraticCurveTo(shotX, shotY, shotX + radius, shotY);
            ctx.closePath();
            ctx.clip();
            ctx.drawImage(shot, shotX, shotY, shotW, shotH);
            ctx.restore();

            ctx.strokeStyle = 'rgba(255,255,255,0.9)';
            ctx.lineWidth = 4;
            ctx.strokeRect(shotX, shotY, shotW, shotH);
        } catch { }
    }

    draw_text(ctx, displayName, avatarCenterX, 344, 'bold 26px Sans', '#ffffff', 'center', 'rgba(0,0,0,0.92)', 4);
    draw_centered_text(ctx, `Enjoy ${gameName}`, 420, 'bold 36px Sans', '#ffffff');
    draw_centered_text(ctx, 'Thanks for choosing Crack World Hub', 462, 'bold 28px Sans', '#dff7ff');
    draw_centered_text(ctx, 'Have Fun!!', 504, 'bold 24px Sans', '#ffe8a3', 'rgba(0,0,0,0.92)', 3);

    return canvas.toBuffer('image/jpeg', { quality: 0.82 });
}
async function send_main_vouch(mainChannel, user, gameName, reviewUrl, screenshotUrl = null) {
    await mainChannel.send({ content: `${user.toString()} enjoy your game` });

    let row = new ActionRowBuilder().addComponents(
        new ButtonBuilder().setLabel('Original Review').setStyle(ButtonStyle.Link).setURL(reviewUrl)
    );

    let imageBuffer = null;
    try {
        let screenshotBuffer = null;
        if (screenshotUrl) {
            let res = await axios.get(screenshotUrl, { responseType: 'arraybuffer' });
            screenshotBuffer = res.data;
        }
        imageBuffer = await build_main_vouch_image(user, gameName, screenshotBuffer);
    } catch { }

    if (imageBuffer) {
        await mainChannel.send({
            files: [new AttachmentBuilder(imageBuffer, { name: 'vouch-transfer.jpg' })],
            components: [row]
        });
        return;
    }

    let bannerPath = get_main_vouch_banner();
    if (bannerPath) {
        await mainChannel.send({
            files: [new AttachmentBuilder(bannerPath)],
            components: [row]
        });
    }
}

async function close_ticket_channel(channel, apply_cooldown, notice) {
    let meta = parse_ticket_topic(channel.topic);
    let state = get_ticket_state(channel.id) || {};
    if (apply_cooldown && meta.uid) {
        let cds = load_cds();
        cds[`ticket_${meta.uid}`] = { started_at: now_iso(), tier: state.tier || meta.tier || "free" };
        if (meta.game) cds[`ticket_game_${meta.uid}_${meta.game}`] = { started_at: now_iso() };
        save_cds(cds);
    }
    clear_verification_state(channel.id); remove_ticket_state(channel.id);
    try { await channel.send({ embeds: [new EmbedBuilder().setTitle("✅ Session Complete").setDescription(notice).setColor(CLR.GREEN)] }); } catch { }
    setTimeout(async () => { try { await channel.delete(); } catch { } }, 3000);
}

const client = new Client({ intents: [GatewayIntentBits.Guilds, GatewayIntentBits.GuildMessages, GatewayIntentBits.MessageContent, GatewayIntentBits.GuildMembers] });

async function trigger_worker(interaction, game, original_user) {
    let reqs = load_requests();
    reqs.push({
        request_id: `${original_user.id}-${Date.now()}`,
        user_id: String(original_user.id),
        user_tag: original_user.user.tag,
        game_id: game.id, game_name: game.name, app_id: game.app_id,
        status: "pending", timestamp: now_iso()
    });
    save_requests(reqs);

    let loading_embed = new EmbedBuilder().setTitle("⚙️ Compiling Bypass").setColor(CLR.AMBER).setDescription("Dispatching your request to the Steam worker fleet. Please keep this ticket open while your files are being prepared.");
    let status_msg = await interaction.channel.send({ embeds: [loading_embed] });

    let safe_user_tag = original_user.user.tag.replace(/[^a-zA-Z0-9]/g, '');
    let expected_file = path.join(COMPLETED_DIR, `configs_${safe_user_tag}_${game.app_id}.zip`);
    let failed_file = path.join(COMPLETED_DIR, `configs_${safe_user_tag}_${game.app_id}_failed.txt`);

    let max_wait = 90;
    let found = false;
    let failed = false;
    for (let i = 0; i < max_wait; i++) {
        if (fs.existsSync(expected_file)) { found = true; break; }
        if (fs.existsSync(failed_file)) { failed = true; break; }
        await new Promise(r => setTimeout(r, 1000));
    }

    if (found) {
        let final_embed = new EmbedBuilder().setTitle("✅ Bypass Ready").setColor(CLR.GREEN)
            .setDescription(`Your activation package for **${game.name}** is ready.\n\n**📥 Install Steps**\n**1.** Extract the ZIP file completely.\n**2.** Paste the contents directly into your main game directory.\n**3.** Start the game.\n\n⏳ This package expires in **30 minutes**.\n🛡️ **Do not close this ticket until staff review is complete.**`)
            .setImage(steam_img(game.app_id));
        
        try {
            let stats = fs.statSync(expected_file);
            if (stats.size / (1024 * 1024) > 24.5) throw new Error("File too large (over 24.5MB)");
            await status_msg.edit({ embeds: [final_embed], files: [{ attachment: expected_file, name: `${game.id}_bypass.zip` }] });
        } catch (err) {
            console.error(`[DISCORD] Failed to upload bypass file for ${game.app_id}, attempting cloud routing...`, err.message);
            try {
                await status_msg.edit({ embeds: [new EmbedBuilder().setTitle("☁️ CLOUD ROUTING").setColor(CLR.AMBER).setDescription("File too large for Discord API. Uploading to secure cloud storage...")] });
                const FormData = require('form-data');
                const form = new FormData();
                form.append('file', fs.createReadStream(expected_file));
                
                let uploadRes = await axios.post('https://tmpfiles.org/api/v1/upload', form, { headers: form.getHeaders() });
                let data = typeof uploadRes.data === 'string' ? JSON.parse(uploadRes.data) : uploadRes.data;
                let downloadLink = data.data.url;
                
                final_embed.setDescription(final_embed.data.description + `\n\n🔗 **Cloud Download Link:**\n[Click here to download your file](${downloadLink})`);
                await status_msg.edit({ embeds: [final_embed] });
            } catch (cloudErr) {
                console.error(`[DISCORD] Cloud upload failed:`, cloudErr);
                await status_msg.edit({ embeds: [new EmbedBuilder().setTitle("⚠️ UPLOAD FAILED").setColor(CLR.RED).setDescription(`Failed to upload the file to Discord and Cloud: ${cloudErr.message}\n\nThe file has been kept on the server at: \`${expected_file}\``)] });
                update_ticket_state(interaction.channel.id, { stage: "awaiting_activation_confirmation" });
                await send_vouch_prompt(interaction.channel, original_user.id, game.name);
                return;
            }
        }
        update_ticket_state(interaction.channel.id, { stage: "awaiting_activation_confirmation" });
        await send_vouch_prompt(interaction.channel, original_user.id, game.name);
        try { fs.unlinkSync(expected_file); } catch { }
    } else if (failed) {
        let reason = "unknown";
        try {
            if (fs.existsSync(failed_file)) {
                reason = fs.readFileSync(failed_file, 'utf-8').trim();
                fs.unlinkSync(failed_file);
            }
        } catch (e) {
            console.error('[DISCORD] Failed to read failed_file:', e);
        }

        let desc = `Failed to generate activation for **${game.name}**.\n\n`;
        if (reason === 'no account found') {
            desc += "❌ **No active worker account owns this game, or the account password is incorrect / invalid.**";
        } else if (reason === 'template missing or empty') {
            desc += "❌ **The server-side game template is missing or empty.**";
        } else if (reason === 'steam_settings folder missing') {
            desc += "❌ **The bot could not locate the `steam_settings` or `steamsettings` folder inside the game template.**";
        } else if (reason === 'ticket generation failed') {
            desc += "❌ **Steam ticket generation failed (the account might be rate-limited, locked, or requires a security code).**";
        } else {
            desc += `❌ **Reason:** ${reason}`;
        }
        desc += "\n\nPlease contact activation staff for assistance.";

        await status_msg.edit({
            content: `${staff_ping()} <@${original_user.id}>`,
            embeds: [new EmbedBuilder().setTitle("❌ ACTIVATION FAILED").setColor(CLR.RED).setDescription(desc)]
        });
    } else {
        await status_msg.edit({
            content: `${staff_ping()} <@${original_user.id}>`,
            embeds: [new EmbedBuilder().setTitle("❌ NO ACCOUNT FOUND").setColor(CLR.RED).setDescription(`The bot could not create a token for **${game.name}**.\n\nNo available account currently holds a valid license. Please contact activation staff for assistance.`)]
        });
    }
}

async function prompt_ubi_token(channel, user, game) {
    let embed = new EmbedBuilder().setTitle(`✅ System Verified • ${game.name}`).setColor(CLR.GREEN).setImage(steam_img(game.app_id))
        .setDescription("Your machine passed verification and the request lane is open.\n\n**🧭 Denuvo Activation Steps**\n**1.** Launch the game with the Crack World bypass files.\n**2.** When the activation box appears, copy the Denuvo request.\n**3.** Upload the exact **`token_req_<numbers>.txt`** file in this ticket.\n\n⏳ Our Ubisoft fleet is standing by for the file.");
    await channel.send({ content: `<@${user.id}>`, embeds: [embed] });
}

client.on('messageCreate', async message => {
    if (message.author.bot) return;

    if (message.content.trim().toLowerCase() === '-saveguide') {
        let embed = new EmbedBuilder()
            .setTitle('💾 Steam Emulator Save Guide')
            .setColor(CLR.BLUE)
            .setDescription(
                `### 📂 Default Save Locations\n` +
                `By default, your save files are stored in one of the following global folders:\n\n` +
                `• **Goldberg Saves:**\n` +
                `  \`C:\\Users\\<Username>\\AppData\\Roaming\\Goldberg SteamEmu Saves\\\`\n` +
                `  *or*\n` +
                `  \`C:\\Users\\<Username>\\Documents\\Goldberg SteamEmu Saves\\\`\n\n` +
                `• **Contrary Saves:**\n` +
                `  \`C:\\Users\\<Username>\\Documents\\GSE_Saves\\\`\n\n` +
                `---` +
                `### ⚙️ How to Customise Save Location\n` +
                `You can redirect your saves to any custom folder (e.g., directly inside your game directory) by editing the configuration files:\n\n` +
                `**1.** Open your game folder and navigate to the **\`steam_settings\`** directory.\n` +
                `**2.** Open **\`configs.user.ini\`** (or **\`configs.user\`** depending on the emulator) in Notepad.\n` +
                `**3.** Add the following configuration block at the bottom of the file:\n` +
                ` \`\`\`ini\n` +
                ` [user::saves]\n` +
                ` local_save_path=.\\Saves\n` +
                ` saves_folder_name=MySaveGame\n` +
                ` \`\`\`\n` +
                `**4.** Save the file and launch the game. Your saves will now be stored in the custom path specified!`
            )
            .setFooter({ text: 'Crack World Hub • Save Configuration Systems' })
            .setTimestamp();
        return message.reply({ embeds: [embed] });
    }

    if (VOUCH_CHANNEL_ID && String(message.channel.id) === String(VOUCH_CHANNEL_ID)) {
        let linked_ticket = find_open_ticket(message.guild, message.author.id) || find_recent_user_ticket(message.guild, message.author.id);

        if (!linked_ticket) {
            try { await message.delete(); } catch (e) { console.error('[VOUCH] Failed to delete message:', e.message); }
            try {
                let warn = await message.channel.send(`⚠️ <@${message.author.id}>, Do not post anything unless you have to vouch for a activation!`);
                setTimeout(() => warn.delete().catch(() => {}), 5000);
            } catch (e) {}
            return;
        }

        const botPinged = message.mentions.has(client.user);
        const staffPinged = message.mentions.roles.has(STAFF_ROLE_ID) || 
                            message.mentions.members.some(m => m.roles.cache.has(STAFF_ROLE_ID));

        if (!botPinged && !staffPinged) {
            try { await message.delete(); } catch (e) { console.error('[VOUCH] Failed to delete message:', e.message); }
            try {
                let warn = await message.channel.send(`⚠️ <@${message.author.id}>, Mention either the bot or your activator.`);
                setTimeout(() => warn.delete().catch(() => {}), 5000);
            } catch (e) {}
            return;
        }

        console.log(`[VOUCH] Bot pinged. Message received from ${message.author.tag} in vouch channel ${message.channel.id}`);
        let meta = parse_ticket_topic(linked_ticket.topic);
        let game = get_game_by_id(meta.game);
        let screenshotAttachment = message.attachments.find(att => att.contentType?.startsWith('image/') || /\.(png|jpe?g|webp|gif)$/i.test(att.name));

        let mainVouchChannel = null;
        if (MAIN_VOUCH_CHANNEL_ID) mainVouchChannel = await client.channels.fetch(String(MAIN_VOUCH_CHANNEL_ID)).catch(() => null);
        if (mainVouchChannel?.isTextBased?.() && typeof mainVouchChannel.send === 'function') {
            try {
                console.log(`[VOUCH] Forwarding vouch for ${message.author.tag} to main channel ${MAIN_VOUCH_CHANNEL_ID}`);
                await send_main_vouch(mainVouchChannel, message.author, game?.name || 'your game', message.url, screenshotAttachment?.url || null);
                console.log(`[VOUCH] Forward complete for ${message.author.tag}`);
            } catch (err) {
                console.error('[VOUCH] Failed to forward main vouch post:', err);
            }
        } else {
            console.error(`[VOUCH] Main vouch channel ${MAIN_VOUCH_CHANNEL_ID} was not found or is not sendable in guild ${message.guild.id}.`);
        }

        console.log(`[VOUCH] Closing linked ticket ${linked_ticket.id} for ${message.author.tag}`);
        await close_ticket_channel(linked_ticket, true, "Your vouch was received and forwarded. This ticket will now be closed.");
        return;
    }

    if (verification_queue[message.channel.id] && message.attachments.size > 0) {
        let att = message.attachments.first();
        if (att.name.toLowerCase().match(/\.(png|jpg|jpeg)$/)) {
            let status = await message.channel.send("🤖 **Processing Image Data...** Applying OCR algorithms.");
            try {
                let res = await axios.get(att.url, { responseType: 'arraybuffer' });
                let img = await loadImage(res.data);
                let canvas = createCanvas(img.width * 2, img.height * 2);
                let ctx = canvas.getContext('2d');
                ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
                
                let worker = await tesseract.createWorker('eng', 1, {
                    langPath: process.env.TESSERACT_LANG_PATH || BASE,
                    gzip: false
                });
                let ocr = await worker.recognize(canvas.toBuffer());
                await worker.terminate();
                let text = ocr.data.text.toLowerCase();

                let wub_pass = ['disable updates', 'update blocker', 'sordum', 'service status', 'protect services', 'apply now'].some(k => text.includes(k));
                
                let dir_pass = ['properties', 'steamapps', 'common', 'size on disk', 'bytes', 'gb'].some(k => text.includes(k));

                let errors = [];
                if (!wub_pass) errors.push("❌ Missing: **Windows Update Blocker** (Ensure 'Disable Updates' is active).");
                if (!dir_pass) errors.push("❌ Missing: **Game Properties / Folder** (Ensure Properties window or steamapps folder is visible).");

                let row = new ActionRowBuilder().addComponents(new ButtonBuilder().setCustomId('force_approve').setLabel('🛡️ Staff Override').setStyle(ButtonStyle.Danger));
                
                if (errors.length) {
                    await status.edit({ content: null, embeds: [new EmbedBuilder().setTitle("🛑 VERIFICATION REJECTED").setDescription(errors.join('\n') + "\n\n⚠️ **Please resolve these issues.**").setColor(CLR.RED)], components: [row] });
                } else {
                    await status.edit({ content: "✅ **System Verified. Security checks passed.**" });
                    let qd = verification_queue[message.channel.id];
                    let game = get_game_by_id(qd.game_id);
                    clear_verification_state(message.channel.id);
                    update_ticket_state(message.channel.id, { stage: "verified" });
                    
                    if (qd.type === 'steam') await trigger_worker({ channel: message.channel }, game, message.member);
                    else await prompt_ubi_token(message.channel, message.member, game);
                }
            } catch (e) {
                let row = new ActionRowBuilder().addComponents(new ButtonBuilder().setCustomId('force_approve').setLabel('🛡️ Staff Override').setStyle(ButtonStyle.Danger));
                await status.edit({ content: "⚠️ **OCR Failure.** Image compression too high. Please ask Staff to manually override.", components: [row] });
            }
        }
    }

    if (message.channel.name.includes("ticket-") && message.attachments.size > 0 && !verification_queue[message.channel.id]) {
        let att = message.attachments.first();
        if (att.name.startsWith("token_req")) {
            let res = await axios.get(att.url);
            let content = res.data.trim();
            if (!content.includes('|')) return await message.channel.send({ embeds: [new EmbedBuilder().setTitle("❌ Data Corrupted").setColor(CLR.RED)] });
            
            let [t_val, a_val] = content.split('|');
            let ureqs = load_ubi_reqs();
            ureqs.push({ user_id: String(message.author.id), t_val: t_val.trim(), app_id: a_val.trim() });
            save_ubi_reqs(ureqs);

            let status = await message.channel.send("⚙️ **Decrypting Denuvo Request... Pinging Ubisoft Fleet.**");
            let expected_file = path.join(COMPLETED_DIR, `ubi_token_${message.author.id}.txt`);
            let failed_file = path.join(COMPLETED_DIR, `ubi_token_${message.author.id}_failed.txt`);
            
            let success = false;
            for (let i = 0; i < 60; i++) {
                if (fs.existsSync(expected_file)) { success = true; break; }
                if (fs.existsSync(failed_file)) { fs.unlinkSync(failed_file); break; }
                await new Promise(r => setTimeout(r, 1000));
            }
            await status.delete();
            
            if (success) {
                let final_text = "**📥 Token Delivery Complete**\n**1.** Download **`token.txt`** and place it in your game folder.\n**2.** Launch the game.\n**3.** Upload a screenshot showing the game running.\n\n🛡️ Wait for staff verification before closing this session.";
                let matching_game = load_games().find(g => String(g.app_id) === a_val.trim());
                let e = new EmbedBuilder().setTitle("✅ Denuvo Token Generated").setDescription(final_text).setColor(CLR.GREEN);
                if (matching_game) e.setImage(steam_img(matching_game.app_id));
                await message.channel.send({ embeds: [e], files: [expected_file] });
                update_ticket_state(message.channel.id, { stage: "awaiting_activation_confirmation" });
                await send_vouch_prompt(message.channel, message.author.id, matching_game ? matching_game.name : 'your game');
                fs.unlinkSync(expected_file);
            } else {
                await message.channel.send({
                    content: `${staff_ping()} <@${message.author.id}>`,
                    embeds: [new EmbedBuilder().setTitle("❌ NO ACCOUNT FOUND").setDescription("The bot was unable to create a Ubisoft token because no account holds a valid license for this request.\n\nPlease contact activation staff for assistance.").setColor(CLR.RED)]
                });
            }
        }
    }
});

client.on('interactionCreate', async interaction => {
    if (interaction.isCommand()) {
        if (interaction.commandName === 'donate') {
            return interaction.reply({ content: '⚠️ Donation flow is not ported yet in the Node bot.', ephemeral: true });
        }
        if (!is_bartender(interaction.member)) return interaction.reply({ content: "❌ Access Denied.", ephemeral: true });
        if (interaction.commandName === 'say' || interaction.commandName === 'announce') {
            return send_staff_announcement(interaction, interaction.options.getString('message'));
        }
        if (interaction.commandName === 'setup') {
            let tier = interaction.options.getString('tier');
            let meta = PANEL_META[tier];
            let [steamRow, ubiRow] = build_panel_rows(tier);
            
            let embed = new EmbedBuilder().setTitle(`${meta.emoji} ${meta.name.toUpperCase()} ACTIVATION PANEL`).setColor(meta.color).setDescription(`**${meta.tagline}**\n\n**👤 Eligibility**\n${meta.access_note} Access is limited.\n\n**🧭 Activation Flow**\n1. Pick your game from the menu below.\n2. Enter your private ticket.\n3. Pass system and screenshot verification.\n4. Receive your files or token inside the ticket.\n\n**⚠️ Security Notice**\nScreenshot verification is enabled. Select only a game that is fully installed and ready.`).setImage(meta.banner_url);
            
            await interaction.channel.send({ embeds: [embed], components: [steamRow, ubiRow] });
            await interaction.reply({ content: "✅ Panel Deployed", ephemeral: true });
        }
        else if (interaction.commandName === 'addsteamgame' || interaction.commandName === 'addubigame') {
            await interaction.deferReply({ ephemeral: true });
            let games = load_games();
            let appid = interaction.options.getString('appid').trim();
            if (games.some(g => String(g.app_id) === appid)) return interaction.followUp({ content: '❌ Game with this AppID already exists.', ephemeral: true });

            let platform = interaction.commandName === 'addubigame' ? 'ubi' : 'steam';
            let name = interaction.options.getString('name').trim();
            let emoji = (interaction.options.getString('emoji') || '🎮').trim() || '🎮';
            let tiers = normalize_game_tiers(interaction.options.getString('tiers') || 'free, bronze, silver, gold');
            games.push({ id: `${platform}_${appid}`, name, app_id: appid, platform, emoji, tiers });
            save_games(games);
            return interaction.followUp({
                embeds: [new EmbedBuilder()
                    .setTitle('✅ Vault Updated')
                    .setDescription(`Added **${name}** to the ${platform === 'ubi' ? 'Ubisoft/Denuvo' : 'Steam'} library.\n\n**Panels:** ${format_tier_badges(tiers)}`)
                    .setColor(CLR.GREEN)],
                ephemeral: true
            });
        }
        else if (interaction.commandName === 'removesteamgame' || interaction.commandName === 'removeubigame' || interaction.commandName === 'removegame') {
            await interaction.deferReply({ ephemeral: true });
            let appid = interaction.options.getString('appid').trim();
            let games = load_games();
            let platform = interaction.commandName === 'removesteamgame' ? 'steam' : interaction.commandName === 'removeubigame' ? 'ubi' : null;
            let removedGame = games.find(g => String(g.app_id) === appid && (!platform || (g.platform || 'steam') === platform));
            if (!removedGame) return interaction.followUp({ content: `❌ AppID \`${appid}\` not found in the vault.`, ephemeral: true });
            games = games.filter(g => !(String(g.app_id) === appid && (!platform || (g.platform || 'steam') === platform)));
            save_games(games);
            return interaction.followUp({
                embeds: [new EmbedBuilder()
                    .setTitle('✅ Vault Updated')
                    .setDescription(`Removed **${removedGame.name}** from the library.`)
                    .setColor(CLR.GREEN)],
                ephemeral: true
            });
        }
        else if (interaction.commandName === 'addsteamacc') {
            await interaction.deferReply({ ephemeral: true });
            let accounts = load_accounts();
            let username = interaction.options.getString('username').trim();
            let password = interaction.options.getString('password');
            if (accounts.some(a => a.username.toLowerCase() === username.toLowerCase())) return interaction.followUp({ content: '❌ Account already exists in Fleet.', ephemeral: true });

            accounts.push({ username, password });
            save_accounts(accounts);
            return interaction.followUp({ embeds: [new EmbedBuilder().setTitle('⚙️ Fleet Updated').setDescription(`Steam Account \`${username}\` added to main pool. The bot will automatically test it and request a Steam Guard code if needed.`).setColor(CLR.GREEN)], ephemeral: true });
        }
        else if (interaction.commandName === 'removesteamacc') {
            await interaction.deferReply({ ephemeral: true });
            let username = interaction.options.getString('username').trim();
            let accounts = load_accounts();
            let filtered = accounts.filter(a => a.username.toLowerCase() !== username.toLowerCase());
            if (filtered.length === accounts.length) return interaction.followUp({ content: `❌ Account \`${username}\` not found in Steam Fleet.`, ephemeral: true });
            save_accounts(filtered);
            return interaction.followUp({ embeds: [new EmbedBuilder().setTitle('✅ Fleet Updated').setDescription(`Removed Steam account \`${username}\`.`).setColor(CLR.GREEN)], ephemeral: true });
        }
        else if (interaction.commandName === 'addubiacc') {
            await interaction.deferReply({ ephemeral: true });
            let data = load_ubi_accounts();
            let email = interaction.options.getString('email').trim();
            let password = interaction.options.getString('password').trim();
            let appid = interaction.options.getString('appid').trim();
            if (!data[appid]) data[appid] = [];
            if (data[appid].some(a => a.email.toLowerCase() === email.toLowerCase())) return interaction.followUp({ content: '❌ Account already exists for this AppID.', ephemeral: true });
            data[appid].push({ email, password });
            save_ubi_accounts(data);
            return interaction.followUp({ embeds: [new EmbedBuilder().setTitle('✅ Fleet Updated').setDescription(`Mapped \`${email}\` to Denuvo AppID \`${appid}\`.`).setColor(CLR.GREEN)], ephemeral: true });
        }
        else if (interaction.commandName === 'removeubiacc') {
            await interaction.deferReply({ ephemeral: true });
            let email = interaction.options.getString('email').trim();
            let data = load_ubi_accounts();
            let found = false;
            for (let appid of Object.keys(data)) {
                let originalLength = data[appid].length;
                data[appid] = data[appid].filter(a => a.email.toLowerCase() !== email.toLowerCase());
                if (data[appid].length < originalLength) found = true;
                if (!data[appid].length) delete data[appid];
            }
            if (!found) return interaction.followUp({ content: `❌ Account \`${email}\` not found in Ubisoft Fleet.`, ephemeral: true });
            save_ubi_accounts(data);
            return interaction.followUp({ embeds: [new EmbedBuilder().setTitle('✅ Fleet Updated').setDescription(`Scrubbed Ubisoft account \`${email}\` from all databases.`).setColor(CLR.GREEN)], ephemeral: true });
        }
        else if (interaction.commandName === 'removeflag') {
            await interaction.deferReply({ ephemeral: true });
            let userId = interaction.options.getString('user_id').trim();
            let flags = load_donations('flag');
            let filtered = flags.filter(f => String(f.user_id) !== userId);
            if (filtered.length === flags.length) return interaction.followUp({ content: `❌ User ID \`${userId}\` is not currently flagged.`, ephemeral: true });
            save_donations('flag', filtered);
            return interaction.followUp({ embeds: [new EmbedBuilder().setTitle('✅ Flag Removed').setDescription(`User ID \`${userId}\` has been unflagged.`).setColor(CLR.GREEN)], ephemeral: true });
        }
        else if (interaction.commandName === 'resetcooldown') {
            await interaction.deferReply({ ephemeral: true });
            let userId = interaction.options.getString('user_id').trim();
            if (!/^\d+$/.test(userId)) return interaction.followUp({ content: '❌ Please provide a valid Discord User ID.', ephemeral: true });
            if (!clear_ticket_cooldown(userId)) {
                return interaction.followUp({ embeds: [new EmbedBuilder().setTitle('❌ No Cooldown Found').setDescription(`User ID \`${userId}\` does not currently have a saved ticket cooldown.`).setColor(CLR.RED)], ephemeral: true });
            }
            return interaction.followUp({ embeds: [new EmbedBuilder().setTitle('✅ Cooldown Reset').setDescription(`Ticket cooldown cleared for User ID \`${userId}\`.`).setColor(CLR.GREEN)], ephemeral: true });
        }
        else if (interaction.commandName === 'delete') {
            if (!interaction.channel.name.includes("ticket-")) return interaction.reply({ content: "❌ Command must be used in a ticket.", ephemeral: true });
            await interaction.deferReply();
            await close_ticket_channel(interaction.channel, true, "Staff closed this ticket.");
        }
        else if (interaction.commandName === 'saveguide') {
            await interaction.deferReply();
            let embed = new EmbedBuilder()
                .setTitle('💾 Steam Emulator Save Guide')
                .setColor(CLR.BLUE)
                .setDescription(
                    `### 📂 Default Save Locations\n` +
                    `By default, your save files are stored in one of the following global folders:\n\n` +
                    `• **Goldberg Saves:**\n` +
                    `  \`C:\\Users\\<Username>\\AppData\\Roaming\\Goldberg SteamEmu Saves\\\`\n` +
                    `  *or*\n` +
                    `  \`C:\\Users\\<Username>\\Documents\\Goldberg SteamEmu Saves\\\`\n\n` +
                    `• **Contrary Saves:**\n` +
                    `  \`C:\\Users\\<Username>\\Documents\\GSE_Saves\\\`\n\n` +
                    `---` +
                    `### ⚙️ How to Customise Save Location\n` +
                    `You can redirect your saves to any custom folder (e.g., directly inside your game directory) by editing the configuration files:\n\n` +
                    `**1.** Open your game folder and navigate to the **\`steam_settings\`** directory.\n` +
                    `**2.** Open **\`configs.user.ini\`** (or **\`configs.user\`** depending on the emulator) in Notepad.\n` +
                    `**3.** Add the following configuration block at the bottom of the file:\n` +
                    ` \`\`\`ini\n` +
                    ` [user::saves]\n` +
                    ` local_save_path=.\\Saves\n` +
                    ` saves_folder_name=MySaveGame\n` +
                    ` \`\`\`\n` +
                    `**4.** Save the file and launch the game. Your saves will now be stored in the custom path specified!`
                )
                .setFooter({ text: 'Crack World Hub • Save Configuration Systems' })
                .setTimestamp();
            return interaction.followUp({ embeds: [embed] });
        }
        else if (interaction.commandName === 'refreshcache') {
            await interaction.deferReply({ ephemeral: true });
            const cachePath = path.join(DATA, 'ownership_cache.json');
            try {
                if (fs.existsSync(cachePath)) {
                    fs.unlinkSync(cachePath);
                }
                fs.writeFileSync(cachePath, '{}', 'utf-8');
                return interaction.followUp({
                    embeds: [new EmbedBuilder()
                        .setTitle('🔄 Cache Cleared')
                        .setDescription('Ownership cache has been cleared. The worker fleet will now re-authenticate accounts on-demand to rebuild the game ownership database.')
                        .setColor(CLR.GREEN)],
                    ephemeral: true
                });
            } catch (err) {
                return interaction.followUp({ content: `❌ Failed to clear cache: ${err.message}`, ephemeral: true });
            }
        }
    } else if (interaction.isStringSelectMenu()) {
        if (interaction.customId.startsWith('direct_steam_select_') || interaction.customId.startsWith('direct_ubi_select_')) {
            await interaction.deferReply({ ephemeral: true });
            let tier = interaction.customId.split('_').pop();
            try {
                await interaction.message.edit({ components: build_panel_rows(tier) });
            } catch { }
            let gid = interaction.values[0];
            if (gid === "none") return interaction.followUp({ content: "❌ No games available." });
            
            let game = get_game_by_id(gid);
            if (!game) return interaction.followUp({ content: "❌ Game not found." });
            
            if (!can_access_panel(interaction.member, tier)) return interaction.followUp({ content: "🚫 You do not have the role for this panel." });
            let remainingCooldown = get_remaining_cooldown_ms(interaction.member, game.id);
            if (remainingCooldown > 0) {
                return interaction.followUp({ content: `⏳ You are still on cooldown. Please wait **${format_duration(remainingCooldown)}** before opening another activation ticket.` });
            }
            
            let openTicket = find_open_ticket(interaction.guild, interaction.user.id);
            if (openTicket) {
                let meta = parse_ticket_topic(openTicket.topic);
                let openGame = get_game_by_id(meta.game);
                return interaction.followUp({ content: `🎫 You already have an active ticket open in <#${openTicket.id}>${openGame ? ` (for **${openGame.name}**)` : ''}. Please close or complete that session before opening a new one.` });
            }

            let cat = resolve_ticket_category(interaction.guild, tier);
            let safeName = interaction.user.username.replace(/[^a-zA-Z0-9-]/g, '').substring(0, 20) || 'user';
            let memberTier = get_member_access_tier(interaction.member) || tier;
            
            let overwrites = [
                { id: interaction.guild.id, deny: [PermissionsBitField.Flags.ViewChannel] },
                { id: interaction.user.id, allow: [PermissionsBitField.Flags.ViewChannel, PermissionsBitField.Flags.SendMessages, PermissionsBitField.Flags.AttachFiles] }
            ];
            for (let staffRoleId of get_staff_role_ids(interaction.guild)) {
                overwrites.push({ id: staffRoleId, allow: [PermissionsBitField.Flags.ViewChannel, PermissionsBitField.Flags.SendMessages, PermissionsBitField.Flags.ManageMessages] });
            }

            let ticket = await interaction.guild.channels.create({
                name: `ticket-${safeName}`,
                type: ChannelType.GuildText,
                parent: cat,
                permissionOverwrites: overwrites,
                topic: build_ticket_topic(interaction.user.id, game.id, tier)
            });

            set_ticket_state(ticket.id, { user_id: String(interaction.user.id), game_id: game.id, req_type: game.platform === 'ubi' ? 'ubi' : 'steam', tier: memberTier, panel_tier: tier, stage: "awaiting_ready", created_at: now_iso() });
            
            let row = new ActionRowBuilder().addComponents(new ButtonBuilder().setCustomId('init_yes').setLabel('✅ Yes, My Game Is Ready').setStyle(ButtonStyle.Success));
            let initEmb = new EmbedBuilder().setTitle(`ACTIVATION SESSION`).setColor(CLR.BLUE)
                .setDescription(`### 🎯 Request Locked In\n<@${interaction.user.id}>, your private ticket is ready for **${game.name}**.\n\n### ⚠️ Required Before You Continue\n• The full game must already be installed.\n• Keep Windows Update blocked.\n\n### ✅ Next Step\nPress **Yes, My Game Is Ready** once everything above is done.`).setImage(steam_img(game.app_id));
            
            await ticket.send({ content: `<@${interaction.user.id}>`, embeds: [initEmb], components: [row] });
            await interaction.followUp({ content: `✅ Your secure request channel is ready: <#${ticket.id}>` });
        }
    } else if (interaction.isButton()) {
        if (interaction.customId === 'init_yes') {
            await interaction.deferReply();
            let s = get_ticket_state(interaction.channel.id);
            if (!s || s.user_id !== String(interaction.user.id)) return interaction.followUp({ content: "❌ This isn't your ticket or it was lost.", ephemeral: true });
            await interaction.message.edit({ components: [] });
            update_ticket_state(interaction.channel.id, { stage: "awaiting_verification" });
            
            let r = new ActionRowBuilder().addComponents(new ButtonBuilder().setCustomId('start_ocr').setLabel('📸 Upload Screenshot Now').setStyle(ButtonStyle.Primary));
            await interaction.followUp({ content: "Ready for OCR verification.", components: [r] });
        } else if (interaction.customId === 'start_ocr') {
            await interaction.deferUpdate();
            let s = get_ticket_state(interaction.channel.id);
            if (!s || s.user_id !== String(interaction.user.id)) return;
            set_verification_state(interaction.channel.id, { type: s.req_type, game_id: s.game_id, user_id: s.user_id });
            update_ticket_state(interaction.channel.id, { stage: "awaiting_screenshot" });
            await interaction.message.edit({ components: [] });
            let emb = new EmbedBuilder().setTitle("🔍 System Verification Required").setColor(CLR.BLUE)
                .setDescription("Before the request can continue, the bot needs one proof screenshot showing:\n📂 **1.** The game's `Properties` window proving the install is larger than **10GB**.\n🛡️ **2.** `Windows Update Blocker` showing **Disable Updates** is active.");
            const examplePath = path.join(ASSETS, 'wub_example.png');
            if (fs.existsSync(examplePath)) {
                emb.setImage('attachment://wub_example.png');
                await interaction.channel.send({ content: `<@${interaction.user.id}>`, embeds: [emb], files: [examplePath] });
            } else {
                await interaction.channel.send({ content: `<@${interaction.user.id}>`, embeds: [emb] });
            }
        } else if (interaction.customId === 'force_approve') {
            if (!is_bartender(interaction.member)) return interaction.reply({ content: "❌ Only staff can override.", ephemeral: true });
            let q = verification_queue[String(interaction.channel.id)] || load_verification_state()[String(interaction.channel.id)];
            if (!q) return interaction.reply({ content: "❌ No saved verification step.", ephemeral: true });
            
            await interaction.message.edit({ components: [] });
            await interaction.reply({ content: "🛡️ **Staff Override Applied.** Bypassing security checks..." });
            
            let game = get_game_by_id(q.game_id);
            let targetUser = await interaction.guild.members.fetch(q.user_id).catch(()=>null);
            clear_verification_state(interaction.channel.id);
            update_ticket_state(interaction.channel.id, { stage: "verified" });
            
            if (q.type === 'steam') await trigger_worker(interaction, game, targetUser);
            else await prompt_ubi_token(interaction.channel, targetUser, game);
        } else if (interaction.customId === 'activation_working') {
            await interaction.deferReply({ ephemeral: true });
            let s = get_ticket_state(interaction.channel.id);
            if (!s || s.user_id !== String(interaction.user.id)) return interaction.followUp({ content: "❌ This isn't your activation ticket.", ephemeral: true });
            let game = get_game_by_id(s.game_id);
            update_ticket_state(interaction.channel.id, { stage: "awaiting_vouch" });
            await interaction.message.edit({ components: [] }).catch(() => null);
            await interaction.channel.send({
                content: `<@${interaction.user.id}>`,
                embeds: [
                    new EmbedBuilder()
                        .setTitle('⭐ Leave Your Vouch')
                        .setColor(CLR.GREEN)
                        .setDescription(`Glad everything is working for **${game?.name || 'your game'}**.\n\nPlease go to <#${VOUCH_CHANNEL_ID}> and send your vouch there. Once your vouch is sent, this ticket will close automatically.`)
                ]
            });
            await interaction.followUp({ content: "✅ Vouch instructions sent in your ticket.", ephemeral: true });
        } else if (interaction.customId === 'activation_not_working') {
            await interaction.deferReply({ ephemeral: true });
            let s = get_ticket_state(interaction.channel.id);
            if (!s || s.user_id !== String(interaction.user.id)) return interaction.followUp({ content: "❌ This isn't your activation ticket.", ephemeral: true });
            let game = get_game_by_id(s.game_id);
            update_ticket_state(interaction.channel.id, { stage: "needs_staff_help" });
            await interaction.message.edit({ components: [] }).catch(() => null);
            await interaction.channel.send({
                content: `${staff_ping()} <@${interaction.user.id}>`,
                embeds: [
                    new EmbedBuilder()
                        .setTitle('🛠️ Activation Staff Assistance Needed')
                        .setColor(CLR.RED)
                        .setDescription(`The user reported that **${game?.name || 'this activation'}** is not working.\n\nActivation staff, please review this ticket and assist.`)
                ]
            });
            await interaction.followUp({ content: "✅ Activation staff have been notified in your ticket.", ephemeral: true });
        } else if (interaction.customId.startsWith('guard_prompt_')) {
            let username = interaction.customId.replace('guard_prompt_', '');
            let modal = new ModalBuilder()
                .setCustomId(`guard_submit_${username}`)
                .setTitle('Steam Guard Code');
            
            let codeInput = new TextInputBuilder()
                .setCustomId('code_input')
                .setLabel("Enter the Steam Guard Code")
                .setStyle(TextInputStyle.Short)
                .setRequired(true);
                
            modal.addComponents(new ActionRowBuilder().addComponents(codeInput));
            await interaction.showModal(modal);
        }
    } else if (interaction.isModalSubmit()) {
        if (interaction.customId.startsWith('guard_submit_')) {
            let username = interaction.customId.replace('guard_submit_', '');
            let code = interaction.fields.getTextInputValue('code_input').trim();
            
            let guards = load_guards();
            guards[username] = code;
            save_guards(guards);
            
            await interaction.reply({ embeds: [new EmbedBuilder().setTitle('✅ Guard Code Saved').setDescription(`Guard code for \`${username}\` has been passed to the Steam Fleet.`).setColor(CLR.GREEN)], ephemeral: true });
        }
    }
});

const commands = [
    { name: 'donate', description: 'Donate an account for roles' },
    { name: 'say', description: 'Post a bot-style message', options: [{ name: 'message', description: 'The message the bot should say', type: 3, required: true }] },
    { name: 'announce', description: 'Post a bot-style announcement', options: [{ name: 'message', description: 'The announcement the bot should post', type: 3, required: true }] },
    { name: 'setup', description: 'Deploy a specific tier request panel', options: [{ name: 'tier', description: 'Which panel tier to deploy', type: 3, required: true, choices: PANEL_TIERS.map(t => ({name: t, value: t})) }] },
    { name: 'addsteamgame', description: 'Add a Steam game', options: [
        { name: 'name', description: 'Game name', type: 3, required: true },
        { name: 'appid', description: 'Steam AppID', type: 3, required: true },
        { name: 'emoji', description: 'Emoji', type: 3, required: false },
        { name: 'tiers', description: 'Panel tiers comma separated', type: 3, required: false }
    ]},
    { name: 'addubigame', description: 'Add a Ubisoft/Denuvo game', options: [
        { name: 'name', description: 'Game name', type: 3, required: true },
        { name: 'appid', description: 'Store AppID', type: 3, required: true },
        { name: 'emoji', description: 'Emoji', type: 3, required: false },
        { name: 'tiers', description: 'Panel tiers comma separated', type: 3, required: false }
    ]},
    { name: 'removesteamgame', description: 'Remove a Steam game', options: [{ name: 'appid', description: 'Steam AppID', type: 3, required: true }] },
    { name: 'removeubigame', description: 'Remove a Ubisoft/Denuvo game', options: [{ name: 'appid', description: 'Ubisoft/Denuvo AppID', type: 3, required: true }] },
    { name: 'removegame', description: 'Remove a game from the menu', options: [{ name: 'appid', description: 'The AppID of the game to remove', type: 3, required: true }] },
    { name: 'addsteamacc', description: 'Add a Steam account', options: [
        { name: 'username', description: 'Steam username', type: 3, required: true },
        { name: 'password', description: 'Steam password', type: 3, required: true }
    ]},
    { name: 'removesteamacc', description: 'Remove a Steam account', options: [{ name: 'username', description: 'The exact Steam username to remove', type: 3, required: true }] },
    { name: 'addubiacc', description: 'Add a Ubisoft account', options: [
        { name: 'email', description: 'Ubisoft email', type: 3, required: true },
        { name: 'password', description: 'Ubisoft password', type: 3, required: true },
        { name: 'appid', description: 'Denuvo AppID', type: 3, required: true }
    ]},
    { name: 'removeubiacc', description: 'Remove a Ubisoft account', options: [{ name: 'email', description: 'The exact Ubisoft email to remove', type: 3, required: true }] },
    { name: 'removeflag', description: 'Unflag a user so they can donate again', options: [{ name: 'user_id', description: 'The Discord User ID to unflag', type: 3, required: true }] },
    { name: 'resetcooldown', description: 'Reset a user ticket cooldown', options: [{ name: 'user_id', description: 'The Discord User ID whose ticket cooldown should be cleared', type: 3, required: true }] },
    { name: 'delete', description: 'Securely close and wipe ticket' },
    { name: 'saveguide', description: 'Show the Steam Emulator save game path configuration guide' },
    { name: 'refreshcache', description: 'Clear ownership cache and force rebuild of the fleet database' }
];
client.once('clientReady', async () => {
    console.log(`Logged in as ${client.user.tag}`);
    try {
        const rest = new REST({ version: '10' }).setToken(CFG.bot_token);
        const guildIds = get_target_guild_ids();
        if (guildIds.length) {
            for (const guildId of guildIds) {
                try {
                    console.log(`Syncing ${commands.length} slash commands to guild ${guildId}...`);
                    await rest.put(
                        Routes.applicationGuildCommands(client.user.id, guildId),
                        { body: commands }
                    );
                    console.log(`Slash commands synced to guild ${guildId}.`);
                } catch (e) {
                    console.error(`Slash command sync error for guild ${guildId}:`, e);
                }
            }
        } else {
            console.log(`Syncing ${commands.length} global slash commands...`);
            await rest.put(
                Routes.applicationCommands(client.user.id),
                { body: commands }
            );
            console.log(`Global slash commands synced.`);
        }
        const joinedGuilds = client.guilds.cache.map(g => `${g.name} (${g.id})`);
        console.log(`Bot is currently in: ${joinedGuilds.join(', ')}`);
        
        let notified_guards = {};
        setInterval(async () => {
            if (!fs.existsSync(STEAM_STATE_FILE)) return;
            let states = readJson(STEAM_STATE_FILE, {});
            
            // Target channel for Steam Guard prompts
            const guardChannelId = process.env.GUARD_CHANNEL_ID || '1472543211204378625';
            let channel = client.channels.cache.get(String(guardChannelId));
            if (!channel) return;

            for (let u in states) {
                if (states[u].guard_needed) {
                    let needsNotification = false;
                    let isRetry = !!states[u].last_code_wrong;
                    
                    if (!notified_guards[u]) {
                        needsNotification = true;
                    } else if (isRetry && notified_guards[u] === 'sent') {
                        needsNotification = true;
                    }

                    if (needsNotification) {
                        let row = new ActionRowBuilder().addComponents(
                            new ButtonBuilder().setCustomId(`guard_prompt_${u}`).setLabel('Enter Steam Guard Code').setStyle(ButtonStyle.Danger)
                        );
                        
                        let messageText = `${staff_ping()} ⚠️ **Steam Guard Required** for worker account \`${u}\`.\n`;
                        if (isRetry) {
                            messageText += `❌ **The previous code you entered was incorrect or expired.** Please click below to try again with a new code.`;
                        } else {
                            messageText += `The worker fleet is stuck. Click below to provide the code.`;
                        }

                        try {
                            await channel.send({ content: messageText, components: [row] });
                            notified_guards[u] = isRetry ? 'wrong_notified' : 'sent';
                        } catch(e) {}
                    }
                } else if (!states[u].guard_needed && notified_guards[u]) {
                    delete notified_guards[u]; // reset when no longer needed
                }
            }
        }, 5000);
        
    } catch (e) { console.error("Slash command sync error:", e); }
});

client.login(CFG.bot_token);
