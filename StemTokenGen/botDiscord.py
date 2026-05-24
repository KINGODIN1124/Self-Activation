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
import pytesseract
from PIL import Image, ImageEnhance, ImageOps
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

BASE = Path(__file__).parent

if os.name == 'nt': 
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
else:
    pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'

DATA = BASE / "data"
ASSETS = BASE / "assets"
COMPLETED_DIR = BASE / "completed_configs"
DATA.mkdir(parents=True, exist_ok=True)
ASSETS.mkdir(parents=True, exist_ok=True)
COMPLETED_DIR.mkdir(parents=True, exist_ok=True)

verification_queue = {}

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

STAFF_ROLE_ID = 1494646077893382154 

def get_ban(user_id: str):
    bans = load_bans()
    b = next((x for x in bans if x["userId"] == str(user_id)), None)
    if not b: return None
    if b.get("until") and datetime.fromisoformat(b["until"]) < datetime.now(timezone.utc).replace(tzinfo=None):
        save_bans([x for x in bans if x["userId"] != str(user_id)])
        return None
    return b

def set_ticket_cooldown(user_id: str):
    cds = load_cds()
    cds[f"ticket_{user_id}"] = now_iso()
    save_cds(cds)

def get_ticket_cooldown(user_id: str) -> Optional[str]:
    cds = load_cds()
    last = cds.get(f"ticket_{user_id}")
    if not last: return None
    diff = datetime.now(timezone.utc).replace(tzinfo=None) - datetime.fromisoformat(last)
    rem = timedelta(hours=24) - diff
    if rem.total_seconds() > 0:
        return f"{int(rem.total_seconds() // 3600)}h {int((rem.total_seconds() % 3600) // 60)}m"
    return None

def is_bartender(interaction: Interaction) -> bool:
    if interaction.user.guild_permissions.administrator: return True
    if isinstance(interaction.user, discord.Member):
        for role in interaction.user.roles:
            if role.id == STAFF_ROLE_ID: return True
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

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

class PubBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.add_view(DirectPanelView())
        guild_id = CFG.get("guild_id")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else: await self.tree.sync()

bot = PubBot()

class StaffOverrideView(View):
    def __init__(self, game: dict, user: discord.Member, req_type: str):
        super().__init__(timeout=None)
        self.game = game
        self.user = user
        self.req_type = req_type

    @discord.ui.button(label="🛡️ Staff Override (Force Approve)", style=discord.ButtonStyle.danger, custom_id="force_approve")
    async def force_approve(self, interaction: Interaction, button: Button):
        if not is_bartender(interaction):
            return await interaction.response.send_message("❌ Only staff can override OCR failures.", ephemeral=True)
        
        await interaction.response.send_message("🛡️ **Staff Override Applied.** Bypassing security checks...", ephemeral=False)
        for child in self.children: child.disabled = True
        await interaction.message.edit(view=self)
        
        if interaction.channel.id in verification_queue:
            del verification_queue[interaction.channel.id]
            
        if self.req_type == "steam":
            await trigger_worker(interaction, self.game, self.user)
        elif self.req_type == "ubi":
            await prompt_ubi_token(interaction.channel, self.user, self.game)

class VerificationInitView(View):
    def __init__(self, game: dict, user: discord.Member, req_type: str):
        super().__init__(timeout=None)
        self.game = game
        self.user = user
        self.req_type = req_type

    @discord.ui.button(label="📸 Upload Screenshot Now", style=discord.ButtonStyle.primary, custom_id="start_ocr")
    async def start_ocr(self, interaction: Interaction, button: Button):
        await interaction.response.defer()
        if interaction.user.id != self.user.id: return await interaction.followup.send("❌ This isn't your ticket!", ephemeral=True)
        
        verification_queue[interaction.channel.id] = {"type": self.req_type, "game": self.game, "user": self.user}
        
        for child in self.children: child.disabled = True
        await interaction.edit_original_response(view=self)
        
        req_embed = discord.Embed(
            title="🔍 SYSTEM VERIFICATION REQUIRED", color=CLR.BLUE,
            description=(
                "To prevent token wastage, our automated system needs to verify your setup.\n\n"
                "**Please upload ONE screenshot directly in this chat that shows BOTH:**\n"
                "📂 **1.** Your game folder's `Properties` Window (Proving size is > 10GB).\n"
                "🛡️ **2.** Your `Windows Update Blocker` showing **Disable Updates** is active.\n\n"
                "*Reference the example image below if you are confused.*"
            )
        )
        
        img_path = ASSETS / "wub_example.png"
        if img_path.exists():
            file = discord.File(img_path, filename="wub_example.png")
            req_embed.set_image(url="attachment://wub_example.png")
            await interaction.channel.send(content=self.user.mention, embed=req_embed, file=file)
        else:
            await interaction.channel.send(content=self.user.mention, embed=req_embed)

class TicketInitView(View):
    def __init__(self, game: dict, user: discord.Member, req_type: str):
        super().__init__(timeout=None)
        self.game = game
        self.user = user
        self.req_type = req_type

    @discord.ui.button(label="✅ Yes, My Game Is Ready", style=discord.ButtonStyle.success, custom_id="init_yes")
    async def init_yes(self, interaction: Interaction, button: Button):
        await interaction.response.defer()
        if interaction.user.id != self.user.id: return await interaction.followup.send("❌ This isn't your ticket!", ephemeral=True)
        for child in self.children: child.disabled = True
        await interaction.edit_original_response(view=self)
        await interaction.channel.send(view=VerificationInitView(self.game, self.user, self.req_type))

class DirectPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)
        games = load_games()
        
        steam_games = [g for g in games if g.get('platform', 'steam') == 'steam']
        ubi_games = [g for g in games if g.get('platform') == 'ubi']
        
        steam_opts = [discord.SelectOption(label=f"{g.get('emoji','🎮')} {g['name']}"[:100], description=f"AppID: {g['app_id']}", value=g["id"]) for g in steam_games[:25]]
        if not steam_opts:
            steam_opts = [discord.SelectOption(label="No Steam games available", value="none")]
        
        steam_sel = Select(placeholder="--- 🌟 Steam Activation 🌟 ---", options=steam_opts, custom_id="direct_steam_select")
        steam_sel.callback = self._on_select
        self.add_item(steam_sel)

        ubi_opts = [discord.SelectOption(label=f"{g.get('emoji','⚡')} {g['name']}"[:100], description=f"AppID: {g['app_id']}", value=g["id"]) for g in ubi_games[:25]]
        if not ubi_opts:
            ubi_opts = [discord.SelectOption(label="No Ubisoft games available", value="none")]
        
        ubi_sel = Select(placeholder="--- ⚡ Ubisoft Activation ⚡ ---", options=ubi_opts, custom_id="direct_ubi_select")
        ubi_sel.callback = self._on_select
        self.add_item(ubi_sel)

    async def _on_select(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        game_id = interaction.data["values"][0]
        if game_id == "none":
            return await interaction.followup.send("❌ No games currently available in this category.", ephemeral=True)
            
        game = next((g for g in load_games() if g["id"] == game_id), None)
        if not game: return await interaction.followup.send("❌ Game not found in vault.", ephemeral=True)

        ban = get_ban(str(interaction.user.id))
        if ban: return await interaction.followup.send(embed=discord.Embed(title="🔨 Access Denied", description=f"Reason: {ban.get('reason','N/A')}", color=CLR.RED), ephemeral=True)

        cd_rem = get_ticket_cooldown(str(interaction.user.id))
        if cd_rem: return await interaction.followup.send(embed=discord.Embed(title="⏳ Cooldown Active", description=f"You must wait **{cd_rem}** before requesting another game.", color=CLR.RED), ephemeral=True)

        guild = interaction.guild
        category = discord.utils.get(guild.categories, name="Game Tickets")
        if not category: category = await guild.create_category("Game Tickets")

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
        }
        staff_role = guild.get_role(STAFF_ROLE_ID)
        if staff_role: overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True)

        ticket_channel = await guild.create_text_channel(name=f"ticket-{interaction.user.name}", category=category, overwrites=overwrites, topic=str(interaction.user.id))

        is_ubisoft = game.get('platform') == 'ubi'
        req_type = "ubi" if is_ubisoft else "steam"

        init_embed = discord.Embed(
            title=f"🔐 CRACK WORLD TERMINAL: {game['name'].upper()}", 
            color=CLR.BLUE if is_ubisoft else CLR.DARK,
            description=(
                f"Welcome to your private activation session, {interaction.user.mention}.\n\n"
                "**📑 SESSION DETAILS:**\n"
                f"**• Target:** {game['name']}\n"
                f"**• Engine:** {'Denuvo Protocol' if is_ubisoft else 'Steam Fleet Architecture'}\n"
                f"**• Store AppID:** `{game['app_id']}`\n\n"
                "**⚠️ STRICT PREREQUISITES:**\n"
                "**1.** The game must be **100% fully downloaded and installed**.\n"
                "**2.** Windows Updates must be **strictly blocked** using the official [Windows Update Blocker (WUB)](https://www.sordum.org/downloads/?st-windows-update-blocker).\n\n"
                "If you do not meet these requirements, do not proceed. Token wastage will result in a timeout."
            )
        )
        init_embed.set_image(url=steam_img(str(game['app_id'])))
        init_embed.set_footer(text=f"Crack World Automated Systems • Awaiting User Confirmation")
            
        await ticket_channel.send(content=f"{interaction.user.mention}", embed=init_embed, view=TicketInitView(game, interaction.user, req_type))
        await interaction.followup.send(f"✅ Secure channel established: {ticket_channel.mention}", ephemeral=True)

class AddSteamGameModal(Modal, title="Vault: Add Steam Game"):
    g_name = TextInput(label="Game Name", max_length=100, required=True)
    g_appid = TextInput(label="Steam AppID", max_length=20, required=True)
    g_emoji = TextInput(label="Emoji (Optional)", max_length=10, required=False, default="🎮")
    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        games = load_games()
        gid = self.g_appid.value.strip()
        if any(str(g["app_id"]) == gid for g in games): return await interaction.followup.send("❌ Game with this AppID already exists.", ephemeral=True)
        games.append({"id": f"steam_{gid}", "name": self.g_name.value.strip(), "app_id": gid, "platform": "steam", "emoji": self.g_emoji.value.strip() or "🎮"})
        save_games(games)
        await interaction.followup.send(embed=discord.Embed(title="✅ Vault Updated", description=f"Added **{self.g_name.value}** (Steam Engine).", color=CLR.GREEN), ephemeral=True)

class AddUbiGameModal(Modal, title="Vault: Add Ubisoft Game"):
    g_name = TextInput(label="Game Name", max_length=100, required=True)
    g_appid = TextInput(label="Store AppID (For Artwork Image)", max_length=20, required=True)
    g_emoji = TextInput(label="Emoji (Optional)", max_length=10, required=False, default="🎮")
    async def on_submit(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        games = load_games()
        gid = self.g_appid.value.strip()
        if any(str(g["app_id"]) == gid for g in games): return await interaction.followup.send("❌ Game with this AppID already exists.", ephemeral=True)
        games.append({"id": f"ubi_{gid}", "name": self.g_name.value.strip(), "app_id": gid, "platform": "ubi", "emoji": self.g_emoji.value.strip() or "🎮"})
        save_games(games)
        await interaction.followup.send(embed=discord.Embed(title="✅ Vault Updated", description=f"Added **{self.g_name.value}** (Ubisoft/Denuvo Engine).", color=CLR.GREEN), ephemeral=True)

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
        title=f"✅ System Verified: {game['name']}", color=CLR.GREEN,
        description=(
            "Your system meets all security requirements.\n\n"
            "**DENUVO ACTIVATION STEPS:**\n"
            "**1.** Launch the game using the Crack World launcher bypass files.\n"
            "**2.** When the activation window appears, copy the Denuvo request code.\n"
            "**3.** Upload the **`token_req_<numbers>.txt`** file directly in this chat.\n\n"
            "⏳ *Our servers are standing by to process your request...*"
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
    
    loading_embed = discord.Embed(title="⚙️ COMPILING BYPASS...", color=CLR.AMBER, description="Pinging Steam Worker Fleet to generate your secure token...")
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
            title="✅ BYPASS READY", color=CLR.GREEN,
            description=(
                f"Files compiled successfully for **{game['name']}**.\n\n"
                "**INSTRUCTIONS:**\n"
                "**1.** Extract the ZIP file completely.\n"
                "**2.** Paste the contents directly into your main game directory.\n"
                "**3.** Start the game.\n\n"
                "⏳ *Warning: This token will expire in 30 minutes!*\n"
                "🛡️ **Do not close this ticket. Wait for a Staff member to review.**"
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
    else: await status_msg.edit(embed=discord.Embed(title="❌ FLEET TIMEOUT", color=CLR.RED, description="Steam worker timed out. No available accounts currently hold this license."))

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot: return
    
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
                    view = StaffOverrideView(verification_queue[message.channel.id]["game"], verification_queue[message.channel.id]["user"], verification_queue[message.channel.id]["type"])
                    await status_msg.edit(content=None, embed=discord.Embed(title="🛑 VERIFICATION REJECTED", description=err_msg, color=CLR.RED), view=view)
                else:
                    await status_msg.edit(content="✅ **System Verified. Security checks passed.**")
                    queue_data = verification_queue.pop(message.channel.id)
                    game = queue_data["game"]
                    user = queue_data["user"]
                    req_type = queue_data["type"]
                    
                    if req_type == "steam":
                        class MockInteraction:
                            def __init__(self, ch): self.channel = ch
                        await trigger_worker(MockInteraction(message.channel), game, user)
                    elif req_type == "ubi":
                        await prompt_ubi_token(message.channel, user, game)

            except Exception as e:
                print(f"OCR Error: {e}")
                view = StaffOverrideView(verification_queue[message.channel.id]["game"], verification_queue[message.channel.id]["user"], verification_queue[message.channel.id]["type"])
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
                        "**1.** Download **`token.txt`** and place it in your game folder.\n"
                        "**2.** Launch the game and upload a screenshot of it running as proof.\n\n"
                        "🛡️ Wait for staff to verify and close your session."
                    )
                    file_attachment = discord.File(expected_file, filename="token.txt")
                    
                    games = load_games()
                    matching_game = next((g for g in games if str(g['app_id']) == a_val.strip()), None)
                    success_embed = discord.Embed(title="✅ DENUVO TOKEN GENERATED", description=final_text, color=CLR.GREEN)
                    if matching_game:
                        success_embed.set_image(url=steam_img(str(matching_game['app_id'])))
                        
                    await message.channel.send(embed=success_embed, file=file_attachment)
                    expected_file.unlink()
                else:
                    await message.channel.send(embed=discord.Embed(description="❌ Ubisoft Fleet failed. No accounts hold a valid license for this request.", color=CLR.RED))

            except Exception as e:
                await message.channel.send(embed=discord.Embed(description="⚠️ System error while reading file.", color=CLR.AMBER))

GUILD = discord.Object(id=int(CFG.get("guild_id", 0))) if CFG.get("guild_id") else None

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
    embed = discord.Embed()
    
    if tier_val == "free":
        embed.title = "—— FREE ACTIVATION PANEL ——"
        embed.color = CLR.DARK
        banner_url = "https://media.discordapp.net/attachments/1456346084732571649/1474723315170938962/KCgLyPX.gif"
    elif tier_val == "bronze":
        embed.title = "—— BRONZE ACTIVATION PANEL ——"
        embed.color = CLR.BRONZE
        banner_url = "https://media.discordapp.net/attachments/1456346084732571649/1474723315859066931/hCGoD2M.gif"
    elif tier_val == "silver":
        embed.title = "—— SILVER ACTIVATION PANEL ——"
        embed.color = CLR.SILVER
        banner_url = "https://media.discordapp.net/attachments/1456346084732571649/1474723316194349180/W3LSE6S.gif"
    elif tier_val == "gold":
        embed.title = "—— GOLD ACTIVATION PANEL ——"
        embed.color = CLR.GOLD
        banner_url = "https://media.discordapp.net/attachments/1456346084732571649/1474723316630552781/DrMpQpn.gif"

    embed.description = (
        f"**{tier.name} Session Offline Activations** for YouTube Subscribers!\n\n"
        "**Your Eligibility:** This service is available only to confirmed YouTube Channel Subscribers.\n\n"
        "**Activation Steps:**\n"
        "1. Create Your Ticket (select below)\n\n"
        "⚠️ **NEW VERIFICATION PROCESS**\n"
        "**Select game -> Server verification -> Screenshot -> Ticket**\n"
        "Now includes screenshot verification for security."
    )
    embed.set_image(url=banner_url)
    embed.set_footer(text=f"CRACK WORLD • {tier.name} Panel")
    
    await interaction.channel.send(embed=embed, view=DirectPanelView())
    await interaction.followup.send(f"{tier.name} Panel deployed successfully.", ephemeral=True)

@bot.tree.command(guild=GUILD, description="Vault: Add a Steam game")
async def addsteamgame(interaction: Interaction):
    if not is_bartender(interaction): return await interaction.response.send_message("❌ Access Denied.", ephemeral=True)
    await interaction.response.send_modal(AddSteamGameModal())

@bot.tree.command(guild=GUILD, description="Vault: Add a Ubisoft/Denuvo game")
async def addubigame(interaction: Interaction):
    if not is_bartender(interaction): return await interaction.response.send_message("❌ Access Denied.", ephemeral=True)
    await interaction.response.send_modal(AddUbiGameModal())

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

@bot.tree.command(guild=GUILD, description="Securely close and wipe ticket")
async def delete(interaction: Interaction):
    await interaction.response.defer(ephemeral=False)
    if not is_bartender(interaction): return await interaction.followup.send("❌ Access Denied.", ephemeral=True)
    if "ticket-" not in interaction.channel.name: return await interaction.followup.send("❌ Command must be used in a ticket.", ephemeral=True)
    user_id = interaction.channel.topic
    if user_id and user_id.isdigit(): set_ticket_cooldown(user_id)
    await interaction.followup.send("🗑️ **Scrubbing channel data...** Deleting in 3 seconds.", ephemeral=False)
    await asyncio.sleep(3)
    await interaction.channel.delete()

@bot.event
async def on_ready():
    print(f"[SYSTEM] Bot Online as {bot.user}")

def init_files():
    for p, d in [(DATA / "orders.json", {"orders": []}), (DATA / "cooldowns.json", {}), (DATA / "bans.json", {"bans": []}), (DATA / "pending_requests.json", []), (DATA / "accounts.json", []), (DATA / "guards.json", {}), (DATA / "ubi_accounts.json", {}), (DATA / "pending_ubi.json", [])]:
        if not p.exists(): write_json(p, d)
    if not (DATA / "games.json").exists(): write_json(DATA / "games.json", {"games": []})

if __name__ == "__main__":
    init_files()
    if CFG.get("bot_token"): bot.run(CFG["bot_token"])