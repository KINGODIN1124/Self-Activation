import discord
import json
from pathlib import Path

# Load Config
BASE = Path(__file__).parent
try:
    with open(BASE / "config.json", "r", encoding="utf-8") as f:
        CFG = json.load(f)
except Exception:
    CFG = {}

class CommandWiper(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = discord.app_commands.CommandTree(self)

    async def setup_hook(self):
        print("🧹 Preparing to wipe old commands from Discord's backend...")
        
        # 1. Clear Global Commands
        self.tree.clear_commands(guild=None)
        await self.tree.sync(guild=None)
        print("✅ Global commands wiped.")

        # 2. Clear Guild Commands
        guild_id = CFG.get("guild_id")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.clear_commands(guild=guild)
            await self.tree.sync(guild=guild)
            print(f"✅ Server commands wiped for Guild ID: {guild_id}")

    async def on_ready(self):
        print("\n🎉 SUCCESS! All ghost commands have been deleted.")
        print("Closing script...")
        await self.close()

if __name__ == "__main__":
    token = CFG.get("bot_token")
    if not token:
        print("❌ bot_token missing from config.json")
    else:
        client = CommandWiper()
        client.run(token)