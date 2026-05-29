import discord
from discord import app_commands, Interaction
from discord.ui import View, Button, Select, Modal, TextInput
import asyncio
import aiohttp
import json
import os
import re
import tempfile
import shutil
import io
import queue
import collections
import pytesseract
from PIL import Image, ImageEnhance, ImageDraw, ImageFont, ImageOps
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional
from discord.ext import tasks
from steam.client import SteamClient
from steam.enums import EResult
from eventemitter.emitter import EventEmitter
from steam.client.builtins.friends import SteamFriendlist
from steam.client.gc import GameCoordinator
import time

BASE = Path(__file__).parent

if os.name == 'nt': 
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
else:
    pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'

DATA = BASE / "data"
ASSETS = BASE / "assets"
COMPLETED_DIR = BASE / "completed_configs"
MAUTH = DATA / "cachedlogins"
TICKETS_FILE = DATA / "tickets.json"
VERIFICATION_FILE = DATA / "verification_queue.json"
DATA.mkdir(parents=True, exist_ok=True)
ASSETS.mkdir(parents=True, exist_ok=True)
COMPLETED_DIR.mkdir(parents=True, exist_ok=True)
MAUTH.mkdir(parents=True, exist_ok=True)

verification_queue = {}

def _init_sync_emitter(target):
    target._loop = None
    target._listeners = collections.defaultdict(list)
    target._once = collections.defaultdict(list)
    target._max_listeners = EventEmitter.DEFAULT_MAX_LISTENERS

def _sync_emit(target, event, *args):
    listeners = target._listeners[event][:]
    once_listeners = target._once[event][:]
    target._once[event] = []
    for listener in listeners + once_listeners:
        listener(*args)
    return target

def _safe_remove_all_listeners(target, event=None):
    if event is None:
        target._listeners = collections.defaultdict(list)
        target._once = collections.defaultdict(list)
    else:
        target._listeners.pop(event, None)
        target._once.pop(event, None)

_orig_friendlist_init = SteamFriendlist.__init__
def _patched_friendlist_init(self, client, logger_name="SteamFriendList"):
    _init_sync_emitter(self)
    _orig_friendlist_init(self, client, logger_name)

def _patched_friendlist_emit(self, event, *args):
    if event is not None:
        self._LOG.debug("Emit event: %s" % repr(event))
    return _sync_emit(self, event, *args)

_orig_gc_init = GameCoordinator.__init__
def _patched_gc_init(self, steam_client, app_id):
    _init_sync_emitter(self)
    _orig_gc_init(self, steam_client, app_id)

def _patched_gc_emit(self, event, *args):
    if event is not None:
        self._LOG.debug("Emit event: %s" % repr(event))
    return _sync_emit(self, event, *args)

SteamFriendlist.__init__ = _patched_friendlist_init
SteamFriendlist.emit = _patched_friendlist_emit
GameCoordinator.__init__ = _patched_gc_init
GameCoordinator.emit = _patched_gc_emit

def read_json(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except: return default

def write_json(path: Path, data):
    fd, temp_path = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f: json.dump(data, f, indent=2)
    os.replace(temp_path, path)

CFG = read_json(BASE / "config.json", {})

def load_games(): 
    games = read_json(DATA / "games.json", {"games": []})["games"]
    modified = False
    for g in games:
        if 'platform' not in g:
            if str(g['app_id']) in ["7013", "6101", "1081", "5092", "5093", "13504", "6100", "8006", "5059", "4740"]:
                g['platform'] = 'ubi'
            else:
                g['platform'] = 'steam'
            modified = True
        normalized_tiers = normalize_game_tiers(g.get("tiers"))
        if g.get("tiers") != normalized_tiers:
            g["tiers"] = normalized_tiers
            modified = True
    if modified: save_games(games)
    return games

def save_games(g): write_json(DATA / "games.json", {"games": g})
def load_orders(): return read_json(DATA / "orders.json", {"orders": []})["orders"]
def save_orders(o): write_json(DATA / "orders.json", {"orders": o})
def load_cds(): return read_json(DATA / "cooldowns.json", {})
def save_cds(d): write_json(DATA / "cooldowns.json", d)
def load_bans(): return read_json(DATA / "bans.json", {"bans": []})["bans"]
def save_bans(b): write_json(DATA / "bans.json", {"bans": b})
def load_requests(): return read_json(DATA / "pending_requests.json", [])
def save_requests(r): write_json(DATA / "pending_requests.json", r)
def load_accounts(): return read_json(DATA / "accounts.json", [])
def save_accounts(a): write_json(DATA / "accounts.json", a)
def load_ubi_accounts(): return read_json(DATA / "ubi_accounts.json", {})
def save_ubi_accounts(a): write_json(DATA / "ubi_accounts.json", a)
def load_ubi_reqs(): return read_json(DATA / "pending_ubi.json", [])
def save_ubi_reqs(r): write_json(DATA / "pending_ubi.json", r)
def load_ticket_states(): return read_json(TICKETS_FILE, {})
def save_ticket_states(data): write_json(TICKETS_FILE, data)
def load_verification_state(): return read_json(VERIFICATION_FILE, {})
def save_verification_state(data): write_json(VERIFICATION_FILE, data)

def load_donations(state): return read_json(DATA / f"donate_{state}.json", [])
def save_donations(state, d): write_json(DATA / f"donate_{state}.json", d)

def now_iso() -> str: return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
def steam_img(app_id: str) -> str: return f"https://cdn.akamai.steamstatic.com/steam/apps/{app_id}/header.jpg"

class CLR:
    AMBER = 0xF59E0B
    GREEN = 0x22C55E
    RED = 0xEF4444
    BLUE = 0x3B82F6
    DARK = 0x1E1F22
    GOLD = 0xFFD700
    BRONZE = 0xCD7F32
    SILVER = 0xC0C0C0

PANEL_TIERS = ("free", "bronze", "silver", "gold")
ACCESS_TIERS = ("free", "bronze", "silver", "gold", "investor")
ROLE_TIER_PRIORITY = ("investor", "gold", "silver", "bronze", "free")
TIER_LEVEL = {tier: idx for idx, tier in enumerate(ROLE_TIER_PRIORITY)}
PANEL_META = {
    "free": {
        "name": "Free",
        "emoji": "🆓",
        "color": CLR.DARK,
        "banner_url": "https://media.discordapp.net/attachments/1456346084732571649/1474723315170938962/KCgLyPX.gif",
        "tagline": "Community access lane for confirmed subscribers.",
        "access_note": "Open to confirmed YouTube subscribers.",
    },
    "bronze": {
        "name": "Bronze",
        "emoji": "🥉",
        "color": CLR.BRONZE,
        "banner_url": "https://media.discordapp.net/attachments/1456346084732571649/1474723315859066931/hCGoD2M.gif",
        "tagline": "Fast-track entry for the Bronze queue.",
        "access_note": "Built for Bronze panel requests.",
    },
    "silver": {
        "name": "Silver",
        "emoji": "🥈",
        "color": CLR.SILVER,
        "banner_url": "https://media.discordapp.net/attachments/1456346084732571649/1474723316194349180/W3LSE6S.gif",
        "tagline": "Priority lane with curated Silver-only selections.",
        "access_note": "Built for Silver panel requests.",
    },
    "gold": {
        "name": "Gold",
        "emoji": "🥇",
        "color": CLR.GOLD,
        "banner_url": "https://media.discordapp.net/attachments/1456346084732571649/1474723316630552781/DrMpQpn.gif",
        "tagline": "Top-tier access with premium Gold requests.",
        "access_note": "Built for Gold panel requests.",
    },
}

ROLE_IDS = {tier: int(role_id) for tier, role_id in CFG.get("roles", {}).items() if str(role_id).isdigit()}
CATEGORY_IDS = {tier: int(cat_id) for tier, cat_id in CFG.get("categories", {}).items() if str(cat_id).isdigit()}
COOLDOWN_MS = {tier: int(value) for tier, value in CFG.get("cooldowns", {}).items()}
VOUCH_CHANNEL_ID = int(CFG.get("vouchChannelId", 0) or 0)
MAIN_VOUCH_CHANNEL_ID = int(CFG.get("mainVouchChannelId", 0) or 0)
MAIN_VOUCH_IMAGE_CANDIDATES = (
    ASSETS / "background.jpg",
    ASSETS / "background.png",
    ASSETS / "background.jpeg",
    ASSETS / "background.webp",
    ASSETS / "main_vouch_banner.png",
    ASSETS / "main_vouch_banner.jpg",
    ASSETS / "main_vouch_banner.jpeg",
    ASSETS / "main_vouch_banner.webp",
)

def normalize_game_tiers(raw_tiers) -> list[str]:
    if raw_tiers is None:
        return list(PANEL_TIERS)
    if isinstance(raw_tiers, str):
        items = [t.strip().lower() for t in raw_tiers.split(",")]
    elif isinstance(raw_tiers, list):
        items = [str(t).strip().lower() for t in raw_tiers]
    else:
        items = []

    cleaned = []
    for tier in items:
        if tier in PANEL_TIERS and tier not in cleaned:
            cleaned.append(tier)
    return cleaned or list(PANEL_TIERS)

def format_tier_badges(tiers: list[str]) -> str:
    return " ".join(f"{PANEL_META[t]['emoji']} `{PANEL_META[t]['name']}`" for t in tiers if t in PANEL_META)

def game_allowed_in_tier(game: dict, tier: str) -> bool:
    return tier in normalize_game_tiers(game.get("tiers"))

def get_panel_meta(tier: str) -> dict:
    return PANEL_META.get(tier, PANEL_META["free"])

def format_duration_ms(duration_ms: int) -> str:
    total_seconds = max(0, duration_ms // 1000)
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)

def parse_iso(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None

def get_member_access_tier(member: discord.Member) -> Optional[str]:
    roles = getattr(member, "roles", [])
    role_ids = {role.id for role in roles}
    for tier in ROLE_TIER_PRIORITY:
        if ROLE_IDS.get(tier) in role_ids:
            return tier
        if any(role.name and tier.lower() in role.name.lower() for role in roles if getattr(role, "name", None)):
            return tier
    return None

def can_access_panel(member: discord.Member, panel_tier: str) -> bool:
    member_tier = get_member_access_tier(member)
    if not member_tier or panel_tier not in TIER_LEVEL or member_tier not in TIER_LEVEL:
        return False
    return TIER_LEVEL[member_tier] <= TIER_LEVEL[panel_tier]

def resolve_ticket_category(guild: discord.Guild, tier: str) -> Optional[discord.CategoryChannel]:
    category_id = CATEGORY_IDS.get(tier)
    if category_id:
        channel = guild.get_channel(category_id)
        if isinstance(channel, discord.CategoryChannel):
            return channel
    return discord.utils.get(guild.categories, name=f"{tier.title()} Tickets")

def get_cooldown_ms_for_tier(tier: Optional[str]) -> int:
    if not tier:
        return int(COOLDOWN_MS.get("free", 604800000))
    return int(COOLDOWN_MS.get(tier, COOLDOWN_MS.get("free", 604800000)))

def build_ticket_topic(user_id: str, game_id: str, tier: str) -> str:
    return f"uid={user_id};game={game_id};tier={tier};created={now_iso()}"

def parse_ticket_topic(topic: Optional[str]) -> dict:
    if not topic:
        return {}
    if topic.isdigit():
        return {"uid": topic}
    data = {}
    for part in topic.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        data[key.strip()] = value.strip()
    return data

def get_game_by_id(game_id: Optional[str]) -> Optional[dict]:
    if not game_id:
        return None
    return next((g for g in load_games() if g["id"] == game_id), None)

def get_ticket_state(channel_id: int) -> Optional[dict]:
    return load_ticket_states().get(str(channel_id))

def set_ticket_state(channel_id: int, data: dict):
    tickets = load_ticket_states()
    tickets[str(channel_id)] = data
    save_ticket_states(tickets)

def update_ticket_state(channel_id: int, **updates):
    tickets = load_ticket_states()
    key = str(channel_id)
    current = tickets.get(key, {})
    current.update(updates)
    tickets[key] = current
    save_ticket_states(tickets)

def remove_ticket_state(channel_id: int):
    tickets = load_ticket_states()
    tickets.pop(str(channel_id), None)
    save_ticket_states(tickets)

def set_verification_state(channel_id: int, data: dict):
    verification_queue[channel_id] = data
    persisted = load_verification_state()
    persisted[str(channel_id)] = data
    save_verification_state(persisted)

def clear_verification_state(channel_id: int):
    verification_queue.pop(channel_id, None)
    persisted = load_verification_state()
    persisted.pop(str(channel_id), None)
    save_verification_state(persisted)

def find_open_ticket(guild: discord.Guild, user_id: int, game_id: Optional[str] = None) -> Optional[discord.TextChannel]:
    tickets = []
    for channel in guild.text_channels:
        if not channel.name.startswith("ticket-"):
            continue
        meta = parse_ticket_topic(channel.topic)
        if meta.get("uid") != str(user_id):
            continue
        if game_id and meta.get("game") != game_id:
            continue
        tickets.append(channel)
    tickets.sort(key=lambda ch: ch.created_at, reverse=True)
    return tickets[0] if tickets else None

def set_ticket_cooldown(user_id: str, tier: Optional[str] = None, game_id: Optional[str] = None):
    cds = load_cds()
    now = now_iso()
    cooldown_tier = tier or "free"
    if get_cooldown_ms_for_tier(cooldown_tier) > 0:
        cds[f"ticket_{user_id}"] = {"started_at": now, "tier": cooldown_tier}
    if game_id:
        cds[f"ticket_game_{user_id}_{game_id}"] = {"started_at": now}
    save_cds(cds)

def get_ticket_cooldown(user_id: str, tier: Optional[str] = None) -> Optional[str]:
    cds = load_cds()
    last = cds.get(f"ticket_{user_id}")
    if not last:
        return None

    cooldown_tier = tier or "free"
    duration_ms = get_cooldown_ms_for_tier(cooldown_tier)
    if duration_ms <= 0:
        return None

    started_at = last.get("started_at") if isinstance(last, dict) else last
    started_dt = parse_iso(started_at) if started_at else None
    if not started_dt:
        return None

    rem = timedelta(milliseconds=duration_ms) - (datetime.now(timezone.utc).replace(tzinfo=None) - started_dt)
    if rem.total_seconds() > 0:
        return format_duration_ms(int(rem.total_seconds() * 1000))
    return None

def get_same_game_cooldown(user_id: str, game_id: str, duration_ms: int = 86400000) -> Optional[str]:
    cds = load_cds()
    last = cds.get(f"ticket_game_{user_id}_{game_id}")
    if not last:
        return None
    started_at = last.get("started_at") if isinstance(last, dict) else last
    started_dt = parse_iso(started_at) if started_at else None
    if not started_dt:
        return None
    rem = timedelta(milliseconds=duration_ms) - (datetime.now(timezone.utc).replace(tzinfo=None) - started_dt)
    if rem.total_seconds() > 0:
        return format_duration_ms(int(rem.total_seconds() * 1000))
    return None

STAFF_ROLE_ID = int(CFG.get("staff_role_id", 0))

def get_ban(user_id: str):
    bans = load_bans()
    b = next((x for x in bans if x["userId"] == str(user_id)), None)
    if not b: return None
    if b.get("until") and datetime.fromisoformat(b["until"]) < datetime.now(timezone.utc).replace(tzinfo=None):
        save_bans([x for x in bans if x["userId"] != str(user_id)])
        return None
    return b

def clear_ticket_cooldown(user_id: str) -> bool:
    cds = load_cds()
    prefix = f"ticket_{user_id}"
    keys = [key for key in cds if key == prefix or key.startswith(f"ticket_game_{user_id}_")]
    if not keys:
        return False
    for key in keys:
        del cds[key]
    save_cds(cds)
    return True

def is_bartender(interaction: Interaction) -> bool:
    if interaction.user.guild_permissions.administrator: return True
    if isinstance(interaction.user, discord.Member):
        for role in interaction.user.roles:
            if role.id == STAFF_ROLE_ID: return True
    return False

def is_duplicate_account(email: str, current_user_id: str) -> bool:
    for st in ["tier1", "tier2", "tier3", "tier4plus", "pending", "error", "semi_flag", "flag"]:
        data = load_donations(st)
        for entry in data:
            if entry.get("email", "").lower() == email.lower():
                if str(entry.get("user_id")) != str(current_user_id):
                    return True
    return False

async def upload_to_gofile(file_path: Path) -> Optional[str]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.gofile.io/servers") as resp:
                data = await resp.json()
                if data.get("status") != "ok": return None
                server = data["data"]["servers"][0]["name"]
            with open(file_path, "rb") as f:
                form = aiohttp.FormData()
                form.add_field("file", f, filename=file_path.name)
                async with session.post(f"https://{server}.gofile.io/contents/uploadfile", data=form) as upload_resp:
                    upload_data = await upload_resp.json()
                    if upload_data.get("status") == "ok":
                        return upload_data["data"]["downloadPage"]
    except: pass
    return None

def format_relative_timestamp(started_at: Optional[datetime]) -> str:
    if not started_at:
        return "Unknown"
    diff = datetime.now(timezone.utc).replace(tzinfo=None) - started_at
    seconds = max(0, int(diff.total_seconds()))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h ago"
    if hours:
        return f"{hours}h {minutes}m ago"
    return f"{minutes}m ago"

def strip_mentions_from_text(message: discord.Message) -> str:
    text = message.content
    for user in message.mentions:
        text = text.replace(user.mention, "")
    return text.strip()

def get_main_vouch_banner() -> Optional[Path]:
    for candidate in MAIN_VOUCH_IMAGE_CANDIDATES:
        if candidate.exists():
            return candidate
    return None

def _load_vouch_font(size: int) -> ImageFont.ImageFont:
    for candidate in (
        "DejaVuSans-Bold.ttf",
        "DejaVuSans.ttf",
        "arial.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size=size)
        except Exception:
            continue
    return ImageFont.load_default()

def _wrap_draw_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]

    lines = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines

class OriginalReviewView(View):
    def __init__(self, review_url: str):
        super().__init__(timeout=None)
        self.add_item(Button(label="Original Review", style=discord.ButtonStyle.link, url=review_url))

def build_main_vouch_image(user: discord.abc.User, game_name: str, avatar_bytes: Optional[bytes] = None) -> Optional[io.BytesIO]:
    banner_path = get_main_vouch_banner()
    if not banner_path:
        return None

    try:
        with Image.open(banner_path).convert("RGBA") as base_image:
            canvas = base_image.copy()
    except Exception:
        return None

    width, height = canvas.size
    draw = ImageDraw.Draw(canvas)

    if avatar_bytes:
        try:
            avatar_size = max(135, min(width // 4, 205))
            avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
            avatar = ImageOps.fit(avatar, (avatar_size, avatar_size), method=Image.Resampling.LANCZOS)
            mask = Image.new("L", (avatar_size, avatar_size), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, avatar_size - 1, avatar_size - 1), fill=255)

            ring_size = avatar_size + 10
            ring = Image.new("RGBA", (ring_size, ring_size), (0, 0, 0, 0))
            ring_draw = ImageDraw.Draw(ring)
            ring_draw.ellipse((0, 0, ring_size - 1, ring_size - 1), fill=(255, 255, 255, 35), outline=(255, 255, 255, 220), width=3)

            ring_x = (width - ring_size) // 2
            ring_y = int(height * 0.41) - (ring_size // 2)
            canvas.alpha_composite(ring, (ring_x, ring_y))

            avatar_x = (width - avatar_size) // 2
            avatar_y = ring_y + 5
            canvas.paste(avatar, (avatar_x, avatar_y), mask)
            draw = ImageDraw.Draw(canvas)
        except Exception:
            pass

    title_font = _load_vouch_font(max(28, width // 28))
    game_font = _load_vouch_font(max(26, width // 30))
    small_font = _load_vouch_font(max(20, width // 40))

    title_text = f"{user.display_name} Enjoy {game_name}"
    game_text = "Thanks for choosing Crack World Hub"
    footer_text = "Have Fun!!"

    max_text_width = width - 120
    title_lines = _wrap_draw_text(draw, title_text, title_font, max_text_width)
    game_lines = _wrap_draw_text(draw, game_text, game_font, max_text_width)

    line_specs = []
    for line in title_lines:
        line_specs.append((line, title_font, (255, 255, 255, 255), 14, 2, (0, 0, 0, 220)))
    for line in game_lines:
        line_specs.append((line, game_font, (185, 245, 255, 255), 10, 2, (0, 0, 0, 220)))

    text_heights = []
    for line, font, _, gap, _, _ in line_specs:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_heights.append((bbox[3] - bbox[1]) + gap)

    total_height = sum(text_heights) - (line_specs[-1][3] if line_specs else 0)
    y = int(height * 0.64) - max(0, total_height // 2)

    for line, font, color, gap, stroke_width, stroke_fill in line_specs:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_width = bbox[2] - bbox[0]
        x = (width - line_width) // 2
        draw.text((x, y), line, font=font, fill=color, stroke_width=stroke_width, stroke_fill=stroke_fill)
        y += (bbox[3] - bbox[1]) + gap

    footer_bbox = draw.textbbox((0, 0), footer_text, font=small_font)
    footer_width = footer_bbox[2] - footer_bbox[0]
    footer_x = max(60, (width - footer_width) // 2)
    draw.text((footer_x, height - 102), footer_text, font=small_font, fill=(220, 220, 220, 255), stroke_width=1, stroke_fill=(0, 0, 0, 200))

    output = io.BytesIO()
    canvas = canvas.convert("RGB")
    if canvas.width > 960:
        new_height = int(canvas.height * (960 / canvas.width))
        canvas = canvas.resize((960, new_height), Image.Resampling.LANCZOS)
    canvas.save(output, format="JPEG", quality=82, optimize=True)
    output.seek(0)
    return output

async def send_vouch_media(channel: discord.TextChannel, mention: str, review_url: str, rendered_vouch_image: Optional[io.BytesIO], banner_path: Optional[Path]):
    attempts = []
    if rendered_vouch_image:
        attempts.append(("vouch-transfer.jpg", rendered_vouch_image))
    if banner_path and banner_path.exists():
        attempts.append((banner_path.name, banner_path))

    last_exc = None
    for filename, media in attempts:
        for _ in range(3):
            try:
                file = discord.File(media, filename=filename)
                await channel.send(
                    file=file,
                    view=OriginalReviewView(review_url)
                )
                return
            except Exception as exc:
                last_exc = exc
                if hasattr(media, "seek"):
                    media.seek(0)
                await asyncio.sleep(1)
    if last_exc:
        raise last_exc

async def close_ticket_channel(channel: discord.TextChannel, *, apply_cooldown: bool, notice: str):
    meta = parse_ticket_topic(channel.topic)
    user_id = meta.get("uid") or channel.topic
    tier = meta.get("tier")
    game_id = meta.get("game")
    if apply_cooldown and user_id and str(user_id).isdigit():
        set_ticket_cooldown(str(user_id), tier=tier, game_id=game_id)
    clear_verification_state(channel.id)
    remove_ticket_state(channel.id)
    try:
        await channel.send(embed=discord.Embed(title="✅ Session Complete", description=notice, color=CLR.GREEN))
        await asyncio.sleep(3)
    except Exception as exc:
        print(f"[TICKET] Failed to send close notice in #{channel.name}: {exc}")
    try:
        await channel.delete()
    except Exception as exc:
        print(f"[TICKET] Failed to delete #{channel.name}: {exc}")

class PatchedSteamClient(SteamClient):
    def __init__(self):
        _init_sync_emitter(self)
        super().__init__()

    def count_listeners(self, event):
        return self.count(event)

    def emit(self, event, *args):
        return _sync_emit(self, event, *args)

    def remove_all_listeners(self, event=None):
        _safe_remove_all_listeners(self, event)
        return self

    def wait_event(self, event, timeout=None, raises=False):
        result_queue = queue.Queue(maxsize=1)

        def handler(*args):
            try:
                result_queue.put_nowait(args)
            except queue.Full:
                pass

        self.once(event, handler)

        try:
            return result_queue.get(timeout=timeout)
        except queue.Empty:
            self.remove_listener(event, handler)
            if raises:
                raise TimeoutError(f"Timed out waiting for event: {event}")
            return None

def create_steam_client():
    return PatchedSteamClient()

def sync_steam_login(username, password, auth_code, domain):
    try:
        client = create_steam_client()
        client.set_credential_location(str(MAUTH))
        kwargs = {"username": username, "password": password}
        if auth_code:
            if domain == "email": kwargs["auth_code"] = auth_code
            else: kwargs["two_factor_code"] = auth_code
        return client.login(**kwargs)
    except Exception as exc:
        print(f"[STEAM] Failed to initialize login client for {username}: {exc}")
        return None

def sync_steam_scan(username, password):
    try:
        client = create_steam_client()
        client.set_credential_location(str(MAUTH))
        
        res = client.login(username=username, password=password)
        if res != EResult.OK:
            return []
        
        client.wait_event('licenses', timeout=10)
        licenses = client.licenses
        if not licenses:
            time.sleep(3)
            licenses = client.licenses

        if not licenses:
            client.logout()
            return []
            
        pkg_ids = list(licenses.keys())
        info = client.get_product_info(packages=pkg_ids)
        owned_app_ids = set()
        
        if info and 'packages' in info:
            for pkg_data in info['packages'].values():
                appids_data = pkg_data.get('appids', {})
                if isinstance(appids_data, dict):
                    for v in appids_data.values(): owned_app_ids.add(str(v))
                elif isinstance(appids_data, list):
                    for v in appids_data: owned_app_ids.add(str(v))
        
        denuvo_db = CFG.get("denuvo_games", {})
        owned_games = []
        for appid, name in denuvo_db.items():
            if appid in owned_app_ids:
                owned_games.append(name)
                
        client.logout()
        return owned_games
    except Exception as exc:
        print(f"[STEAM] Failed to scan library for {username}: {exc}")
        return []

def sync_steam_verify_login(email, password):
    try:
        client = create_steam_client()
        client.set_credential_location(str(MAUTH))
        return client.login(username=email, password=password)
    except Exception as exc:
        print(f"[STEAM] Failed to verify donation account {email}: {exc}")
        return None

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

class PubBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        for tier_name in PANEL_TIERS:
            self.add_view(DirectPanelView(tier_name))
        self.add_view(TicketInitView())
        self.add_view(VerificationInitView())
        self.add_view(StaffOverrideView())
        self.add_view(DonatePlatformView())
        self.add_view(ReverifyView())
        self.add_view(AuthPromptView())
        verification_queue.update({int(k): v for k, v in load_verification_state().items() if str(k).isdigit()})
        guild_id = CFG.get("guild_id")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else: await self.tree.sync()
        self.donation_checker.start()

    @tasks.loop(minutes=2)
    async def donation_checker(self):
        guild = self.get_guild(int(CFG.get("guild_id", 0)))
        if not guild: return
        
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        
        for tier in ["1", "2", "3", "4plus"]:
            donations = load_donations(f"tier{tier}")
            active_donations = []
            for acc in donations:
                if acc["platform"] == "steam":
                    res = await asyncio.to_thread(sync_steam_verify_login, acc["email"], acc["password"])
                    if res is None:
                        active_donations.append(acc)
                        continue
                    if res == EResult.OK:
                        active_donations.append(acc)
                    else:
                        semi_flags = load_donations("semi_flag")
                        if not any(f["email"].lower() == acc["email"].lower() for f in semi_flags):
                            semi_flags.append({
                                "user_id": acc["user_id"], 
                                "email": acc["email"], 
                                "platform": acc["platform"],
                                "flagged_at": now.isoformat(), 
                                "warn_12h": False
                            })
                            save_donations("semi_flag", semi_flags)
                            
                            try:
                                member = await guild.fetch_member(int(acc["user_id"]))
                                role_id = CFG.get(f"donator_role_{tier}")
                                if role_id:
                                    role = guild.get_role(int(role_id))
                                    if role: await member.remove_roles(role)
                                
                                embed = discord.Embed(
                                    title="⚠️ ACTION REQUIRED: Account Disconnected", 
                                    description=f"Your donated account `{acc['email']}` was logged out or the credentials changed. Your donator perks have been temporarily suspended.\n\n**You have 24 hours to reverify this account.** Failure to update the credentials will result in a permanent fraud donation ban.", 
                                    color=CLR.RED
                                )
                                await member.send(embed=embed, view=ReverifyView())
                            except: pass

                        errors = load_donations("error")
                        if not any(e["email"].lower() == acc["email"].lower() for e in errors):
                            errors.append(acc)
                            save_donations("error", errors)
                        
                        accounts = load_accounts()
                        accounts = [a for a in accounts if a["username"].lower() != acc["email"].lower()]
                        save_accounts(accounts)
                        
                else:
                    active_donations.append(acc)
            save_donations(f"tier{tier}", active_donations)

        semi_flags = load_donations("semi_flag")
        new_semi_flags = []
        flags = load_donations("flag")
        
        for sf in semi_flags:
            flagged_time = datetime.fromisoformat(sf["flagged_at"])
            elapsed = (now - flagged_time).total_seconds()
            
            if elapsed > 86400:
                if not any(f["user_id"] == sf["user_id"] for f in flags):
                    flags.append({"user_id": sf["user_id"], "email": sf["email"]})
                
                try:
                    member = await guild.fetch_member(int(sf["user_id"]))
                    emb = discord.Embed(
                        title="⛔ SYSTEM BAN ENFORCED", 
                        description=f"You have been banned with the allegation of fraud donation regarding account `{sf['email']}`.\n\nYou failed to reverify the credentials within the 24-hour grace period.\n\nIf you believe this is false or a mistake, please contact staff immediately.", 
                        color=CLR.RED
                    )
                    await member.send(embed=emb)
                except: pass
            else:
                if elapsed > 43200 and not sf.get("warn_12h"):
                    try:
                        member = await guild.fetch_member(int(sf["user_id"]))
                        emb = discord.Embed(
                            title="⏳ 12-HOUR WARNING", 
                            description=f"You have less than 12 hours remaining to reverify your account `{sf['email']}`.\n\nFailure to update the credentials will result in a permanent fraud ban.", 
                            color=CLR.AMBER
                        )
                        await member.send(embed=emb, view=ReverifyView())
                    except: pass
                    sf["warn_12h"] = True
                new_semi_flags.append(sf)
                
        save_donations("semi_flag", new_semi_flags)
        save_donations("flag", flags)

    @donation_checker.before_loop
    async def before_donation_checker(self):
        await self.wait_until_ready()

bot = PubBot()

class AuthCodeModal(Modal, title="Provide Authentication Code"):
    code = TextInput(label="2FA / Steam Guard Code", max_length=10, required=True)

    def __init__(self, acc_data):
        super().__init__()
        self.acc_data = acc_data

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        code_val = self.code.value.strip()
        
        auths = load_donations("auth")
        auths = [a for a in auths if not (a["email"].lower() == self.acc_data["email"].lower() and str(a["user_id"]) == str(interaction.user.id))]
        save_donations("auth", auths)
        
        await interaction.followup.send(embed=discord.Embed(title="⚙️ Processing", description="Code received. Attempting login...", color=CLR.AMBER), ephemeral=True)
        await process_steam_donation(interaction, self.acc_data["email"], self.acc_data["password"], self.acc_data["user_id"], code_val, self.acc_data["domain"])

class AuthPromptView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Submit Code", style=discord.ButtonStyle.success, custom_id="submit_auth_code")
    async def submit_auth(self, interaction: Interaction, button: Button):
        auths = load_donations("auth")
        user_auths = [a for a in auths if str(a["user_id"]) == str(interaction.user.id)]
        
        if not user_auths:
            return await interaction.response.send_message("❌ No pending authentication requests found. You may need to restart your donation.", ephemeral=True)
            
        await interaction.response.send_modal(AuthCodeModal(user_auths[-1]))

class ReverifyModal(Modal, title="Update Account Credentials"):
    email = TextInput(label="Username / Email", max_length=100, required=True)
    password = TextInput(label="New Password", max_length=100, required=True)

    def __init__(self):
        super().__init__()

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        email_val = self.email.value.strip()
        pass_val = self.password.value.strip()
        
        errors = load_donations("error")
        acc_data = next((a for a in errors if a["email"].lower() == email_val.lower() and str(a["user_id"]) == str(interaction.user.id)), None)
        
        if not acc_data:
            return await interaction.followup.send("❌ Account not found in your error list. Please check the spelling.", ephemeral=True)
            
        errors = [a for a in errors if not (a["email"].lower() == email_val.lower() and str(a["user_id"]) == str(interaction.user.id))]
        save_donations("error", errors)
        
        await interaction.followup.send(embed=discord.Embed(title="⚙️ Processing", description="Re-verifying credentials...", color=CLR.AMBER), ephemeral=True)
        if acc_data.get("platform", "steam") == "steam":
            await process_steam_donation(interaction, email_val, pass_val, acc_data["user_id"])
        else:
            await process_ubi_donation(interaction, email_val, pass_val, acc_data["user_id"])

class ReverifyView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Reverify Account", style=discord.ButtonStyle.primary, custom_id="reverify_acc_btn")
    async def rev_btn(self, interaction: Interaction, button: Button):
        await interaction.response.send_modal(ReverifyModal())

class DonateModal(Modal, title="Account Donation"):
    email = TextInput(label="Username / Email", max_length=100, required=True)
    password = TextInput(label="Password", max_length=100, required=True)

    def __init__(self, platform):
        super().__init__()
        self.platform = platform

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        email_val = self.email.value.strip()
        pass_val = self.password.value.strip()
        
        if is_duplicate_account(email_val, interaction.user.id):
            return await interaction.followup.send(embed=discord.Embed(title="❌ Submission Rejected", description="This account is already registered to another user in the fleet.", color=CLR.RED), ephemeral=True)
        
        pending = load_donations("pending")
        pending.append({"user_id": str(interaction.user.id), "platform": self.platform, "email": email_val, "password": pass_val, "timestamp": now_iso()})
        save_donations("pending", pending)
        
        await interaction.followup.send(embed=discord.Embed(title="⚙️ Processing", description="Attempting initial connection...", color=CLR.AMBER), ephemeral=True)
        
        if self.platform == "steam":
            await process_steam_donation(interaction, email_val, pass_val, str(interaction.user.id))
        else:
            await process_ubi_donation(interaction, email_val, pass_val, str(interaction.user.id))

async def process_steam_donation(interaction, username, password, uid, auth_code=None, domain=None):
    res = await asyncio.to_thread(sync_steam_login, username, password, auth_code, domain)
    if res is None:
        return await interaction.user.send(
            embed=discord.Embed(
                title="❌ Steam Connection Failed",
                description="Steam client initialization failed on the server. Please try again after restarting the bot.",
                color=CLR.RED
            )
        )

    if res == EResult.OK:
        await interaction.user.send(embed=discord.Embed(title="🔍 Verifying Games", description="Login successful. Scanning library for Denuvo titles... this will only take a few seconds.", color=CLR.BLUE))
        
        owned_games = await asyncio.to_thread(sync_steam_scan, username, password)
        count = len(owned_games)
        
        if count == 0:
            return await interaction.user.send(embed=discord.Embed(title="❌ Donation Rejected", description="No supported Denuvo games were found on this account. The account has not been added to the fleet.", color=CLR.RED))

        tier = "1"
        if count >= 3: tier = "3"
        elif count == 2: tier = "2"

        acc_data = {
            "user_id": uid,
            "discord_name": str(interaction.user),
            "platform": "steam",
            "email": username,
            "password": password,
            "game_count": count,
            "games": owned_games,
            "timestamp": now_iso()
        }
        
        tier_db = load_donations(f"tier{tier}")
        tier_db = [a for a in tier_db if a["email"].lower() != username.lower()]
        tier_db.append(acc_data)
        save_donations(f"tier{tier}", tier_db)
        
        sf_data = load_donations("semi_flag")
        sf_data = [x for x in sf_data if x["email"].lower() != username.lower()]
        save_donations("semi_flag", sf_data)
        
        err_data = load_donations("error")
        err_data = [x for x in err_data if x["email"].lower() != username.lower()]
        save_donations("error", err_data)
        
        accounts = load_accounts()
        if not any(a["username"].lower() == username.lower() for a in accounts):
            accounts.append({"username": username, "password": password})
            save_accounts(accounts)

        guild = bot.get_guild(int(CFG.get("guild_id", 0)))
        if guild:
            member = await guild.fetch_member(int(uid))
            role_id = CFG.get(f"donator_role_{tier}")
            if role_id and member:
                role = guild.get_role(int(role_id))
                if role: await member.add_roles(role)

        games_list = "\n".join([f"• {g}" for g in owned_games])
        final_emb = discord.Embed(title="✅ Donation Accepted", color=CLR.GREEN)
        final_emb.add_field(name="Account", value=f"`{username}`", inline=False)
        final_emb.add_field(name=f"Denuvo Games Found ({count})", value=games_list, inline=False)
        final_emb.set_footer(text="Donator role assigned. Thank you for supporting the system.")
        await interaction.user.send(embed=final_emb)

    elif res in [EResult.AccountLoginDeniedNeedTwoFactor, EResult.AccountLogonDenied]:
        dom = "2FA" if res == EResult.AccountLoginDeniedNeedTwoFactor else "email"
        auths = load_donations("auth")
        auths.append({"user_id": uid, "platform": "steam", "email": username, "password": password, "domain": dom})
        save_donations("auth", auths)
        
        emb = discord.Embed(title="🔐 Steam Guard Required", description=f"An authentication code is required to login ({dom}). Click below to submit it.", color=CLR.AMBER)
        await interaction.user.send(embed=emb, view=AuthPromptView())
    else:
        await interaction.user.send(embed=discord.Embed(title="❌ Login Failed", description=f"Steam rejected the login. Reason: {res}", color=CLR.RED))

async def process_ubi_donation(interaction, email, password, uid):
    acc_data = {
        "user_id": uid,
        "discord_name": str(interaction.user),
        "platform": "ubi",
        "email": email,
        "password": password,
        "game_count": 1,
        "games": ["Ubisoft Title"],
        "timestamp": now_iso()
    }
    tier_db = load_donations("tier1")
    tier_db = [a for a in tier_db if a["email"].lower() != email.lower()]
    tier_db.append(acc_data)
    save_donations("tier1", tier_db)
    
    sf_data = load_donations("semi_flag")
    sf_data = [x for x in sf_data if x["email"].lower() != email.lower()]
    save_donations("semi_flag", sf_data)
        
    err_data = load_donations("error")
    err_data = [x for x in err_data if x["email"].lower() != email.lower()]
    save_donations("error", err_data)

    guild = bot.get_guild(int(CFG.get("guild_id", 0)))
    if guild:
        member = await guild.fetch_member(int(uid))
        role_id = CFG.get("donator_role_1")
        if role_id and member:
            role = guild.get_role(int(role_id))
            if role: await member.add_roles(role)
            
    final_emb = discord.Embed(title="✅ Donation Accepted", description="Ubisoft account received and vaulted.", color=CLR.GREEN)
    final_emb.set_footer(text="Donator role assigned.")
    await interaction.user.send(embed=final_emb)

class DonateInfoView(View):
    def __init__(self, platform):
        super().__init__(timeout=None)
        self.platform = platform

    @discord.ui.button(label="Proceed to Donate", style=discord.ButtonStyle.success, custom_id="proceed_donate")
    async def proc_btn(self, interaction: Interaction, button: Button):
        await interaction.response.send_modal(DonateModal(self.platform))

class DonatePlatformView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Donate Steam Account", style=discord.ButtonStyle.primary, custom_id="donate_steam")
    async def don_steam(self, interaction: Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        
        flags = load_donations("flag")
        if any(f["user_id"] == str(interaction.user.id) for f in flags):
            return await interaction.followup.send("❌ You are permanently flagged by the system and cannot donate accounts. Contact staff.", ephemeral=True)
            
        emb = discord.Embed(
            title="🛡️ Secure Donation Protocol",
            description="By donating your Steam account, you help fuel the automated delivery system.\n\n**Security Guarantee:**\n• Your credentials are encrypted and stored offline.\n• The bot handles all logins dynamically.\n• You will automatically receive a Donator Role based on the number of premium Denuvo games on the account.\n\n⚠️ **COMMITMENT WARNING:**\nOnce donated, you cannot request account removal for **30 days**.\n\nClick below to securely submit your credentials.",
            color=CLR.BLUE
        )
        try:
            await interaction.user.send(embed=emb, view=DonateInfoView("steam"))
            await interaction.followup.send("✅ Check your DMs for instructions.", ephemeral=True)
        except:
            await interaction.followup.send("❌ Cannot send DM. Please enable DMs from server members.", ephemeral=True)

    @discord.ui.button(label="Donate Ubisoft Account", style=discord.ButtonStyle.primary, custom_id="donate_ubi")
    async def don_ubi(self, interaction: Interaction, button: Button):
        await interaction.response.defer(ephemeral=True)
        
        flags = load_donations("flag")
        if any(f["user_id"] == str(interaction.user.id) for f in flags):
            return await interaction.followup.send("❌ You are permanently flagged by the system and cannot donate accounts. Contact staff.", ephemeral=True)

        emb = discord.Embed(
            title="🛡️ Secure Donation Protocol",
            description="By donating your Ubisoft account, you help fuel the automated delivery system.\n\n**Security Guarantee:**\n• Your credentials are encrypted and stored offline.\n• The bot handles all logins dynamically.\n• You will automatically receive a Donator Role.\n\n⚠️ **COMMITMENT WARNING:**\nOnce donated, you cannot request account removal for **30 days**.\n\nClick below to securely submit your credentials.",
            color=CLR.BLUE
        )
        try:
            await interaction.user.send(embed=emb, view=DonateInfoView("ubi"))
            await interaction.followup.send("✅ Check your DMs for instructions.", ephemeral=True)
        except:
            await interaction.followup.send("❌ Cannot send DM. Please enable DMs from server members.", ephemeral=True)

class StaffOverrideView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🛡️ Staff Override (Force Approve)", style=discord.ButtonStyle.danger, custom_id="force_approve")
    async def force_approve(self, interaction: Interaction, button: Button):
        if not is_bartender(interaction):
            return await interaction.response.send_message("❌ Only staff can override OCR failures.", ephemeral=True)

        queue_data = verification_queue.get(interaction.channel.id) or load_verification_state().get(str(interaction.channel.id))
        if not queue_data:
            return await interaction.response.send_message("❌ No saved verification step was found for this ticket.", ephemeral=True)

        game = get_game_by_id(queue_data.get("game_id"))
        if not game:
            return await interaction.response.send_message("❌ Game data is missing for this ticket.", ephemeral=True)

        user = interaction.guild.get_member(int(queue_data["user_id"])) if interaction.guild and str(queue_data.get("user_id", "")).isdigit() else None
        if not user:
            return await interaction.response.send_message("❌ User could not be resolved for this ticket.", ephemeral=True)

        await interaction.response.send_message("🛡️ **Staff Override Applied.** Bypassing security checks...", ephemeral=False)
        for child in self.children: child.disabled = True
        await interaction.message.edit(view=self)

        clear_verification_state(interaction.channel.id)
        update_ticket_state(interaction.channel.id, stage="verified")

        if queue_data.get("type") == "steam":
            await trigger_worker(interaction, game, user)
        elif queue_data.get("type") == "ubi":
            await prompt_ubi_token(interaction.channel, user, game)

class VerificationInitView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📸 Upload Screenshot Now", style=discord.ButtonStyle.primary, custom_id="start_ocr")
    async def start_ocr(self, interaction: Interaction, button: Button):
        await interaction.response.defer()
        ticket_state = get_ticket_state(interaction.channel.id)
        if not ticket_state:
            return await interaction.followup.send("❌ This ticket could not be restored. Ask staff to open a new one.", ephemeral=True)
        if interaction.user.id != int(ticket_state["user_id"]):
            return await interaction.followup.send("❌ This isn't your ticket!", ephemeral=True)

        game = get_game_by_id(ticket_state.get("game_id"))
        if not game:
            return await interaction.followup.send("❌ Game data is missing for this ticket.", ephemeral=True)

        set_verification_state(
            interaction.channel.id,
            {"type": ticket_state["req_type"], "game_id": ticket_state["game_id"], "user_id": ticket_state["user_id"]}
        )
        update_ticket_state(interaction.channel.id, stage="awaiting_screenshot")

        for child in self.children: child.disabled = True
        await interaction.edit_original_response(view=self)

        req_embed = discord.Embed(
            title="🔍 System Verification Required", color=CLR.BLUE,
            description=(
                "Before the request can continue, the bot needs one proof screenshot.\n\n"
                "**📸 Your upload must show both items in one image:**\n"
                "📂 **1.** The game's `Properties` window proving the install is larger than **10GB**.\n"
                "🛡️ **2.** `Windows Update Blocker` showing **Disable Updates** is active.\n\n"
                "Use the example image below if you need a reference."
            )
        )
        
        img_path = ASSETS / "wub_example.png"
        if img_path.exists():
            file = discord.File(img_path, filename="wub_example.png")
            req_embed.set_image(url="attachment://wub_example.png")
            await interaction.channel.send(content=interaction.user.mention, embed=req_embed, file=file)
        else:
            await interaction.channel.send(content=interaction.user.mention, embed=req_embed)

class TicketInitView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Yes, My Game Is Ready", style=discord.ButtonStyle.success, custom_id="init_yes")
    async def init_yes(self, interaction: Interaction, button: Button):
        await interaction.response.defer()
        ticket_state = get_ticket_state(interaction.channel.id)
        if not ticket_state:
            return await interaction.followup.send("❌ This ticket could not be restored. Ask staff to open a new one.", ephemeral=True)
        if interaction.user.id != int(ticket_state["user_id"]):
            return await interaction.followup.send("❌ This isn't your ticket!", ephemeral=True)
        for child in self.children: child.disabled = True
        await interaction.edit_original_response(view=self)
        update_ticket_state(interaction.channel.id, stage="awaiting_verification")
        await interaction.channel.send(view=VerificationInitView())

class DirectPanelView(View):
    def __init__(self, tier: str):
        super().__init__(timeout=None)
        self.tier = tier
        panel_meta = get_panel_meta(tier)
        games = load_games()
        eligible_games = [g for g in games if game_allowed_in_tier(g, tier)]

        steam_games = [g for g in eligible_games if g.get('platform', 'steam') == 'steam']
        ubi_games = [g for g in eligible_games if g.get('platform') == 'ubi']

        steam_opts = [
            discord.SelectOption(
                label=f"{g.get('emoji','🎮')} {g['name']}"[:100],
                description=f"Steam • AppID {g['app_id']}"[:100],
                value=g["id"]
            )
            for g in steam_games[:25]
        ]
        if not steam_opts:
            steam_opts = [discord.SelectOption(label="No Steam titles in this panel", value="none")]

        steam_sel = Select(
            placeholder="■■■■ ★ Steam ★ ■■■■",
            options=steam_opts,
            custom_id=f"direct_steam_select_{tier}"
        )
        steam_sel.callback = self._on_select
        self.add_item(steam_sel)

        ubi_opts = [
            discord.SelectOption(
                label=f"{g.get('emoji','⚡')} {g['name']}"[:100],
                description=f"Ubisoft/Denuvo • AppID {g['app_id']}"[:100],
                value=g["id"]
            )
            for g in ubi_games[:25]
        ]
        if not ubi_opts:
            ubi_opts = [discord.SelectOption(label="No Ubisoft titles in this panel", value="none")]

        ubi_sel = Select(
            placeholder="■■■■ 🔥 Ubisoft 🔥 ■■■■",
            options=ubi_opts,
            custom_id=f"direct_ubi_select_{tier}"
        )
        ubi_sel.callback = self._on_select
        self.add_item(ubi_sel)

    async def _on_select(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            await interaction.message.edit(view=DirectPanelView(self.tier))
        except Exception:
            pass
        game_id = interaction.data["values"][0]
        if game_id == "none":
            return await interaction.followup.send("❌ No games are currently available in this category for this panel.", ephemeral=True)

        if not isinstance(interaction.user, discord.Member):
            return await interaction.followup.send("❌ This panel can only be used inside the server.", ephemeral=True)

        member_tier = get_member_access_tier(interaction.user)
        if not member_tier:
            return await interaction.followup.send(
                embed=discord.Embed(
                    title="🚫 Access Locked",
                    description="You do not have a valid access role for this panel.",
                    color=CLR.RED
                ),
                ephemeral=True
            )
        if not can_access_panel(interaction.user, self.tier):
            panel_name = get_panel_meta(self.tier)["name"]
            return await interaction.followup.send(
                embed=discord.Embed(
                    title="🚫 Tier Locked",
                    description=f"You need the **{panel_name}** access role or higher to use this panel.",
                    color=CLR.RED
                ),
                ephemeral=True
            )
            
        game = next((g for g in load_games() if g["id"] == game_id), None)
        if not game: return await interaction.followup.send("❌ Game not found in vault.", ephemeral=True)
        if not game_allowed_in_tier(game, self.tier):
            panel_name = get_panel_meta(self.tier)["name"]
            return await interaction.followup.send(
                embed=discord.Embed(
                    title="🚫 Panel Lock",
                    description=f"**{game['name']}** is not enabled in the **{panel_name}** panel.",
                    color=CLR.RED,
                ),
                ephemeral=True
            )

        ban = get_ban(str(interaction.user.id))
        if ban: return await interaction.followup.send(embed=discord.Embed(title="🔨 Access Denied", description=f"Reason: {ban.get('reason','N/A')}", color=CLR.RED), ephemeral=True)

        guild = interaction.guild
        existing_same_game = find_open_ticket(guild, interaction.user.id, game["id"])
        if existing_same_game:
            return await interaction.followup.send(
                embed=discord.Embed(
                    title="🎮 Game Already Requested",
                    description=f"You already opened **{game['name']}** in {existing_same_game.mention}. Finish that ticket or wait for it to close.",
                    color=CLR.RED
                ),
                ephemeral=True
            )

        gold_same_game_rem = None
        if member_tier == "gold":
            gold_same_game_rem = get_same_game_cooldown(str(interaction.user.id), game["id"])
            if gold_same_game_rem:
                return await interaction.followup.send(
                    embed=discord.Embed(
                        title="🎮 Same Game Lock",
                        description=f"You already made a ticket for **{game['name']}**. Try a different game.\n\nThis lock resets in **{gold_same_game_rem}**.",
                        color=CLR.RED
                    ),
                    ephemeral=True
                )

        cd_rem = get_ticket_cooldown(str(interaction.user.id), member_tier)
        if cd_rem:
            return await interaction.followup.send(
                embed=discord.Embed(
                    title="⏳ Cooldown Active",
                    description=f"Your **{member_tier.title()}** cooldown is still active.\n\nTime remaining: **{cd_rem}**",
                    color=CLR.RED
                ),
                ephemeral=True
            )

        category = resolve_ticket_category(guild, self.tier)
        if not category:
            category = await guild.create_category(f"{self.tier.title()} Tickets")

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
        }
        staff_role = guild.get_role(STAFF_ROLE_ID)
        if staff_role: overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True)

        safe_name = re.sub(r"[^a-zA-Z0-9-]", "", interaction.user.name.lower())[:20] or "user"
        ticket_channel = await guild.create_text_channel(
            name=f"ticket-{safe_name}",
            category=category,
            overwrites=overwrites,
            topic=build_ticket_topic(str(interaction.user.id), game["id"], self.tier)
        )

        is_ubisoft = game.get('platform') == 'ubi'
        req_type = "ubi" if is_ubisoft else "steam"
        set_ticket_state(ticket_channel.id, {
            "user_id": str(interaction.user.id),
            "game_id": game["id"],
            "req_type": req_type,
            "tier": self.tier,
            "stage": "awaiting_ready",
            "created_at": now_iso()
        })
        panel_meta = get_panel_meta(self.tier)
        tier_badges = format_tier_badges(normalize_game_tiers(game.get("tiers")))

        init_embed = discord.Embed(
            title=f"{panel_meta['emoji']} {panel_meta['name'].upper()} ACTIVATION SESSION",
            color=panel_meta["color"] if not is_ubisoft else CLR.BLUE,
            description=(
                f"### 🎯 Request Locked In\n"
                f"{interaction.user.mention}, your private activation ticket is ready.\n\n"
                f"**{game.get('emoji', '🎮')} Game:** `{game['name']}`\n"
                f"**🧩 Engine:** {'Ubisoft / Denuvo Protocol' if is_ubisoft else 'Steam Fleet Architecture'}\n"
                f"**🆔 Store AppID:** `{game['app_id']}`\n"
                f"**🏷️ Panel Access:** {tier_badges}\n\n"
                f"**👑 Your Access Tier:** `{member_tier.title()}`\n"
                f"### ⚠️ Required Before You Continue\n"
                f"• The full game must already be installed.\n"
                f"• Keep Windows Update blocked with [WUB](https://www.sordum.org/downloads/?st-windows-update-blocker).\n"
                f"• Stay in this ticket until the flow is fully completed.\n\n"
                f"### ✅ Next Step\n"
                f"Press **Yes, My Game Is Ready** once everything above is done."
            )
        )
        init_embed.set_image(url=steam_img(str(game['app_id'])))
        init_embed.set_footer(text=f"Crack World • {panel_meta['name']} Queue • Awaiting user confirmation")
            
        await ticket_channel.send(content=f"{interaction.user.mention}", embed=init_embed, view=TicketInitView())
        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Ticket Opened",
                description=f"Your secure request channel is ready: {ticket_channel.mention}",
                color=panel_meta["color"],
            ),
            ephemeral=True
        )

class AddSteamGameModal(Modal, title="Vault: Add Steam Game"):
    g_name = TextInput(label="Game Name", max_length=100, required=True)
    g_appid = TextInput(label="Steam AppID", max_length=20, required=True)
    g_emoji = TextInput(label="Emoji (Optional)", max_length=10, required=False, default="🎮")
    g_tiers = TextInput(label="Panel Tiers", max_length=50, required=False, default="free, bronze, silver, gold")
    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        games = load_games()
        gid = self.g_appid.value.strip()
        if any(str(g["app_id"]) == gid for g in games): return await interaction.followup.send("❌ Game with this AppID already exists.", ephemeral=True)
        tiers = normalize_game_tiers(self.g_tiers.value.strip())
        games.append({"id": f"steam_{gid}", "name": self.g_name.value.strip(), "app_id": gid, "platform": "steam", "emoji": self.g_emoji.value.strip() or "🎮", "tiers": tiers})
        save_games(games)
        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Vault Updated",
                description=f"Added **{self.g_name.value}** to the Steam library.\n\n**Panels:** {format_tier_badges(tiers)}",
                color=CLR.GREEN
            ),
            ephemeral=True
        )

class AddUbiGameModal(Modal, title="Vault: Add Ubisoft Game"):
    g_name = TextInput(label="Game Name", max_length=100, required=True)
    g_appid = TextInput(label="Store AppID (For Artwork Image)", max_length=20, required=True)
    g_emoji = TextInput(label="Emoji (Optional)", max_length=10, required=False, default="🎮")
    g_tiers = TextInput(label="Panel Tiers", max_length=50, required=False, default="free, bronze, silver, gold")
    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        games = load_games()
        gid = self.g_appid.value.strip()
        if any(str(g["app_id"]) == gid for g in games): return await interaction.followup.send("❌ Game with this AppID already exists.", ephemeral=True)
        tiers = normalize_game_tiers(self.g_tiers.value.strip())
        games.append({"id": f"ubi_{gid}", "name": self.g_name.value.strip(), "app_id": gid, "platform": "ubi", "emoji": self.g_emoji.value.strip() or "🎮", "tiers": tiers})
        save_games(games)
        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Vault Updated",
                description=f"Added **{self.g_name.value}** to the Ubisoft/Denuvo library.\n\n**Panels:** {format_tier_badges(tiers)}",
                color=CLR.GREEN
            ),
            ephemeral=True
        )

class RemoveSteamGameModal(Modal, title="Vault: Remove Steam Game"):
    g_appid = TextInput(label="Steam AppID", max_length=20, required=True)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        appid = self.g_appid.value.strip()
        games = load_games()
        removed_game = next((g for g in games if str(g["app_id"]) == appid and g.get("platform", "steam") == "steam"), None)
        if not removed_game:
            return await interaction.followup.send("❌ Steam game not found in the vault.", ephemeral=True)
        games = [g for g in games if not (str(g["app_id"]) == appid and g.get("platform", "steam") == "steam")]
        save_games(games)
        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Vault Updated",
                description=f"Removed **{removed_game['name']}** from the Steam library.",
                color=CLR.GREEN
            ),
            ephemeral=True
        )

class RemoveUbiGameModal(Modal, title="Vault: Remove Ubisoft Game"):
    g_appid = TextInput(label="Ubisoft/Denuvo AppID", max_length=20, required=True)

    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        appid = self.g_appid.value.strip()
        games = load_games()
        removed_game = next((g for g in games if str(g["app_id"]) == appid and g.get("platform") == "ubi"), None)
        if not removed_game:
            return await interaction.followup.send("❌ Ubisoft/Denuvo game not found in the vault.", ephemeral=True)
        games = [g for g in games if not (str(g["app_id"]) == appid and g.get("platform") == "ubi")]
        save_games(games)
        await interaction.followup.send(
            embed=discord.Embed(
                title="✅ Vault Updated",
                description=f"Removed **{removed_game['name']}** from the Ubisoft/Denuvo library.",
                color=CLR.GREEN
            ),
            ephemeral=True
        )

class AddSteamAccModal(Modal, title="Fleet: Add Steam Account"):
    a_user = TextInput(label="Steam Username", max_length=100, required=True)
    a_pass = TextInput(label="Steam Password", max_length=100, required=True)
    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        accounts = load_accounts()
        if any(a["username"].lower() == self.a_user.value.lower() for a in accounts): return await interaction.followup.send("❌ Account already exists in Fleet.", ephemeral=True)
        accounts.append({"username": self.a_user.value, "password": self.a_pass.value})
        save_accounts(accounts)
        await interaction.followup.send(embed=discord.Embed(title="✅ Fleet Updated", description=f"Steam Account `{self.a_user.value}` added to main pool.", color=CLR.GREEN), ephemeral=True)

class AddUbiAccModal(Modal, title="Fleet: Add Ubisoft Account"):
    u_email = TextInput(label="Ubisoft Email", max_length=100, required=True)
    u_pass = TextInput(label="Password", max_length=100, required=True)
    u_appid = TextInput(label="Denuvo AppID (e.g. 7013)", max_length=20, required=True)
    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        data = load_ubi_accounts()
        appid = self.u_appid.value.strip()
        if appid not in data: data[appid] = []
        if any(a["email"].lower() == self.u_email.value.lower() for a in data[appid]): return await interaction.followup.send("❌ Account already exists for this AppID.", ephemeral=True)
        data[appid].append({"email": self.u_email.value.strip(), "password": self.u_pass.value.strip()})
        save_ubi_accounts(data)
        await interaction.followup.send(embed=discord.Embed(title="✅ Fleet Updated", description=f"Mapped `{self.u_email.value}` to Denuvo AppID `{appid}`.", color=CLR.GREEN), ephemeral=True)

async def prompt_ubi_token(channel, user, game):
    embed = discord.Embed(
        title=f"✅ System Verified • {game['name']}", color=CLR.GREEN,
        description=(
            "Your machine passed verification and the request lane is open.\n\n"
            "**🧭 Denuvo Activation Steps**\n"
            "**1.** Launch the game with the Crack World bypass files.\n"
            "**2.** When the activation box appears, copy the Denuvo request.\n"
            "**3.** Upload the exact **`token_req_<numbers>.txt`** file in this ticket.\n\n"
            "⏳ Our Ubisoft fleet is standing by for the file."
        )
    )
    embed.set_image(url=steam_img(str(game['app_id'])))
    await channel.send(content=user.mention, embed=embed)

async def trigger_worker(interaction, game: dict, original_user: discord.Member):
    reqs = load_requests()
    new_request = {
        "request_id": f"{original_user.id}-{int(datetime.now(timezone.utc).timestamp())}",
        "user_id": str(original_user.id),
        "user_tag": str(original_user),
        "game_id": game["id"],
        "game_name": game["name"],
        "app_id": game["app_id"],
        "status": "pending",
        "timestamp": now_iso()
    }
    reqs.append(new_request)
    save_requests(reqs)
    
    loading_embed = discord.Embed(
        title="⚙️ Compiling Bypass",
        color=CLR.AMBER,
        description="Dispatching your request to the Steam worker fleet. Please keep this ticket open while your files are being prepared."
    )
    status_msg = await interaction.channel.send(embed=loading_embed)

    safe_user_tag = "".join([c for c in str(original_user) if c.isalpha() or c.isdigit()]).rstrip()
    expected_file = COMPLETED_DIR / f"configs_{safe_user_tag}_{game['app_id']}.zip"

    max_wait_seconds = 90
    file_found = False
    for _ in range(max_wait_seconds):
        if expected_file.exists():
            file_found = True
            break
        await asyncio.sleep(1)

    if file_found:
        file_size_mb = expected_file.stat().st_size / (1024 * 1024)
        final_embed = discord.Embed(
            title="✅ Bypass Ready", color=CLR.GREEN,
            description=(
                f"Your activation package for **{game['name']}** is ready.\n\n"
                "**📥 Install Steps**\n"
                "**1.** Extract the ZIP file completely.\n"
                "**2.** Paste the contents directly into your main game directory.\n"
                "**3.** Start the game.\n\n"
                "⏳ This package expires in **30 minutes**.\n"
                "🛡️ **Do not close this ticket until staff review is complete.**"
            )
        ).set_image(url=steam_img(str(game["app_id"])))
        
        try:
            if file_size_mb > 24.5: raise ValueError("File exceeds limits")
            file_attachment = discord.File(expected_file, filename=f"{game['id']}_bypass.zip")
            await status_msg.edit(embed=final_embed, attachments=[file_attachment])
        except:
            uploading_embed = discord.Embed(title="☁️ CLOUD ROUTING", color=CLR.AMBER, description="File too large for Discord. Routing through Gofile...")
            await status_msg.edit(embed=uploading_embed, attachments=[])
            link = await upload_to_gofile(expected_file)
            if link:
                final_embed.description = f"☁️ **[DOWNLOAD SECURE ARCHIVE]({link})**\n\n" + final_embed.description
                await status_msg.edit(embed=final_embed)
            else: await status_msg.edit(embed=discord.Embed(title="❌ ROUTING FAILED", color=CLR.RED, description="Cloud backup failed."))
        finally:
            try: expected_file.unlink()
            except: pass 
    else: 
        await status_msg.edit(embed=discord.Embed(title="❌ FLEET TIMEOUT", color=CLR.RED, description="Steam worker timed out. No available accounts currently hold this license."))

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot: return

    if VOUCH_CHANNEL_ID and message.channel.id == VOUCH_CHANNEL_ID:
        bot_member = message.guild.me if message.guild else None
        bot_was_tagged = bool(bot_member and any(member.id == bot_member.id for member in message.mentions))

        if not bot_was_tagged:
            try:
                await message.delete()
            except Exception:
                pass
            warning = await message.channel.send(
                f"{message.author.mention} you must tag the bot in your vouch message or it will not be accepted."
            )
            await asyncio.sleep(6)
            try:
                await warning.delete()
            except Exception:
                pass
            return

        linked_ticket = None
        linked_guild = None
        for guild in bot.guilds:
            linked_ticket = find_open_ticket(guild, message.author.id)
            if linked_ticket:
                linked_guild = guild
                break

        ticket_meta = parse_ticket_topic(linked_ticket.topic if linked_ticket else None)
        game = next((g for g in load_games() if g["id"] == ticket_meta.get("game")), None)
        ticket_created = parse_iso(ticket_meta.get("created")) if ticket_meta.get("created") else None
        clean_message = strip_mentions_from_text(message) or "No comment provided."

        embed = discord.Embed(
            title="⭐ NEW VOUCH RECEIVED",
            description="New vouch submitted.",
            color=CLR.GREEN
        )
        embed.add_field(name="👤 User", value=message.author.mention, inline=True)
        embed.add_field(name="🤖 Bot", value=(bot_member.mention if bot_member else "Unknown"), inline=True)
        embed.add_field(name="🟢 Status", value="Completed - Vouch Received", inline=True)
        embed.add_field(name="💬 Message", value=clean_message[:1024], inline=False)
        embed.add_field(name="🎮 Game", value=game["name"] if game else "Unknown", inline=True)
        embed.add_field(name="🏪 Platform", value=(game.get("platform", "steam").upper() if game else "UNKNOWN"), inline=True)
        embed.add_field(name="⏱️ Ticket Open For", value=format_relative_timestamp(ticket_created), inline=True)
        embed.add_field(
            name="📅 Ticket Created",
            value=(ticket_created.strftime("%A, %B %d, %Y %I:%M %p") if ticket_created else "Unknown"),
            inline=True
        )
        embed.add_field(name="🎟️ Ticket", value=(linked_ticket.mention if linked_ticket else "Unknown"), inline=True)
        embed.add_field(name="🆔 Ticket ID", value=(str(linked_ticket.id) if linked_ticket else "Unknown"), inline=True)
        embed.set_thumbnail(url=message.author.display_avatar.url)
        image_attachments = [
            attachment for attachment in message.attachments
            if attachment.filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
        ]
        if image_attachments:
            embed.set_image(url=f"attachment://{image_attachments[0].filename}")
        embed.timestamp = datetime.now(timezone.utc)
        embed.set_footer(text=f"Vouch bridge • Source: #{message.channel.name}")

        main_vouch_channel = bot.get_channel(MAIN_VOUCH_CHANNEL_ID) if MAIN_VOUCH_CHANNEL_ID else None
        if isinstance(main_vouch_channel, discord.TextChannel):
            game_name = game["name"] if game else "your game"
            try:
                await main_vouch_channel.send(
                    content=f"{message.author.mention} enjoy your game"
                )
                avatar_bytes = None
                try:
                    avatar_bytes = await message.author.display_avatar.read()
                except Exception:
                    pass
                rendered_vouch_image = build_main_vouch_image(message.author, game_name, avatar_bytes=avatar_bytes)
                await send_vouch_media(
                    main_vouch_channel,
                    message.author.mention,
                    message.jump_url,
                    rendered_vouch_image,
                    get_main_vouch_banner()
                )
            except Exception as exc:
                print(f"[VOUCH] Failed to forward main vouch post: {exc}")

        confirm = discord.Embed(
            title="✅ Vouch Logged",
            description="Your vouch was sent to the main vouch channel and your ticket is being closed.",
            color=CLR.GREEN
        )
        try:
            await message.author.send(embed=confirm)
        except Exception:
            fallback_notice = await message.channel.send(content=message.author.mention, embed=confirm)
            await asyncio.sleep(6)
            try:
                await fallback_notice.delete()
            except Exception:
                pass

        if linked_ticket:
            await close_ticket_channel(
                linked_ticket,
                apply_cooldown=True,
                notice="Your vouch was received and forwarded to the main server. This ticket will now be closed."
            )
        return
    
    if message.channel.id in verification_queue and message.attachments:
        attachment = message.attachments[0]
        if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg']):
            status_msg = await message.channel.send("🤖 **Processing Image Data...** Applying OCR Upscaling algorithms.")
            
            try:
                image_bytes = await attachment.read()
                img = Image.open(io.BytesIO(image_bytes))
                
                img = img.convert('L')
                img = ImageEnhance.Contrast(img).enhance(2.0)
                img = img.resize((img.width * 2, img.height * 2), Image.Resampling.LANCZOS)
                
                text = pytesseract.image_to_string(img).lower()
                
                wub_keywords = ['disable updates', 'update blocker', 'sordum', 'service status', 'protect services', 'apply now']
                wub_pass = any(kw in text for kw in wub_keywords)
                
                size_pass = False
                sizes = re.findall(r'(\d+(?:[\.,]\d+)?)\s*(?:gb|gigabytes|tb|terabytes)', text)
                if sizes:
                    max_size = max([float(s.replace(',', '.')) for s in sizes])
                    if max_size >= 10.0: size_pass = True
                        
                bytes_match = re.findall(r'(\d{1,3}(?:[.,]\d{3}){2,})\s*bytes', text)
                if bytes_match: size_pass = True 
                
                errors = []
                if not wub_pass: errors.append("❌ Missing: **Windows Update Blocker** (Ensure 'Disable Updates' is active).")
                if not size_pass: errors.append("❌ Missing: **Folder Size > 10GB** (Ensure Properties window is clearly visible).")
                
                if errors:
                    err_msg = "\n".join(errors) + "\n\n⚠️ **Please resolve these issues and upload a new screenshot. If this is an error, request Staff Override.**"
                    view = StaffOverrideView()
                    await status_msg.edit(content=None, embed=discord.Embed(title="🛑 VERIFICATION REJECTED", description=err_msg, color=CLR.RED), view=view)
                else:
                    await status_msg.edit(content="✅ **System Verified. Security checks passed.**")
                    queue_data = verification_queue.get(message.channel.id) or load_verification_state().get(str(message.channel.id))
                    if not queue_data:
                        return await status_msg.edit(content="❌ Verification data was lost. Ask staff to reopen this ticket.")
                    game = get_game_by_id(queue_data.get("game_id"))
                    user = message.guild.get_member(int(queue_data["user_id"])) if message.guild and str(queue_data.get("user_id", "")).isdigit() else None
                    req_type = queue_data["type"]
                    if not game or not user:
                        return await status_msg.edit(content="❌ Ticket data is missing after restart. Ask staff to reopen this ticket.")
                    clear_verification_state(message.channel.id)
                    update_ticket_state(message.channel.id, stage="verified")
                    
                    if req_type == "steam":
                        class MockInteraction:
                            def __init__(self, ch): self.channel = ch
                        await trigger_worker(MockInteraction(message.channel), game, user)
                    elif req_type == "ubi":
                        await prompt_ubi_token(message.channel, user, game)

            except Exception as e:
                view = StaffOverrideView()
                await status_msg.edit(content="⚠️ **OCR Failure.** Image compression too high. Please ask Staff to manually override.", view=view)
        return

    if "ticket-" in message.channel.name and message.attachments and message.channel.id not in verification_queue:
        attachment = message.attachments[0]
        if attachment.filename.startswith("token_req"):
            if not re.search(r"token_req_\d+", attachment.filename):
                return await message.channel.send(embed=discord.Embed(description="❌ **Invalid Format.**\nPlease send the exact `token_req_<numbers>.txt` file.", color=CLR.RED))

            try:
                file_bytes = await attachment.read()
                content = file_bytes.decode('utf-8', errors='ignore').strip()
                if "|" not in content:
                    return await message.channel.send(embed=discord.Embed(title="❌ Data Corrupted", description="File does not contain valid Denuvo parameters.", color=CLR.RED))

                status_msg = await message.channel.send("⚙️ **Decrypting Denuvo Request... Pinging Ubisoft Fleet.**")
                t_val, a_val = content.split("|", 1)
                
                ureqs = load_ubi_reqs()
                ureqs.append({"user_id": str(message.author.id), "t_val": t_val.strip(), "app_id": a_val.strip()})
                save_ubi_reqs(ureqs)

                expected_file = COMPLETED_DIR / f"ubi_token_{message.author.id}.txt"
                failed_file = COMPLETED_DIR / f"ubi_token_{message.author.id}_failed.txt"
                
                success = False
                for _ in range(60): 
                    if expected_file.exists():
                        success = True
                        break
                    if failed_file.exists():
                        failed_file.unlink()
                        break
                    await asyncio.sleep(1)

                await status_msg.delete()
                
                if success:
                    final_text = (
                        "**📥 Token Delivery Complete**\n"
                        "**1.** Download **`token.txt`** and place it in your game folder.\n"
                        "**2.** Launch the game.\n"
                        "**3.** Upload a screenshot showing the game running.\n\n"
                        "🛡️ Wait for staff verification before closing this session."
                    )
                    file_attachment = discord.File(expected_file, filename="token.txt")
                    
                    games = load_games()
                    matching_game = next((g for g in games if str(g['app_id']) == a_val.strip()), None)
                    success_embed = discord.Embed(title="✅ Denuvo Token Generated", description=final_text, color=CLR.GREEN)
                    if matching_game:
                        success_embed.set_image(url=steam_img(str(matching_game['app_id'])))
                        
                    await message.channel.send(embed=success_embed, file=file_attachment)
                    expected_file.unlink()
                else:
                    await message.channel.send(embed=discord.Embed(description="❌ Ubisoft Fleet failed. No accounts hold a valid license for this request.", color=CLR.RED))

            except Exception as e:
                await message.channel.send(embed=discord.Embed(description="⚠️ System error while reading file.", color=CLR.AMBER))

GUILD = discord.Object(id=int(CFG.get("guild_id", 0))) if CFG.get("guild_id") else None

@bot.tree.command(guild=GUILD, description="Donate an account for roles")
async def donate(interaction: Interaction):
    await interaction.response.send_message("Select platform to donate:", view=DonatePlatformView(), ephemeral=True)

async def send_staff_announcement(interaction: Interaction, message: str):
    await interaction.response.defer(ephemeral=True)
    if not is_bartender(interaction):
        return await interaction.followup.send("❌ Access Denied.", ephemeral=True)

    clean_message = message.strip()
    if not clean_message:
        return await interaction.followup.send("❌ Message cannot be empty.", ephemeral=True)

    embed = discord.Embed(
        title="📢 Crack World Notice",
        description=clean_message,
        color=CLR.BLUE
    )
    embed.set_footer(text="Crack World Automated Systems")
    await interaction.channel.send(embed=embed)
    await interaction.followup.send("✅ Announcement sent.", ephemeral=True)

@bot.tree.command(guild=GUILD, description="Post a bot-style message")
@app_commands.describe(message="The message the bot should say")
async def say(interaction: Interaction, message: str):
    await send_staff_announcement(interaction, message)

@bot.tree.command(guild=GUILD, description="Post a bot-style announcement")
@app_commands.describe(message="The announcement the bot should post")
async def announce(interaction: Interaction, message: str):
    await send_staff_announcement(interaction, message)

@bot.tree.command(guild=GUILD, description="Vault: Deploy a specific tier request panel")
@app_commands.describe(tier="Which panel tier to deploy")
@app_commands.choices(tier=[
    app_commands.Choice(name="Free", value="free"),
    app_commands.Choice(name="Bronze", value="bronze"),
    app_commands.Choice(name="Silver", value="silver"),
    app_commands.Choice(name="Gold", value="gold"),
])
async def setup(interaction: Interaction, tier: app_commands.Choice[str]):
    await interaction.response.defer(ephemeral=True)
    if not is_bartender(interaction): return await interaction.followup.send("❌ Access Denied.", ephemeral=True)

    tier_val = tier.value
    panel_meta = get_panel_meta(tier_val)
    games = load_games()
    panel_games = [g for g in games if game_allowed_in_tier(g, tier_val)]
    steam_count = sum(1 for g in panel_games if g.get("platform", "steam") == "steam")
    ubi_count = sum(1 for g in panel_games if g.get("platform") == "ubi")

    embed = discord.Embed(
        title=f"{panel_meta['emoji']} {panel_meta['name'].upper()} ACTIVATION PANEL",
        color=panel_meta["color"],
        description=(
            f"**{panel_meta['tagline']}**\n\n"
            f"**👤 Eligibility**\n"
            f"{panel_meta['access_note']} Access is limited to confirmed subscribers.\n\n"
            f"**📦 Current Library**\n"
            f"• Steam titles: **{steam_count}**\n"
            f"• Ubisoft/Denuvo titles: **{ubi_count}**\n\n"
            f"**🧭 Activation Flow**\n"
            f"1. Pick your game from the menu below.\n"
            f"2. Enter your private ticket.\n"
            f"3. Pass system and screenshot verification.\n"
            f"4. Receive your files or token inside the ticket.\n\n"
            f"**⚠️ Security Notice**\n"
            f"Screenshot verification is enabled. Select only a game that is fully installed and ready."
        )
    )
    embed.set_image(url=panel_meta["banner_url"])
    embed.set_footer(text=f"Crack World • {panel_meta['name']} panel • Verified request flow")

    await interaction.channel.send(embed=embed, view=DirectPanelView(tier_val))
    await interaction.followup.send(
        embed=discord.Embed(
            title="✅ Panel Deployed",
            description=f"{panel_meta['emoji']} {panel_meta['name']} panel posted with tier-filtered game menus.",
            color=panel_meta["color"],
        ),
        ephemeral=True
    )

@bot.tree.command(guild=GUILD, description="Vault: Add a Steam game")
async def addsteamgame(interaction: Interaction):
    if not is_bartender(interaction): return await interaction.response.send_message("❌ Access Denied.", ephemeral=True)
    await interaction.response.send_modal(AddSteamGameModal())

@bot.tree.command(guild=GUILD, description="Vault: Add a Ubisoft/Denuvo game")
async def addubigame(interaction: Interaction):
    if not is_bartender(interaction): return await interaction.response.send_message("❌ Access Denied.", ephemeral=True)
    await interaction.response.send_modal(AddUbiGameModal())

@bot.tree.command(guild=GUILD, description="Vault: Remove a Steam game")
async def removesteamgame(interaction: Interaction):
    if not is_bartender(interaction): return await interaction.response.send_message("❌ Access Denied.", ephemeral=True)
    await interaction.response.send_modal(RemoveSteamGameModal())

@bot.tree.command(guild=GUILD, description="Vault: Remove a Ubisoft/Denuvo game")
async def removeubigame(interaction: Interaction):
    if not is_bartender(interaction): return await interaction.response.send_message("❌ Access Denied.", ephemeral=True)
    await interaction.response.send_modal(RemoveUbiGameModal())

@bot.tree.command(guild=GUILD, description="Vault: Remove a game from the menu")
@app_commands.describe(appid="The AppID of the game to remove")
async def removegame(interaction: Interaction, appid: str):
    await interaction.response.defer(ephemeral=True)
    if not is_bartender(interaction): return await interaction.followup.send("❌ Access Denied.", ephemeral=True)
    
    games = load_games()
    original_count = len(games)
    games = [g for g in games if str(g["app_id"]) != appid.strip()]
    
    if len(games) < original_count:
        save_games(games)
        await interaction.followup.send(embed=discord.Embed(title="✅ Vault Updated", description=f"Removed AppID `{appid}` from the menu.", color=CLR.GREEN), ephemeral=True)
    else:
        await interaction.followup.send(f"❌ AppID `{appid}` not found in the vault.", ephemeral=True)

@bot.tree.command(guild=GUILD, description="Fleet: Add a Steam account")
async def addsteamacc(interaction: Interaction):
    if not is_bartender(interaction): return await interaction.response.send_message("❌ Access Denied.", ephemeral=True)
    await interaction.response.send_modal(AddSteamAccModal())

@bot.tree.command(guild=GUILD, description="Fleet: Remove a Steam account")
@app_commands.describe(username="The exact Steam username to remove")
async def removesteamacc(interaction: Interaction, username: str):
    await interaction.response.defer(ephemeral=True)
    if not is_bartender(interaction): return await interaction.followup.send("❌ Access Denied.", ephemeral=True)
    
    accounts = load_accounts()
    original_count = len(accounts)
    accounts = [a for a in accounts if a["username"].lower() != username.lower()]
    
    if len(accounts) < original_count:
        save_accounts(accounts)
        await interaction.followup.send(embed=discord.Embed(title="✅ Fleet Updated", description=f"Removed Steam account `{username}`.", color=CLR.GREEN), ephemeral=True)
    else:
        await interaction.followup.send(f"❌ Account `{username}` not found in Steam Fleet.", ephemeral=True)

@bot.tree.command(guild=GUILD, description="Fleet: Add a Ubisoft account")
async def addubiacc(interaction: Interaction):
    if not is_bartender(interaction): return await interaction.response.send_message("❌ Access Denied.", ephemeral=True)
    await interaction.response.send_modal(AddUbiAccModal())

@bot.tree.command(guild=GUILD, description="Fleet: Remove a Ubisoft account")
@app_commands.describe(email="The exact Ubisoft email to remove")
async def removeubiacc(interaction: Interaction, email: str):
    await interaction.response.defer(ephemeral=True)
    if not is_bartender(interaction): return await interaction.followup.send("❌ Access Denied.", ephemeral=True)
    
    data = load_ubi_accounts()
    found = False
    
    for appid in list(data.keys()):
        original_len = len(data[appid])
        data[appid] = [a for a in data[appid] if a["email"].lower() != email.lower()]
        if len(data[appid]) < original_len:
            found = True
        if not data[appid]:
            del data[appid]
            
    if found:
        save_ubi_accounts(data)
        await interaction.followup.send(embed=discord.Embed(title="✅ Fleet Updated", description=f"Scrubbed Ubisoft account `{email}` from all databases.", color=CLR.GREEN), ephemeral=True)
    else:
        await interaction.followup.send(f"❌ Account `{email}` not found in Ubisoft Fleet.", ephemeral=True)

@bot.tree.command(guild=GUILD, description="Fleet: Unflag a user so they can donate again")
@app_commands.describe(user_id="The Discord User ID to unflag")
async def removeflag(interaction: Interaction, user_id: str):
    await interaction.response.defer(ephemeral=True)
    if not is_bartender(interaction): return await interaction.followup.send("❌ Access Denied.", ephemeral=True)
    
    flags = load_donations("flag")
    original_count = len(flags)
    flags = [f for f in flags if str(f["user_id"]) != user_id.strip()]
    
    if len(flags) < original_count:
        save_donations("flag", flags)
        await interaction.followup.send(embed=discord.Embed(title="✅ Flag Removed", description=f"User ID `{user_id}` has been unflagged.", color=CLR.GREEN), ephemeral=True)
    else:
        await interaction.followup.send(f"❌ User ID `{user_id}` is not currently flagged.", ephemeral=True)

@bot.tree.command(guild=GUILD, description="Vault: Reset a user's ticket cooldown")
@app_commands.describe(user_id="The Discord User ID whose ticket cooldown should be cleared")
async def resetcooldown(interaction: Interaction, user_id: str):
    await interaction.response.defer(ephemeral=True)
    if not is_bartender(interaction): return await interaction.followup.send("âŒ Access Denied.", ephemeral=True)

    target_id = user_id.strip()
    if not target_id.isdigit():
        return await interaction.followup.send("âŒ Please provide a valid Discord User ID.", ephemeral=True)

    if clear_ticket_cooldown(target_id):
        await interaction.followup.send(
            embed=discord.Embed(
                title="âœ… Cooldown Reset",
                description=f"Ticket cooldown cleared for User ID `{target_id}`.",
                color=CLR.GREEN
            ),
            ephemeral=True
        )
    else:
        await interaction.followup.send(
            embed=discord.Embed(
                title="âŒ No Cooldown Found",
                description=f"User ID `{target_id}` does not currently have a saved ticket cooldown.",
                color=CLR.RED
            ),
            ephemeral=True
        )

@bot.tree.command(guild=GUILD, description="Securely close and wipe ticket")
async def delete(interaction: Interaction):
    await interaction.response.defer(ephemeral=False)
    if not is_bartender(interaction): return await interaction.followup.send("❌ Access Denied.", ephemeral=True)
    if "ticket-" not in interaction.channel.name: return await interaction.followup.send("❌ Command must be used in a ticket.", ephemeral=True)
    await close_ticket_channel(
        interaction.channel,
        apply_cooldown=True,
        notice="Staff closed this ticket. Your cooldown has been updated."
    )

@bot.event
async def on_ready():
    pass

def init_files():
    for p, d in [
        (DATA / "orders.json", {"orders": []}),
        (DATA / "cooldowns.json", {}),
        (DATA / "bans.json", {"bans": []}),
        (DATA / "pending_requests.json", []),
        (DATA / "accounts.json", []),
        (DATA / "guards.json", {}),
        (DATA / "ubi_accounts.json", {}),
        (DATA / "pending_ubi.json", []),
        (TICKETS_FILE, {}),
        (VERIFICATION_FILE, {})
    ]:
        if not p.exists(): write_json(p, d)
    if not (DATA / "games.json").exists(): write_json(DATA / "games.json", {"games": []})
    for st in ["pending", "auth", "verify", "tier1", "tier2", "tier3", "tier4plus", "error", "semi_flag", "flag"]:
        if not (DATA / f"donate_{st}.json").exists(): write_json(DATA / f"donate_{st}.json", [])

if __name__ == "__main__":
    init_files()
    if CFG.get("bot_token"): bot.run(CFG["bot_token"])
