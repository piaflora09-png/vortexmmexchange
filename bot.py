import os
import sys
import asyncio
import json
import discord
from discord.ext import commands, tasks
from aiohttp import web
from dotenv import load_dotenv

# Load local .env variables if present
load_dotenv()

# ==============================================================================
# CONFIGURATION (LOADED SECURELY FROM ENVIRONMENT VARIABLES)
# ==============================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", 0))

# Render assigns a dynamic port via the 'PORT' environment variable
API_PORT = int(os.getenv("PORT", 5000))

# The 4 designated Staff Role IDs
STAFF_ROLE_IDS = [
    1568386943945801839,
    1568387066709024788,
    1568387138322694268,
    1568387457752371270,
]

# Exit early if the token is not set
if not BOT_TOKEN:
    print("[ERROR] BOT_TOKEN is missing! Set it in your Render Environment tab.")
    sys.exit(1)

# ==============================================================================
# BOT INITIALIZATION
# ==============================================================================
intents = discord.Intents.default()
intents.members = True      # Required: Server Members Intent enabled in Dev Portal
intents.presences = True    # Required: Presence Intent enabled in Dev Portal

bot = commands.Bot(command_prefix="!", intents=intents)

# In-memory storage & rate-limit cache
banner_cache = {}  # {user_id: {"banner": url, "accent_color": hex}}
live_cache = {
    "server_name": "Vortex Exchange & MM",
    "member_count": 0,
    "online_count": 0,
    "boost_count": 0,
    "boost_tier": 0,
    "staff": []
}

# ==============================================================================
# DATA SCRAPING & ROLE AUDITING
# ==============================================================================
async def update_community_stats():
    global live_cache, banner_cache
    await bot.wait_until_ready()

    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return

    # 1. Total & Online Counts
    total_members = guild.member_count or len(guild.members)
    online_members = sum(
        1 for m in guild.members
        if m.status != discord.Status.offline and not m.bot
    )
    boost_count = guild.premium_subscription_count or 0
    boost_tier = guild.premium_tier or 0

    # 2. Extract Staff Members
    staff_list = []
    seen_user_ids = set()

    for role_id in STAFF_ROLE_IDS:
        role = guild.get_role(role_id)
        if not role:
            continue

        for member in role.members:
            if member.id in seen_user_ids:
                continue
            seen_user_ids.add(member.id)

            # Check if online (Online, Idle, or DND = Online)
            is_online = member.status != discord.Status.offline
            status_text = "online" if is_online else "offline"

            # Profile banner caching to avoid Discord REST rate limits
            if member.id in banner_cache:
                banner_url = banner_cache[member.id]["banner"]
                accent_color = banner_cache[member.id]["accent_color"]
            else:
                banner_url = None
                accent_color = "#6366f1"
                try:
                    full_user = await bot.fetch_user(member.id)
                    if full_user.banner:
                        banner_url = full_user.banner.url
                    if full_user.accent_color:
                        accent_color = str(full_user.accent_color)
                    banner_cache[member.id] = {
                        "banner": banner_url,
                        "accent_color": accent_color
                    }
                except Exception:
                    pass
                await asyncio.sleep(0.15)

            avatar_url = member.display_avatar.url

            # Role styling
            role_hex = str(role.color)
            if role_hex.lower() in ["#000000", "#0"]:
                role_hex = "#38bdf8"

            staff_list.append({
                "id": str(member.id),
                "username": member.name,
                "display_name": member.display_name,
                "avatar": avatar_url,
                "banner": banner_url,
                "accent_color": accent_color,
                "role_id": str(role.id),
                "role_name": role.name,
                "role_color": role_hex,
                "status": status_text,
                "is_online": is_online
            })

    live_cache = {
        "server_name": guild.name,
        "member_count": total_members,
        "online_count": online_members,
        "boost_count": boost_count,
        "boost_tier": boost_tier,
        "staff": staff_list
    }

    try:
        with open("stats.json", "w", encoding="utf-8") as f:
            json.dump(live_cache, f, indent=2)
    except Exception:
        pass

# ==============================================================================
# REAL-TIME EVENT LISTENERS (AUTOMATIC ROLE & STATUS CHANGES)
# ==============================================================================
@bot.event
async def on_member_update(before, after):
    before_roles = {r.id for r in before.roles}
    after_roles = {r.id for r in after.roles}
    target_set = set(STAFF_ROLE_IDS)

    # Automatically add or remove staff when Discord roles change
    if (before_roles & target_set) != (after_roles & target_set):
        print(f"[⚡ ROLE UPDATE] Staff role change detected for {after.display_name}. Updating...")
        await update_community_stats()

@bot.event
async def on_presence_update(before, after):
    # Detect when a staff member changes status (Online <-> Offline)
    has_staff_role = any(r.id in STAFF_ROLE_IDS for r in after.roles)
    if has_staff_role and before.status != after.status:
        print(f"[⚡ STATUS UPDATE] {after.display_name} switched from {before.status} to {after.status}.")
        await update_community_stats()

@bot.event
async def on_member_remove(member):
    # If a staff member leaves or gets kicked/banned, remove them immediately
    has_staff_role = any(r.id in STAFF_ROLE_IDS for r in member.roles)
    if has_staff_role:
        print(f"[⚡ MEMBER LEFT] Staff member {member.display_name} left. Removing from website...")
        await update_community_stats()

@tasks.loop(seconds=30)
async def stats_updater_task():
    await update_community_stats()

@bot.event
async def on_ready():
    print("==================================================")
    print(f"Logged in as: {bot.user.name} ({bot.user.id})")
    print(f"Server ID: {GUILD_ID}")
    print("Real-time staff role and presence listener active.")
    print("==================================================")

    guild = bot.get_guild(GUILD_ID)
    if guild:
        try:
            print("[*] Chunking guild members to cache roles...")
            await guild.chunk()
        except Exception as e:
            print(f"[!] Chunking warning: {e}")

    await update_community_stats()
    if not stats_updater_task.is_running():
        stats_updater_task.start()

# ==============================================================================
# NATIVE CORS REST API (RUNS ON RENDER PORT)
# ==============================================================================
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "*"
}

async def handle_get_stats(request):
    return web.json_response(live_cache, headers=CORS_HEADERS)

async def handle_root(request):
    return web.json_response({"status": "online", "service": "Vortex API"}, headers=CORS_HEADERS)

async def handle_options(request):
    return web.Response(headers=CORS_HEADERS)

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/api/stats", handle_get_stats)
    app.router.add_route("OPTIONS", "/api/stats", handle_options)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", API_PORT)
    await site.start()
    print(f"[✓] Live Stats API active on port {API_PORT}")

# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================
async def main():
    async with bot:
        await start_web_server()
        await bot.start(BOT_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot process stopped by user.")
