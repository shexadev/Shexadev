import discord
from discord.ext import commands, tasks
import logging
import json
import os
import random
import time
import math
import datetime
import re
import asyncio
import sqlite3
import aiohttp
import concurrent.futures as _cf
from dotenv import load_dotenv

load_dotenv()
token = os.getenv('DISCORD_TOKEN')

# Fallback: load from token.txt if env var not set
if not token:
    try:
        with open('token.txt', 'r') as _f:
            token = _f.read().strip()
        print("Token loaded from token.txt")
    except FileNotFoundError:
        pass

handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True
intents.presences = True  # REQUIRED FOR THE COOL STATUS AFK FEATURE

bot = commands.Bot(command_prefix='$', intents=intents, help_command=None)
bot_start_time = time.time()

LEVEL_ICONS = {range(1,5):"🌱", range(5,10):"⚡", range(10,20):"🔥", range(20,30):"💎", range(30,50):"👑"}
def level_icon(lvl):
    return next((v for k,v in LEVEL_ICONS.items() if lvl in k), "🌟")


# 🌍 BILINGUAL LANGUAGE SYSTEM
LANG = {
    "en": {
        "welcome": "Welcome to the server! | بەخێربێیت بۆ سێرڤەر!",
        "help": "Here are my commands | ئەمەش فەرمانەکانم",
        "language_set": "Language changed to English 🇺🇸 | زمان گۆڕدرا بۆ ئینگلیزی",
        "ping": "Bot latency | خێرایی بۆت",
        "success": "Success | سەرکەوتوو",
        "error": "Error | هەڵە"
    },
    "ku": {
        "welcome": "بەخێربێیت بۆ سێرڤەر! | Welcome to the server!",
        "help": "ئەمەش فەرمانەکانم | Here are my commands",
        "language_set": "زمان گۆڕدرا بۆ کوردی 🇹🇯 | Language changed to Kurdish",
        "ping": "خێرایی بۆت | Bot latency",
        "success": "سەرکەوتوو | Success",
        "error": "هەڵە | Error"
    }
}

user_langs = {}

def tr(user_id, key):
    lang = user_langs.get(user_id, "en")
    return LANG.get(lang, LANG["en"]).get(key, key)


# --- SQLITE DATABASE SETUP ---
DB_FILE = "bot_data.db"

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS xp_data (
            guild_id INTEGER,
            user_id INTEGER,
            message_xp INTEGER DEFAULT 0,
            voice_xp INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS economy (
            guild_id INTEGER,
            user_id INTEGER,
            wallet INTEGER DEFAULT 0,
            bank INTEGER DEFAULT 0,
            last_daily REAL DEFAULT 0,
            last_weekly REAL DEFAULT 0,
            last_work REAL DEFAULT 0,
            last_beg REAL DEFAULT 0,
            inventory TEXT DEFAULT '[]',
            PRIMARY KEY (guild_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS warnings (
            guild_id INTEGER,
            user_id INTEGER,
            count INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS afk_users (
            guild_id INTEGER,
            user_id INTEGER,
            reason TEXT,
            since REAL,
            old_nick TEXT,
            image_url TEXT,
            PRIMARY KEY (guild_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS level_channels (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS lucky_leaderboard (
            guild_id INTEGER,
            user_id INTEGER,
            wins INTEGER DEFAULT 0,
            total_guesses INTEGER DEFAULT 0,
            best_guesses INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS welcome_channels (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS ticket_settings (
            guild_id INTEGER PRIMARY KEY,
            staff_role_id INTEGER,
            category_id INTEGER,
            log_channel_id INTEGER,
            panel_channel_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS open_tickets (
            guild_id INTEGER,
            user_id INTEGER,
            channel_id INTEGER,
            PRIMARY KEY (guild_id, user_id)
        );
    """)
    conn.commit()
    conn.close()

init_db()

secret_role = "Gamer"

MESSAGE_XP_MIN = 15
MESSAGE_XP_MAX = 25
MESSAGE_XP_COOLDOWN = 60
VOICE_XP_PER_MINUTE = 10

xp_data = {}
warnings_data = {}
economy = {}
afk_users = {}
afk_cooldowns = {}
level_channels = {}
welcome_channels = {}
invite_channels = {}
invite_data = {}
level_enabled = {}
ticket_settings = {}
open_tickets_map = {}
message_cooldowns = {}
voice_sessions = {}

EIGHT_BALL_RESPONSES = [
    "It is certain. | دڵنیاییە.",
    "Without a doubt. | بێ گومان.",
    "Yes - definitely. | بەڵێ - بەتەواوی.",
    "You may rely on it. | دەتوانیت پشت بدەی پێی.",
    "Most likely. | زۆرجار.",
    "Outlook good. | دیمەن باشە.",
    "Yes. | بەڵێ.",
    "Signs point to yes. | نیشانەکان بەرەو بەڵێ دەکەن.",
    "Reply hazy, try again. | وەڵامەکە شلەوەیە، دووبارە هەوڵبدەرەوە.",
    "Ask again later. | دواتر دووبارە بپرسە.",
    "Better not tell you now. | باشترە ئێستا نەتبڵێم.",
    "Cannot predict now. | ئێستا نایتوانم پێشبینی بکەم.",
    "Concentrate and ask again. | تەمەرکوز بکە و دووبارە بپرسە.",
    "Don't count on it. | سانی مەدە.",
    "My reply is no. | وەڵامم نەخێرە.",
    "My sources say no. | سەرچاوەکانم نەخێر دەڵێن.",
    "Outlook not so good. | دیمەن زۆر باش نییە.",
    "Very doubtful. | زۆر گومانپێکراوە.",
]

# --- SQLITE LOAD/SAVE FUNCTIONS ---

def load_xp():
    global xp_data
    xp_data = {}
    conn = get_db()
    for row in conn.execute("SELECT guild_id, user_id, message_xp, voice_xp FROM xp_data"):
        g = str(row["guild_id"])
        u = str(row["user_id"])
        xp_data.setdefault(g, {})[u] = {"message_xp": row["message_xp"], "voice_xp": row["voice_xp"]}
    conn.close()

def save_xp():
    conn = get_db()
    for gid, users in xp_data.items():
        for uid, entry in users.items():
            conn.execute(
                "INSERT INTO xp_data (guild_id, user_id, message_xp, voice_xp) VALUES (?,?,?,?) "
                "ON CONFLICT(guild_id, user_id) DO UPDATE SET message_xp=excluded.message_xp, voice_xp=excluded.voice_xp",
                (int(gid), int(uid), entry.get("message_xp", 0), entry.get("voice_xp", 0))
            )
    conn.commit()
    conn.close()

def save_xp_entry(guild_id, user_id):
    entry = xp_data.get(str(guild_id), {}).get(str(user_id), {"message_xp": 0, "voice_xp": 0})
    conn = get_db()
    conn.execute(
        "INSERT INTO xp_data (guild_id, user_id, message_xp, voice_xp) VALUES (?,?,?,?) "
        "ON CONFLICT(guild_id, user_id) DO UPDATE SET message_xp=excluded.message_xp, voice_xp=excluded.voice_xp",
        (int(guild_id), int(user_id), entry.get("message_xp", 0), entry.get("voice_xp", 0))
    )
    conn.commit()
    conn.close()

def load_warnings():
    global warnings_data
    warnings_data = {}
    conn = get_db()
    for row in conn.execute("SELECT guild_id, user_id, count FROM warnings"):
        g = str(row["guild_id"])
        u = str(row["user_id"])
        warnings_data.setdefault(g, {})[u] = row["count"]
    conn.close()

def save_warnings():
    conn = get_db()
    for gid, users in warnings_data.items():
        for uid, count in users.items():
            conn.execute(
                "INSERT INTO warnings (guild_id, user_id, count) VALUES (?,?,?) "
                "ON CONFLICT(guild_id, user_id) DO UPDATE SET count=excluded.count",
                (int(gid), int(uid), count)
            )
    conn.commit()
    conn.close()

def load_econ():
    global economy
    economy = {}
    conn = get_db()
    for row in conn.execute("SELECT guild_id, user_id, wallet, bank, last_daily, last_weekly, last_work, last_beg, inventory FROM economy"):
        g = str(row["guild_id"])
        u = str(row["user_id"])
        economy.setdefault(g, {})[u] = {
            "wallet": row["wallet"], "bank": row["bank"],
            "last_daily": row["last_daily"], "last_weekly": row["last_weekly"],
            "last_work": row["last_work"], "last_beg": row["last_beg"],
            "inventory": json.loads(row["inventory"] or "[]"),
        }
    conn.close()

def save_econ():
    conn = get_db()
    for gid, users in economy.items():
        for uid, e in users.items():
            conn.execute(
                "INSERT INTO economy (guild_id, user_id, wallet, bank, last_daily, last_weekly, last_work, last_beg, inventory) "
                "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(guild_id, user_id) DO UPDATE SET "
                "wallet=excluded.wallet, bank=excluded.bank, last_daily=excluded.last_daily, "
                "last_weekly=excluded.last_weekly, last_work=excluded.last_work, last_beg=excluded.last_beg, "
                "inventory=excluded.inventory",
                (int(gid), int(uid), e["wallet"], e["bank"], e["last_daily"], e["last_weekly"],
                 e["last_work"], e["last_beg"], json.dumps(e["inventory"]))
            )
    conn.commit()
    conn.close()

def load_level_channels():
    global level_channels
    level_channels = {}
    conn = get_db()
    for row in conn.execute("SELECT guild_id, channel_id FROM level_channels"):
        level_channels[str(row["guild_id"])] = row["channel_id"]
    conn.close()

def save_level_channels():
    conn = get_db()
    for gid, cid in level_channels.items():
        conn.execute(
            "INSERT INTO level_channels (guild_id, channel_id) VALUES (?,?) "
            "ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id",
            (int(gid), int(cid))
        )
    conn.commit()
    conn.close()

def load_ticket_settings():
    global ticket_settings, open_tickets_map
    ticket_settings = {}
    open_tickets_map = {}
    conn = get_db()
    for row in conn.execute("SELECT guild_id, staff_role_id, category_id, log_channel_id, panel_channel_id FROM ticket_settings"):
        ticket_settings[str(row["guild_id"])] = {
            "staff_role_id": row["staff_role_id"],
            "category_id": row["category_id"],
            "log_channel_id": row["log_channel_id"],
            "panel_channel_id": row["panel_channel_id"],
        }
    for row in conn.execute("SELECT guild_id, user_id, channel_id FROM open_tickets"):
        open_tickets_map[(str(row["guild_id"]), str(row["user_id"]))] = row["channel_id"]
    conn.close()

def save_ticket_settings():
    conn = get_db()
    for gid, s in ticket_settings.items():
        conn.execute(
            "INSERT INTO ticket_settings (guild_id, staff_role_id, category_id, log_channel_id, panel_channel_id) "
            "VALUES (?,?,?,?,?) ON CONFLICT(guild_id) DO UPDATE SET "
            "staff_role_id=excluded.staff_role_id, category_id=excluded.category_id, "
            "log_channel_id=excluded.log_channel_id, panel_channel_id=excluded.panel_channel_id",
            (int(gid), s.get("staff_role_id"), s.get("category_id"), s.get("log_channel_id"), s.get("panel_channel_id"))
        )
    conn.commit()
    conn.close()

def save_open_tickets():
    conn = get_db()
    conn.execute("DELETE FROM open_tickets")
    for (gid, uid), cid in open_tickets_map.items():
        conn.execute(
            "INSERT INTO open_tickets (guild_id, user_id, channel_id) VALUES (?,?,?)",
            (int(gid), int(uid), int(cid))
        )
    conn.commit()
    conn.close()

def get_ticket_cfg(guild_id):
    return ticket_settings.get(str(guild_id), {})

async def ticket_log(guild, message):
    cfg = get_ticket_cfg(guild.id)
    log_cid = cfg.get("log_channel_id")
    if not log_cid:
        return
    ch = guild.get_channel(int(log_cid))
    if ch:
        embed = discord.Embed(color=0x5865f2, description=message, timestamp=datetime.datetime.utcnow())
        try:
            await ch.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

def load_welcome_channels():
    global welcome_channels
    welcome_channels = {}
    conn = get_db()
    for row in conn.execute("SELECT guild_id, channel_id FROM welcome_channels"):
        welcome_channels[str(row["guild_id"])] = row["channel_id"]
    conn.close()

def save_welcome_channels():
    conn = get_db()
    for gid, cid in welcome_channels.items():
        conn.execute(
            "INSERT INTO welcome_channels (guild_id, channel_id) VALUES (?,?) "
            "ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id",
            (int(gid), int(cid))
        )
    conn.commit()
    conn.close()

def load_afk():
    global afk_users
    afk_users = {}
    conn = get_db()
    for row in conn.execute("SELECT guild_id, user_id, reason, since, old_nick, image_url FROM afk_users"):
        key = (row["guild_id"], row["user_id"])
        afk_users[key] = {
            "reason": row["reason"], "since": row["since"],
            "old_nick": row["old_nick"], "image_url": row["image_url"]
        }
    conn.close()

def save_afk():
    conn = get_db()
    conn.execute("DELETE FROM afk_users")
    for (gid, uid), data in afk_users.items():
        conn.execute(
            "INSERT INTO afk_users (guild_id, user_id, reason, since, old_nick, image_url) VALUES (?,?,?,?,?,?)",
            (int(gid), int(uid), data.get("reason"), data.get("since"), data.get("old_nick"), data.get("image_url"))
        )
    conn.commit()
    conn.close()

def get_econ(gid, uid):
    g = economy.setdefault(str(gid), {})
    return g.setdefault(str(uid), {
        "wallet": 0, "bank": 0,
        "last_daily": 0, "last_weekly": 0,
        "last_work": 0, "last_beg": 0,
        "inventory": [],
    })

START_TIME = time.time()

# --- UTILITY FUNCTIONS ---

def fmt_uptime(seconds):
    days, seconds = divmod(int(seconds), 86400)
    hours, seconds = divmod(seconds, 3600)
    mins, secs = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins:
        parts.append(f"{mins}m")
    parts.append(f"{secs}s")
    return " ".join(parts)

def get_user_entry(guild_id, user_id):
    g = xp_data.setdefault(str(guild_id), {})
    return g.setdefault(str(user_id), {"message_xp": 0, "voice_xp": 0})

def add_xp(guild_id, user_id, amount, kind):
    entry = get_user_entry(guild_id, user_id)
    entry[kind] = entry.get(kind, 0) + amount
    save_xp_entry(guild_id, user_id)

def total_xp(entry):
    return entry.get("message_xp", 0) + entry.get("voice_xp", 0)

def level_from_xp(xp):
    return int(math.floor(math.sqrt(xp / 100)))

def xp_for_level(level):
    return (level ** 2) * 100

def make_progress_bar(current, total, length=20):
    if total <= 0:
        return "░" * length
    pct = max(0.0, min(1.0, current / total))
    filled = int(pct * length)
    return "█" * filled + "░" * (length - filled)

def humanize_seconds(s):
    s = int(max(0, s))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h {m}m"
    d, h = divmod(h, 24)
    return f"{d}d {h}h"

def get_level_channel(guild):
    cid = level_channels.get(str(guild.id))
    if cid is None:
        return None
    return guild.get_channel(int(cid))

async def announce_level_up(member, fallback_channel, new_level, source):
    target = get_level_channel(member.guild) or fallback_channel
    if target is None:
        return
    color = discord.Color.from_hsv((min(new_level, 50) / 50) * 0.83, 0.90, 1.0)
    icon = level_icon(new_level)
    next_xp = xp_for_level(new_level + 1) - xp_for_level(new_level)
    embed = discord.Embed(
        title=f"🎉 LEVEL UP! | ئاستت بەرزبووەوە!",
        description=(
            f"### {member.mention} reached {icon} **Level {new_level}**!\n"
            f"گەیشتە {icon} **ئاستی {new_level}**!\n\n"
            f"{'💬 Earned from chatting | کسبکرا لە پەیامدانەوە' if source == 'message' else '🎙️ Earned in voice chat | کسبکرا لە دەنگدانەوە'}"
        ),
        color=color,
        timestamp=datetime.datetime.utcnow(),
    )
    embed.set_author(
        name=member.display_name,
        icon_url=member.display_avatar.url if member.display_avatar else None
    )
    if member.display_avatar:
        embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name=f"{icon} Level | ئاست", value=f"**{new_level}**", inline=True)
    embed.add_field(name="🎯 Next Goal | ئامانجی داهاتوو", value=f"`{next_xp:,}` XP", inline=True)
    embed.add_field(name="⚡ Keep Going!", value="Chat & voice = more XP\nپەیام و دەنگ = زیاتر XP", inline=True)
    embed.set_footer(text=f"GG {member.display_name}! ⚡ Keep climbing • بەردەوام بە!")
    try:
        await target.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass

def parse_duration(text):
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    m = re.match(r"^(\d+)([smhd])$", text.lower())
    if not m:
        return None
    return int(m.group(1)) * units[m.group(2)]

# --- INITIALIZE DATA ---
load_xp()
load_warnings()
load_econ()
load_level_channels()
load_welcome_channels()
load_ticket_settings()
load_afk()

# --- BOT EVENTS ---

@bot.event
async def on_ready():
    print(f"We are ready to go in, {bot.user.name}")
    if not voice_xp_tick.is_running():
        voice_xp_tick.start()
    if not autosave.is_running():
        autosave.start()
    if not idle_check.is_running():
        idle_check.start()
    bot.add_view(TicketPanelView())
    bot.add_view(TicketControlView())
    for guild in bot.guilds:
        for vc in guild.voice_channels:
            for member in vc.members:
                if not member.bot:
                    voice_sessions[(guild.id, member.id)] = time.time()

@bot.event
async def on_member_join(member):
    # --- WELCOME EMBED IN CHANNEL ---
    cid = welcome_channels.get(str(member.guild.id))
    if cid:
        channel = member.guild.get_channel(int(cid))
        if channel:
            join_pos = member.guild.member_count
            account_created = member.user.created_at if hasattr(member, 'user') else member.created_at
            now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
            diff_days = (now - account_created).days
            if diff_days >= 365:
                account_age = f"{diff_days // 365} year{'s' if diff_days // 365 != 1 else ''} ago | {diff_days // 365} ساڵ پێش ئێستا"
            elif diff_days >= 30:
                months = diff_days // 30
                account_age = f"{months} month{'s' if months != 1 else ''} ago | {months} مانگ پێش ئێستا"
            else:
                account_age = f"{diff_days} day{'s' if diff_days != 1 else ''} ago | {diff_days} ڕۆژ پێش ئێستا"

            embed = discord.Embed(
                color=0xFFD700,
                title="👑 بەخێربێیت! | Welcome!",
                description=(
                    f"سڵاو {member.mention} 👑 🤩\n"
                    f"خۆش بوویت بە بینینت بۆ **{member.guild.name}**!\n\n"
                    f"🇬🇧 Welcome {member.mention} 👑 🤩\n"
                    f"Glad to have you here in **{member.guild.name}**!"
                ),
                timestamp=datetime.datetime.utcnow(),
            )
            embed.add_field(
                name="📅 ئەکاونت دروستکراوە | Account Created",
                value=account_age,
                inline=True,
            )
            embed.add_field(
                name="🕐 ژمارەی ئەندامان | Member Number",
                value=f"#{join_pos:,}",
                inline=True,
            )
            embed.add_field(
                name="👤 ئەندامی نوێ | New Member",
                value=member.mention,
                inline=True,
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_image(url="attachment://welcome_banner.png")
            embed.set_footer(
                text=member.guild.name,
                icon_url=member.guild.icon.url if member.guild.icon else None,
            )
            try:
                import os
                if os.path.exists("welcome_banner.png"):
                    await channel.send(
                        embed=embed,
                        file=discord.File("welcome_banner.png", filename="welcome_banner.png")
                    )
                else:
                    await channel.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                pass

    # --- DM WELCOME (fallback) ---
    try:
        await member.send(f"Welcome to the server {member.name} | بەخێربێیت بۆ سێرڤەر {member.name}")
    except discord.Forbidden:
        pass

@bot.listen('on_message')
async def on_message(message):
    if message.author == bot.user or message.author.bot:
        return

    if message.guild is not None:
        # --- COOLER AFK SYSTEM: RETURN FROM AFK ---
        key = (message.guild.id, message.author.id)
        if key in afk_users:
            is_afk_cmd = message.content.startswith(bot.command_prefix + "afk")
            is_any_cmd = message.content.startswith(bot.command_prefix)

            if not is_afk_cmd:
                data = afk_users.pop(key)
                save_afk()

                try:
                    if data.get("old_nick") is not None or (message.author.nick and message.author.nick.startswith("💤")):
                        await message.author.edit(nick=data.get("old_nick"))
                except (discord.Forbidden, discord.HTTPException):
                    pass

                try:
                    new_activities = [a for a in message.author.activities if not isinstance(a, discord.CustomActivity)]
                    await message.author.edit(activities=new_activities)
                except Exception:
                    pass

                if not is_any_cmd:
                    try:
                        away = humanize_seconds(time.time() - data.get("since", time.time()))
                        embed = discord.Embed(
                            title="👋  Welcome Back! | بەخێربووتەوە!",
                            color=discord.Color.from_rgb(87, 242, 135),
                        )
                        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
                        embed.add_field(name="⏱️ You were away for | دوور بوویت بۆ ماوەی", value=f"**{away}**", inline=True)
                        if data.get("reason") and data["reason"] != "AFK":
                            embed.add_field(name="📝 Your reason was | هۆکارەکەت بوو", value=f"*{data['reason']}*", inline=True)
                        if data.get("image_url"):
                            embed.set_thumbnail(url=data["image_url"])
                        embed.set_footer(text="Glad to have you back! | خۆشحاڵین کە گەڕایتەوە!")
                        await message.channel.send(embed=embed)
                    except discord.Forbidden:
                        pass

        # --- COOLER AFK SYSTEM: PINGING AN AFK USER ---
        if message.mentions:
            mentioned_msgs = []
            raw_mentions = re.findall(r'<@!?(\d+)>', message.content)

            for u in message.mentions:
                k = (message.guild.id, u.id)
                if k in afk_users and u.id != message.author.id and str(u.id) in raw_mentions:
                    info = afk_users[k]
                    away = humanize_seconds(time.time() - info.get("since", time.time()))

                    ping_embed = discord.Embed(
                        title="💤  This person is AFK | ئەم کەسە AFK یە",
                        color=discord.Color.from_rgb(88, 101, 242),
                    )
                    ping_embed.set_author(name=u.display_name, icon_url=u.display_avatar.url)
                    ping_embed.add_field(name="📝 Reason | هۆکار", value=f"*{info['reason']}*", inline=False)
                    ping_embed.add_field(name="⏱️ Away for | دووری بۆ ماوەی", value=f"**{away}**", inline=True)
                    ping_embed.add_field(name="🕐 Since | لە کاتی", value=f"<t:{int(info.get('since', time.time()))}:R>", inline=True)
                    if info.get("image_url"):
                        ping_embed.set_thumbnail(url=info["image_url"])
                    mentioned_msgs.append(ping_embed)

            for embed in mentioned_msgs:
                try:
                    await message.channel.send(embed=embed)
                except discord.Forbidden:
                    pass

    # --- WORD FILTER ---
    BANNED_WORDS = [
        "shit",
        "fuck",
        "ur mom", "ur mother", "your mother",
        "ur sis", "ur sister",
    ]
    msg_lower = message.content.lower()
    if any(word in msg_lower for word in BANNED_WORDS):
        try:
            await message.delete()
            warn = discord.Embed(
                description=f"⚠️ {message.author.mention} don't use those words! | ئەو وشانە مەبەکارهێنە!",
                color=0xed4245,
            )
            warn_msg = await message.channel.send(embed=warn)
            await asyncio.sleep(5)
            try:
                await warn_msg.delete()
            except Exception:
                pass
        except (discord.Forbidden, discord.HTTPException):
            pass
        return

    # --- MINI-GAMES INTERCEPTS ---
    if message.channel.id in number_games and message.content.strip().isdigit():
        game = number_games[message.channel.id]
        guess_val = int(message.content.strip())
        game["tries"] += 1
        if guess_val < game["number"]:
            await message.channel.send("📈 Higher! | بەرزتر!")
        elif guess_val > game["number"]:
            await message.channel.send("📉 Lower! | نزمتر!")
        else:
            await message.channel.send(
                f"🎉 {message.author.mention} got it in **{game['tries']}** tries! The number was **{game['number']}**.\n"
                f"🎉 {message.author.mention} بەدەستی هێنا لە **{game['tries']}** هەوڵدا! ژمارەکە **{game['number']}** بوو."
            )
            number_games.pop(message.channel.id, None)
        return

    if message.channel.id in lucky_games and message.content.strip().isdigit():
        game = lucky_games[message.channel.id]
        guess_val = int(message.content.strip())
        game["last_activity"] = time.time()
        game["has_guesses"] = True
        game["guesses"] = game.get("guesses", 0) + 1
        if guess_val < 1 or guess_val > 100:
            await message.channel.send("Please pick a number between 1-100 / تکایە ژمارەیەک لەنێوان ١-١٠٠ هەڵبژێرە.")
        elif guess_val == game["number"]:
            task = game.get("task")
            if task:
                task.cancel()
            guesses_taken = game["guesses"]
            guild_id = game.get("guild_id")
            lucky_games.pop(message.channel.id, None)
            if guild_id:
                conn = get_db()
                conn.execute(
                    "INSERT INTO lucky_leaderboard (guild_id, user_id, wins, total_guesses, best_guesses) "
                    "VALUES (?, ?, 1, ?, ?) "
                    "ON CONFLICT(guild_id, user_id) DO UPDATE SET "
                    "wins = wins + 1, "
                    "total_guesses = total_guesses + excluded.total_guesses, "
                    "best_guesses = CASE WHEN best_guesses = 0 OR excluded.best_guesses < best_guesses "
                    "THEN excluded.best_guesses ELSE best_guesses END",
                    (guild_id, message.author.id, guesses_taken, guesses_taken)
                )
                conn.commit()
                conn.close()
            win_embed = discord.Embed(
                title="🎉 Correct! / دروستە!",
                description=(
                    f"**English:** {message.author.mention} got it in **{guesses_taken}** guess{'es' if guesses_taken != 1 else ''}! "
                    f"The number was **{game['number']}**.\n\n"
                    f"**کوردی:** {message.author.mention} لە **{guesses_taken}** حەزردا بەدەستی هێنا! "
                    f"ژمارەکە **{game['number']}** بوو."
                ),
                color=discord.Color.green()
            )
            win_embed.set_footer(text="Type $luckylb to see the leaderboard | $luckylb بنووسە بۆ بینینی لیستی باشترینان")
            await message.channel.send(embed=win_embed)
        elif guess_val < game["number"]:
            await message.channel.send("📈 Higher! | بەرزتر!")
        else:
            await message.channel.send("📉 Lower! | نزمتر!")
        return

    if message.channel.id in tf_games and not message.content.startswith('$'):
        game = tf_games[message.channel.id]
        ans = message.content.strip().lower()
        accepted_true  = {"راست", "true", "t", "ڕاست"}
        accepted_false = {"چەوت", "false", "f", "چه‌وت"}
        user_pick = None
        if ans in accepted_true:
            user_pick = True
        elif ans in accepted_false:
            user_pick = False
        if user_pick is not None:
            task = game.get("task")
            if task:
                task.cancel()
            tf_games.pop(message.channel.id, None)
            is_correct = (user_pick == game["answer"])
            if is_correct:
                result_embed = discord.Embed(
                    title="🎉 وەڵامەکە راست بوو! / Correct!",
                    description=(
                        f"دەستخۆش {message.author.mention}! وەڵامی دروستت دا! 🏆\n"
                        f"Congrats {message.author.mention}! That was correct! 🏆\n\n"
                        f"✅ وەڵامی دروست: **{'راست ✅' if game['answer'] else 'چەوت ❌'}**\n"
                        f"✅ Correct answer: **{'TRUE ✅' if game['answer'] else 'FALSE ❌'}**"
                    ),
                    color=discord.Color.green()
                )
            else:
                result_embed = discord.Embed(
                    title="❌ وەڵامەکە هەڵە بوو! / Wrong!",
                    description=(
                        f"ببورە {message.author.mention}! وەڵامەکەت هەڵە بوو! 😔\n"
                        f"Sorry {message.author.mention}! That was wrong! 😔\n\n"
                        f"✅ وەڵامی دروست: **{'راست ✅' if game['answer'] else 'چەوت ❌'}**\n"
                        f"✅ Correct answer: **{'TRUE ✅' if game['answer'] else 'FALSE ❌'}**"
                    ),
                    color=discord.Color.red()
                )
            result_embed.add_field(
                name="📝 دوای جووابەکە / The Statement",
                value=f"*{game['statement_ku']}*\n*{game['statement_en']}*",
                inline=False
            )
            await message.channel.send(embed=result_embed)
        return

    if message.channel.id in active_quizzes and not message.content.startswith('$'):
        aq = active_quizzes[message.channel.id]
        user_ans = message.content.strip().lower()
        correct  = aq["answer"].lower()
        alts     = [a.lower() for a in aq.get("alternates", [])]
        if user_ans == correct or user_ans in alts:
            task = aq.get("task")
            if task:
                task.cancel()
            active_quizzes.pop(message.channel.id, None)
            win_embed = discord.Embed(
                title="🎉 وەڵامی دروست! / Correct Answer!",
                description=(
                    f"{message.author.mention} **وەڵامی دروستت دا! باشکاری! 🏆**\n"
                    f"{message.author.mention} **Correct answer! Well done! 🏆**\n\n"
                    f"✅ وەڵامەکە: **{aq['answer']}**\n"
                    f"✅ Answer: **{aq['answer']}**"
                ),
                color=discord.Color.green()
            )
            await message.channel.send(embed=win_embed)
        return

    if message.channel.id in anime_quizzes and not message.content.startswith('$'):
        quiz = anime_quizzes[message.channel.id]
        guess = message.content.strip().lower()
        answer = quiz["name"].lower()
        # Accept if guess contains the answer or vice versa (handles partial matches)
        if guess == answer or answer in guess or guess in answer:
            task = quiz.get("task")
            if task:
                task.cancel()
            anime_quizzes.pop(message.channel.id, None)
            win_embed = discord.Embed(
                title="🎉 وەڵامەکە راست بوو! / Correct Answer!",
                description=(
                    f"دەستخۆش {message.author.mention} 🎊\n"
                    f"ئەوە کارەکتەری **{quiz['name']}** ییە!\n\n"
                    f"**Congrats {message.author.mention}!**\n"
                    f"That's **{quiz['name']}**!\n\n"
                    f"💰 ٥ خاڵ جووچە سەر هەژمارەکەت لە داو سیستەمی خاڵەکان.\n"
                    f"💰 5 points added to your account in the points system."
                ),
                color=discord.Color.green()
            )
            win_embed.set_thumbnail(url=quiz["image_url"])
            await message.channel.send(embed=win_embed)
        return

    if message.channel.id in hangman_games and len(message.content.strip()) == 1 and message.content.strip().isalpha():
        game = hangman_games[message.channel.id]
        letter = message.content.strip().lower()
        if letter in game["guessed"] or letter in game["wrong"]:
            await message.channel.send(f"`{letter}` was already guessed. | `{letter}` پێشتر حەزری کرابوو.")
        elif letter in game["word"]:
            game["guessed"].add(letter)
            if all(c in game["guessed"] for c in game["word"]):
                await message.channel.send(
                    f"🎉 {message.author.mention} solved it! The word was **{game['word']}**.\n"
                    f"🎉 {message.author.mention} چارەسەری کرد! وشەکە **{game['word']}** بوو."
                )
                hangman_games.pop(message.channel.id, None)
            else:
                await message.channel.send(render_hangman(game))
        else:
            game["wrong"].add(letter)
            if len(game["wrong"]) >= 6:
                await message.channel.send(
                    f"💀 Game over! The word was **{game['word']}**.\n{HANGMAN_STAGES[-1]}\n"
                    f"💀 یاری تەواو بوو! وشەکە **{game['word']}** بوو."
                )
                hangman_games.pop(message.channel.id, None)
            else:
                await message.channel.send(render_hangman(game))
        return

    # --- GREENTEA GAME AUTO-GUESS ---
    if message.channel.id in greentea_games and not message.content.startswith('$'):
        content = message.content.strip().lower()
        if content.isalpha():
            session = greentea_games[message.channel.id]
            cid = message.channel.id
            if len(content) > 1:
                if content == session["word"]:
                    session["revealed"] = list(session["word"])
                    uid = message.author.id
                    pts = 5 + session["lives"] * 2
                    session["scores"][uid] = session["scores"].get(uid, 0) + pts
                    del greentea_games[cid]
                    if session["auto_end_task"]:
                        session["auto_end_task"].cancel()
                    await message.channel.send(embed=_gt_build_end_embed(session, won=True))
                else:
                    session["lives"] -= 1
                    session["wrong_letters"].add(f"[{content[:6]}]")
                    if session["lives"] <= 0:
                        del greentea_games[cid]
                        if session["auto_end_task"]:
                            session["auto_end_task"].cancel()
                        await message.channel.send(embed=_gt_build_end_embed(session, won=False))
                    else:
                        e = discord.Embed(
                            title=f"{_GT_WARN}  Wrong Word!  |  وشەی هەڵە!",
                            description=f"**EN:** `{content}` is not the word. -1 life.\n**KU:** `{content}` وشەکە نییە.",
                            color=discord.Color.red()
                        )
                        e.add_field(name=f"{_GT_LETTER} Current", value="```\n" + " ".join(session["revealed"]).upper() + "\n```")
                        await message.channel.send(embed=e)
                return
            letter = content
            if letter in session["guessed_letters"] or letter in session["wrong_letters"]:
                await message.channel.send(f"{_GT_WARN} Already guessed `{letter.upper()}`!", delete_after=4)
                return
            session["guessed_letters"].add(letter)
            session["last_guess_time"] = time.time()
            word = session["word"]
            if letter in word:
                positions = [i for i, ch in enumerate(word) if ch == letter]
                for i in positions:
                    session["revealed"][i] = letter
                uid = message.author.id
                pts = len(positions) * 10
                session["scores"][uid] = session["scores"].get(uid, 0) + pts
                session["guessers"].add(uid)
                if session["auto_end_task"]:
                    session["auto_end_task"].cancel()
                delay = 180 if session["mode"] == "normal" else (90 if session["mode"] == "hard" else 30)
                session["auto_end_task"] = asyncio.create_task(_gt_auto_end_game(cid, delay))
                if "_" not in session["revealed"]:
                    session["scores"][uid] = session["scores"].get(uid, 0) + 20
                    del greentea_games[cid]
                    session["auto_end_task"].cancel()
                    await message.channel.send(embed=_gt_build_end_embed(session, won=True))
                else:
                    e = discord.Embed(
                        title=f"{_GT_WIN} Letter Found! — `{letter.upper()}`  |  پیتەکە دۆزرایەوە!",
                        color=discord.Color.gold()
                    )
                    letter_word = "letter" if len(positions) == 1 else "letters"
                    e.title = f"{_GT_WIN} You got **{len(positions)} {letter_word}** right! — `{letter.upper()}`"
                    e.description = f"🍵 پیتی `{letter.upper()}` **{len(positions)}** جار دۆزرایەوە!"
                    e.add_field(name=f"{_GT_LETTER} Revealed  |  ئاشکراکراو", value="```\n" + " ".join(session["revealed"]).upper() + "\n```", inline=False)
                    e.add_field(name=f"{_GT_ZAP} Points  |  خاڵ", value=f"+{pts} pts  —  {_GT_STAR * min(pts // 10, 5)}", inline=True)
                    await message.channel.send(embed=e)
            else:
                session["wrong_letters"].add(letter)
                session["lives"] -= 1
                if session["lives"] <= 0:
                    del greentea_games[cid]
                    if session["auto_end_task"]:
                        session["auto_end_task"].cancel()
                    await message.channel.send(embed=_gt_build_end_embed(session, won=False))
                else:
                    e = discord.Embed(
                        title=f"{_GT_WARN}  Wrong Letter!  |  پیتی هەڵە!",
                        description=f"**EN:** `{letter.upper()}` not in word. -1 life. `{session['lives']}` left.\n**KU:** `{letter.upper()}` نییە. ژیانی ماوە: `{session['lives']}`",
                        color=discord.Color.dark_red()
                    )
                    e.add_field(name=f"{_GT_LETTER} Current", value="```\n" + " ".join(session["revealed"]).upper() + "\n```")
                    hearts_full = "🟩" * session["lives"]
                    hearts_empty = "🟥" * (session["max_lives"] - session["lives"])
                    e.add_field(name="❤️ Lives", value=hearts_full + hearts_empty, inline=False)
                    await message.channel.send(embed=e)
            return

    # --- PASSIVE XP ---
    if message.guild is not None and not message.content.startswith(bot.command_prefix):
        key = (message.guild.id, message.author.id)
        now = time.time()
        last = message_cooldowns.get(key, 0)
        if now - last >= MESSAGE_XP_COOLDOWN:
            gained = random.randint(MESSAGE_XP_MIN, MESSAGE_XP_MAX)
            entry = get_user_entry(message.guild.id, message.author.id)
            before_total = total_xp(entry)
            before_level = level_from_xp(before_total)
            add_xp(message.guild.id, message.author.id, gained, "message_xp")
            after_level = level_from_xp(total_xp(entry))
            message_cooldowns[key] = now
            if after_level > before_level:
                await announce_level_up(message.author, message.channel, after_level, "message")

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return
    key = (member.guild.id, member.id)
    if before.channel is None and after.channel is not None:
        voice_sessions[key] = time.time()
    elif before.channel is not None and after.channel is None:
        voice_sessions.pop(key, None)

# --- BACKGROUND TASKS ---

@tasks.loop(minutes=1)
async def voice_xp_tick():
    now = time.time()
    for guild in bot.guilds:
        for vc in guild.voice_channels:
            human_members = [m for m in vc.members if not m.bot]
            if len(human_members) < 2:
                continue
            for member in human_members:
                state = member.voice
                if state is None:
                    continue
                if state.self_mute or state.self_deaf or state.mute or state.deaf:
                    continue
                key = (guild.id, member.id)
                start = voice_sessions.get(key, now)
                elapsed = now - start
                if elapsed >= 60:
                    minutes = int(elapsed // 60)
                    entry = get_user_entry(guild.id, member.id)
                    before_lvl = level_from_xp(total_xp(entry))
                    add_xp(guild.id, member.id, minutes * VOICE_XP_PER_MINUTE, "voice_xp")
                    after_lvl = level_from_xp(total_xp(entry))
                    voice_sessions[key] = start + minutes * 60
                    if after_lvl > before_lvl:
                        await announce_level_up(member, vc, after_lvl, "voice")

@tasks.loop(minutes=2)
async def autosave():
    save_xp()
    save_warnings()
    save_econ()
    save_level_channels()
    save_welcome_channels()
    save_ticket_settings()
    save_open_tickets()
    save_afk()

# --- BASIC COMMANDS ---

@bot.command()
async def hello(ctx):
    await ctx.send(f"Hello {ctx.author.mention}! | سڵاو {ctx.author.mention}!")

@bot.command()
async def assign(ctx):
    role = discord.utils.get(ctx.guild.roles, name=secret_role)
    if role:
        await ctx.author.add_roles(role)
        await ctx.send(f"{ctx.author.mention} is now assigned to {secret_role} | ئێستا {ctx.author.mention} بەرپرسی {secret_role} یە")
    else:
        await ctx.send("Role doesn't exist | ئەم رۆڵە بوونی نییە")

@bot.command()
async def remove(ctx):
    role = discord.utils.get(ctx.guild.roles, name=secret_role)
    if role:
        await ctx.author.remove_roles(role)
        await ctx.send(f"{ctx.author.mention} has had the {secret_role} removed | رۆڵی {secret_role} لە {ctx.author.mention} لاوە")
    else:
        await ctx.send("Role doesn't exist | ئەم رۆڵە بوونی نییە")

@bot.command()
async def dm(ctx, *, msg):
    await ctx.author.send(f"You said: {msg} | تۆ گوتت: {msg}")

@bot.command()
async def reply(ctx):
    await ctx.reply("This is a reply to your message! | ئەمە وەڵامێکە بۆ پەیامەکەت!")

@bot.command()
async def poll(ctx, *, question):
    embed = discord.Embed(title="New Poll | دەنگدانی نوێ", description=question)
    poll_message = await ctx.send(embed=embed)
    await poll_message.add_reaction("👍")
    await poll_message.add_reaction("👎")

@bot.command()
async def serverinfo(ctx):
    guild = ctx.guild
    if guild is None:
        await ctx.send("This command can only be used in a server. | ئەم فەرمانە تەنها لە سێرڤەر دەکرێت بەکارهێنرێت.")
        return

    text_ch = len(guild.text_channels)
    voice_ch = len(guild.voice_channels)
    bots = sum(1 for m in guild.members if m.bot)
    humans = (guild.member_count or 0) - bots
    online = sum(1 for m in guild.members if m.status != discord.Status.offline and not m.bot)
    boost_level = guild.premium_tier
    boost_count = guild.premium_subscription_count or 0

    embed = discord.Embed(
        title=f"🏰 {guild.name}",
        description=f"**ID:** `{guild.id}`",
        color=discord.Color.from_rgb(88, 101, 242),
        timestamp=datetime.datetime.utcnow(),
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    if guild.banner:
        embed.set_image(url=guild.banner.with_format("png").url)
    embed.add_field(name="👑 Owner | خاوەن", value=f"<@{guild.owner_id}>", inline=True)
    embed.add_field(name="📅 Created | دروستکراوە", value=f"<t:{int(guild.created_at.timestamp())}:D>", inline=True)
    embed.add_field(name="🌍 Locale | زمان", value=str(guild.preferred_locale), inline=True)
    embed.add_field(name="👥 Members | ئەندامان", value=f"👤 {humans:,} humans\n🤖 {bots} bots", inline=True)
    embed.add_field(name="🟢 Online | ئۆنلاین", value=f"`{online:,}`", inline=True)
    embed.add_field(name="🔐 Verification | پشکنین", value=str(guild.verification_level).title(), inline=True)
    embed.add_field(name="💬 Channels | کەناڵەکان", value=f"📝 {text_ch} text\n🔊 {voice_ch} voice\n📁 {len(guild.categories)} categories", inline=True)
    embed.add_field(name="🎭 Roles | رۆڵەکان", value=f"`{len(guild.roles):,}`", inline=True)
    embed.add_field(name="💎 Boosts | بووستەکان", value=f"Level {boost_level} · {boost_count} boosts", inline=True)
    embed.set_footer(text=f"Requested by {ctx.author.display_name}")
    await ctx.send(embed=embed)

@bot.command()
async def userinfo(ctx, member: discord.Member = None):
    member = member or ctx.author
    roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
    top_role = member.top_role if member.top_role.name != "@everyone" else None
    color = top_role.color if top_role and top_role.color.value != 0 else discord.Color.from_rgb(88, 101, 242)
    status_emojis = {
        discord.Status.online: "🟢", discord.Status.idle: "🟡",
        discord.Status.dnd: "🔴", discord.Status.offline: "⚫",
    }
    status_icon = status_emojis.get(member.status, "⚫")
    all_members = sorted(ctx.guild.members, key=lambda m: m.joined_at or datetime.datetime.utcnow())
    join_pos = next((i + 1 for i, m in enumerate(all_members) if m.id == member.id), "?")
    embed = discord.Embed(
        title=f"{status_icon} {member.display_name}",
        description=f"{'🤖 Bot Account | بۆت' if member.bot else ''}\n**Tag:** `{member}`\n**ID:** `{member.id}`",
        color=color,
        timestamp=datetime.datetime.utcnow(),
    )
    if member.display_avatar:
        embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="📅 Joined Server | پەیوەندی بە سێرڤەر", value=f"<t:{int(member.joined_at.timestamp())}:D>" if member.joined_at else "Unknown", inline=True)
    embed.add_field(name="📆 Account Created | ئەکاونت دروستکراوە", value=f"<t:{int(member.created_at.timestamp())}:D>", inline=True)
    embed.add_field(name="📊 Join Position | جێگای پەیوەستبوون", value=f"`#{join_pos}`", inline=True)
    if top_role:
        embed.add_field(name="⭐ Top Role | باڵاترین رۆڵ", value=top_role.mention, inline=True)
    embed.add_field(name="🎮 Status | دۆخ", value=str(member.status).title(), inline=True)
    embed.add_field(name=f"🎭 Roles | رۆڵەکان ({len(roles)})", value=" ".join(roles[:10]) + ("…" if len(roles) > 10 else "") if roles else "None | هیچ", inline=False)
    embed.set_footer(text=f"Requested by {ctx.author.display_name}")
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(manage_messages=True)
async def clear(ctx, amount: int):
    if amount < 1 or amount > 100:
        await ctx.send("Please choose a number between 1 and 100. | تکایە ژمارەیەک لە نێوان ١ و ١٠٠ هەڵبژێرە.")
        return
    deleted = await ctx.channel.purge(limit=amount + 1)
    confirm = await ctx.send(f"Deleted {len(deleted) - 1} message(s). | {len(deleted) - 1} پەیام سڕایەوە.")
    await confirm.delete(delay=3)

@clear.error
async def clear_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need the Manage Messages permission to use this. | مووچەی بەڕێوەبردنی پەیامەکان پێویستە.")
    elif isinstance(error, commands.BadArgument) or isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Usage: $clear <number between 1 and 100> | بەکارهێنان: $clear <ژمارە لە نێوان ١ و ١٠٠>")

# --- LEVELING COMMANDS ---

@bot.command()
async def rank(ctx, member: discord.Member = None):
    if ctx.guild is None:
        await ctx.send("This command can only be used in a server. | ئەم فەرمانە تەنها لە سێرڤەر دەکرێت بەکارهێنرێت.")
        return
    member = member or ctx.author
    entry = get_user_entry(ctx.guild.id, member.id)
    msg_xp = entry.get("message_xp", 0)
    voice_xp_val = entry.get("voice_xp", 0)
    total = msg_xp + voice_xp_val
    lvl = level_from_xp(total)
    cur_floor = xp_for_level(lvl)
    next_floor = xp_for_level(lvl + 1)
    progress = total - cur_floor
    needed = next_floor - cur_floor
    pct = (progress / needed * 100) if needed else 0
    bar = make_progress_bar(progress, needed, length=20)

    guild_data = xp_data.get(str(ctx.guild.id), {})
    sorted_users = sorted(
        guild_data.items(),
        key=lambda kv: kv[1].get("message_xp", 0) + kv[1].get("voice_xp", 0),
        reverse=True,
    )
    rank_pos = next((i + 1 for i, (uid, _) in enumerate(sorted_users) if uid == str(member.id)), len(sorted_users) + 1)

    icon = level_icon(lvl)
    color = discord.Color.from_hsv((min(lvl, 50) / 50) * 0.83, 0.90, 1.0)
    embed = discord.Embed(color=color, timestamp=datetime.datetime.utcnow())
    embed.set_author(
        name=f"{member.display_name} — Rank Card | کارتی رتبە",
        icon_url=member.display_avatar.url if member.display_avatar else None
    )
    if member.display_avatar:
        embed.set_thumbnail(url=member.display_avatar.url)
    embed.description = (
        f"## {icon} Level | ئاست  **{lvl}**  ·  🏅 Rank | رتبە  **#{rank_pos}**\n"
        f"```\n{bar}  {pct:.1f}%\n```\n"
        f"**{progress:,}** / **{needed:,}** XP  →  Level | ئاستی **{lvl + 1}**"
    )
    embed.add_field(name="💬 Message XP | پەیام", value=f"```{msg_xp:,}```", inline=True)
    embed.add_field(name="🎙️ Voice XP | دەنگ", value=f"```{voice_xp_val:,}```", inline=True)
    embed.add_field(name="✨ Total XP | کۆی گشتی", value=f"```{total:,}```", inline=True)
    embed.set_footer(text="Keep chatting & chilling in voice! | بەردەوام بە لە پەیام و دەنگدا!")
    await ctx.send(embed=embed)

@bot.command(name="toplevel", aliases=["levels", "topleveler", "topup"])
async def toplevel(ctx):
    if ctx.guild is None:
        await ctx.send("This command can only be used in a server. | ئەم فەرمانە تەنها لە سێرڤەر دەکرێت بەکارهێنرێت.")
        return
    guild_data = xp_data.get(str(ctx.guild.id), {})
    rows = []
    for uid, entry in guild_data.items():
        total = entry.get("message_xp", 0) + entry.get("voice_xp", 0)
        if total <= 0:
            continue
        rows.append((uid, total, level_from_xp(total)))
    rows.sort(key=lambda r: (r[2], r[1]), reverse=True)
    rows = rows[:10]
    if not rows:
        await ctx.send("No XP has been earned in this server yet. | هێشتا هیچ XP لەم سێرڤەرە کەسب نەکراوە.")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (uid, total, lvl) in enumerate(rows):
        m = ctx.guild.get_member(int(uid))
        name = m.display_name if m else f"User {uid}"
        cur_floor = xp_for_level(lvl)
        next_floor = xp_for_level(lvl + 1)
        bar = make_progress_bar(total - cur_floor, next_floor - cur_floor, length=12)
        prefix = medals[i] if i < 3 else f"`#{i+1}`"
        icon = level_icon(lvl)
        lines.append(f"{prefix} **{name}** — {icon} Lvl **{lvl}** · `{total:,}` XP\n> `{bar}`")
    embed = discord.Embed(
        title="🏅 Top Level Climbers | باڵاترین ئاستگرتووان",
        description="\n\n".join(lines),
        color=discord.Color.gold(),
        timestamp=datetime.datetime.utcnow(),
    )
    embed.set_footer(text=f"Top 10 of {ctx.guild.name} | بەرزترین ١٠ی {ctx.guild.name}")
    await ctx.send(embed=embed)

@bot.command(name="setlevelchannel", aliases=["levelchannel"])
@commands.has_permissions(manage_guild=True)
async def setlevelchannel(ctx, channel: discord.TextChannel = None):
    if ctx.guild is None:
        await ctx.send("Server only. | تەنها لە سێرڤەر.")
        return
    target = channel or ctx.channel
    level_channels[str(ctx.guild.id)] = target.id
    save_level_channels()
    await ctx.send(f"✅ Level-up announcements will now be sent in {target.mention}. | ئاگاداریەکانی بەرزبوونی ئاست دێنرێن بۆ {target.mention}.")

@bot.command(name="removelevelchannel", aliases=["unsetlevelchannel"])
@commands.has_permissions(manage_guild=True)
async def removelevelchannel(ctx):
    if ctx.guild is None:
        await ctx.send("Server only. | تەنها لە سێرڤەر.")
        return
    if str(ctx.guild.id) in level_channels:
        del level_channels[str(ctx.guild.id)]
        save_level_channels()
        await ctx.send("✅ Level-up announcements will fall back to the channel where the level happened. | ئاگاداریەکانی بەرزبوونی ئاست دەگەڕێنەوە بۆ کەناڵی ئەوجا.")
    else:
        await ctx.send("No level channel was set. | هیچ کەناڵی ئاست دانەنرابوو.")

@bot.command(name="setwelcomeembed", aliases=["welcomechannel", "setwelcome"])
@commands.has_permissions(manage_guild=True)
async def setwelcomeembed(ctx, channel: discord.TextChannel = None):
    """Set the channel where welcome embeds are sent when a new member joins."""
    if ctx.guild is None:
        await ctx.send("Server only. | تەنها لە سێرڤەر.")
        return
    target = channel or ctx.channel
    welcome_channels[str(ctx.guild.id)] = target.id
    save_welcome_channels()
    embed = discord.Embed(
        color=0xFFD700,
        title="✅ کەناڵی بەخێربێیت دانراو! | Welcome Channel Set!",
        description=(
            f"کاتێک ئەندامێکی نوێ بەشداری دەکات، پەیامی بەخێربێیت دەنێردرێت بۆ {target.mention}.\n\n"
            f"🇬🇧 Welcome embeds will now be sent to {target.mention} whenever a new member joins."
        ),
    )
    embed.set_footer(text=f"Set by {ctx.author.display_name} | دانراوە لەلایەن {ctx.author.display_name}")
    await ctx.send(embed=embed)

@bot.command(name="removewelcomeembed", aliases=["unsetwelcome"])
@commands.has_permissions(manage_guild=True)
async def removewelcomeembed(ctx):
    """Remove the welcome embed channel setting."""
    if ctx.guild is None:
        await ctx.send("Server only. | تەنها لە سێرڤەر.")
        return
    if str(ctx.guild.id) in welcome_channels:
        del welcome_channels[str(ctx.guild.id)]
        save_welcome_channels()
        await ctx.send("✅ Welcome channel removed. | کەناڵی بەخێربێیت لابرا.")
    else:
        await ctx.send("No welcome channel was set. | هیچ کەناڵی بەخێربێیت دانەنرابوو.")

@bot.command(aliases=["leaderboard", "lb"])
async def top(ctx, category: str = "total"):
    if ctx.guild is None:
        await ctx.send("This command can only be used in a server. | ئەم فەرمانە تەنها لە سێرڤەر دەکرێت بەکارهێنرێت.")
        return

    category = category.lower()
    if category not in ("total", "message", "messages", "text", "voice"):
        await ctx.send("Usage: `$top [total|message|voice]` | بەکارهێنان: `$top [total|message|voice]`")
        return

    if category in ("message", "messages", "text"):
        key_fn = lambda kv: kv[1].get("message_xp", 0)
        title = "💬 Top Players — Message XP | باڵاترین یاریزانان - XP پەیام"
        color = discord.Color.blue()
    elif category == "voice":
        key_fn = lambda kv: kv[1].get("voice_xp", 0)
        title = "🎙️ Top Players — Voice XP | باڵاترین یاریزانان - XP دەنگ"
        color = discord.Color.purple()
    else:
        key_fn = lambda kv: kv[1].get("message_xp", 0) + kv[1].get("voice_xp", 0)
        title = "🏆 Top Players — Total XP | باڵاترین یاریزانان - کۆی گشتی XP"
        color = discord.Color.gold()

    guild_data = xp_data.get(str(ctx.guild.id), {})
    sorted_users = sorted(guild_data.items(), key=key_fn, reverse=True)
    sorted_users = [u for u in sorted_users if key_fn(u) > 0][:10]

    if not sorted_users:
        await ctx.send("No XP has been earned in this server yet. | هێشتا هیچ XP لەم سێرڤەرە کەسب نەکراوە.")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (uid, entry) in enumerate(sorted_users):
        member = ctx.guild.get_member(int(uid))
        name = member.display_name if member else f"User {uid}"
        msg_xp = entry.get("message_xp", 0)
        voice_xp_val = entry.get("voice_xp", 0)
        total = msg_xp + voice_xp_val
        lvl = level_from_xp(total)
        prefix = medals[i] if i < 3 else f"`#{i + 1}`"
        icon = level_icon(lvl)
        lines.append(
            f"{prefix} **{name}** — {icon} Lvl **{lvl}** · `{total:,}` XP\n"
            f"> 💬 `{msg_xp:,}` · 🎙️ `{voice_xp_val:,}`"
        )
    embed = discord.Embed(
        title=title,
        description="\n\n".join(lines),
        color=color,
        timestamp=datetime.datetime.utcnow(),
    )
    embed.set_footer(text=f"$top total | message | voice  •  {ctx.guild.name}")
    await ctx.send(embed=embed)

# --- MODERATION COMMANDS ---

@bot.command()
async def ping(ctx):
    latency_ms = round(bot.latency * 1000)
    if latency_ms < 100:
        color, bar = 0x57f287, "🟩🟩🟩🟩🟩"
    elif latency_ms < 200:
        color, bar = 0xfee75c, "🟨🟨🟨⬛⬛"
    else:
        color, bar = 0xed4245, "🟥🟥⬛⬛⬛"
    embed = discord.Embed(
        title="🏓 Pong!",
        description=(
            "### ⚡ Bot Latency | خێرایی بۆت\n"
            f"`{latency_ms}ms` {bar}"
        ),
        color=color,
    )
    embed.set_footer(text="discord.py • WebSocket latency | خێرایی WebSocket")
    await ctx.send(embed=embed)

@bot.command(aliases=["av", "pfp", "icon"])
async def avatar(ctx, member: discord.Member = None):
    member = member or ctx.author
    embed = discord.Embed(
        title=f"{member.display_name}'s Avatar | ئاڤاتاری {member.display_name}",
        color=discord.Color.blurple(),
    )
    embed.set_image(url=member.display_avatar.url)
    embed.set_footer(text=f"ID: {member.id}")
    await ctx.send(embed=embed)


async def _animequiz_runner(channel, char_name, image_url, msg):
    """Count down 15s updating footer only — name stays hidden until guessed."""
    try:
        for secs_left in range(15, 0, -1):
            if channel.id not in anime_quizzes:
                return
            countdown_embed = discord.Embed(
                title="🤔 کێ ئەم کارەکتەرە دەناسیتەوە؟ / Who is this character?",
                description=(
                    "خێراترین کەس داوی کارەکتەرمکە بنووسێت!\n"
                    "Be the first to type the character's name in chat!"
                ),
                color=discord.Color.from_rgb(114, 137, 218)
            )
            countdown_embed.set_image(url=image_url)
            countdown_embed.set_footer(
                text=f"⏳ {secs_left} چرکەت لەبەردەستە... / {secs_left} seconds remaining..."
            )
            try:
                await msg.edit(embed=countdown_embed)
            except Exception:
                pass
            await asyncio.sleep(1)

        quiz = anime_quizzes.pop(channel.id, None)
        if quiz:
            timeout_embed = discord.Embed(
                title="⏰ ماوەکەت تەواو بوو! / Time's Up!",
                description=(
                    f"کەس وەڵامی دروست نەدا! 😔\n"
                    f"ناوی ڕاستەقینەی کارەکتەرەکە: **{char_name}** بوو!\n\n"
                    f"Nobody got it right! 😔\n"
                    f"The character was **{char_name}**!"
                ),
                color=discord.Color.red()
            )
            timeout_embed.set_image(url=image_url)
            timeout_embed.set_footer(text="Type $animequiz to play again! / $animequiz بنووسە بۆ دووبارە یاریکردن!")
            try:
                await msg.edit(embed=timeout_embed)
            except Exception:
                await channel.send(embed=timeout_embed)
    except asyncio.CancelledError:
        pass

QUIZ_QUESTIONS = [
    {
        "question": "🇮🇶 پایتەختی عێراق چییە؟\n🇬🇧 What is the capital of Iraq?",
        "answer": "بەغداد",
        "alternates": ["baghdad", "بغداد"],
    },
    {
        "question": "🇮🇶 چەند ئەنگووشتی یەک دەست هەیە؟\n🇬🇧 How many fingers are on one hand?",
        "answer": "٥",
        "alternates": ["5", "پێنج", "five"],
    },
    {
        "question": "🇮🇶 خێرایی ڕووناکی چەندە (km/s)?\n🇬🇧 What is the speed of light (km/s)?",
        "answer": "300000",
        "alternates": ["٣٠٠٠٠٠", "300,000"],
    },
    {
        "question": "🇮🇶 گەورەترین ئەوقیانووسی جیهان چییە؟\n🇬🇧 What is the largest ocean in the world?",
        "answer": "ئۆقیانووسی ئاسیا",
        "alternates": ["pacific", "پاسیفیک", "ئاسیا"],
    },
    {
        "question": "🇮🇶 ١٢ × ١٢ چەندە؟\n🇬🇧 What is 12 × 12?",
        "answer": "144",
        "alternates": ["١٤٤"],
    },
    {
        "question": "🇮🇶 زمانی رەسمی کوردستان چییە؟\n🇬🇧 What is the official language of Kurdistan?",
        "answer": "کوردی",
        "alternates": ["kurdish", "kurdî"],
    },
    {
        "question": "🇮🇶 خۆر ستێرەیە یان گەیارەیە؟\n🇬🇧 Is the Sun a star or a planet?",
        "answer": "ستێرە",
        "alternates": ["star", "ستاره"],
    },
    {
        "question": "🇮🇶 چەند مانگی ساڵەکە هەیە؟\n🇬🇧 How many months are in a year?",
        "answer": "١٢",
        "alternates": ["12", "دوازده", "twelve"],
    },
    {
        "question": "🇮🇶 گەورەترین وڵاتی جیهان کامەیە؟\n🇬🇧 What is the largest country in the world?",
        "answer": "ڕووسیا",
        "alternates": ["russia", "روسیا"],
    },
    {
        "question": "🇮🇶 ئاوی کوێ ئاوی ژیانی زۆرترین ئاژەڵی دەریاییە؟\n🇬🇧 What is the largest animal in the ocean?",
        "answer": "نەهەنگی شین",
        "alternates": ["blue whale", "whale", "نهنگ"],
    },
]

async def _quiz_timeout(channel, q):
    """Auto-reveal answer after 2 minutes if nobody answers."""
    try:
        await asyncio.sleep(120)
        quiz = active_quizzes.pop(channel.id, None)
        if quiz:
            timeout_embed = discord.Embed(
                title="⌛ کات تەواو بوو! / Time's Up!",
                description=(
                    f"کەس وەڵامی دروست نەدا! 😔\n"
                    f"وەڵامی دروستەکە: **{q['answer']}**\n\n"
                    f"Nobody answered correctly! 😔\n"
                    f"The correct answer was: **{q['answer']}**"
                ),
                color=discord.Color.red()
            )
            await channel.send(embed=timeout_embed)
    except asyncio.CancelledError:
        pass

TF_STATEMENTS = [
    {
        "statement_ku": "دیواری گەورەی چین لە سەر مانگاوە بە چاوی ئاسایی دەبینرێت.",
        "statement_en": "The Great Wall of China can be seen from the moon with the naked eye.",
        "answer": False,
    },
    {
        "statement_ku": "ئاژەڵی هەیکەلی دەریایی (Seahorse) نێرەکەی دەژیێ نەک مێکەی.",
        "statement_en": "Male seahorses carry and give birth to babies, not females.",
        "answer": True,
    },
    {
        "statement_ku": "خۆر گەورەتر لە زەویە بە مامناوەند نزیک ١٠٩ جار.",
        "statement_en": "The Sun is about 109 times wider than Earth.",
        "answer": True,
    },
    {
        "statement_ku": "مرۆڤەکان تەنها ١٠٪ لە مێشکەکەیان بەکاردێنن.",
        "statement_en": "Humans only use 10% of their brains.",
        "answer": False,
    },
    {
        "statement_ku": "ئۆکتۆپوس سێ دل هەیە.",
        "statement_en": "An octopus has three hearts.",
        "answer": True,
    },
    {
        "statement_ku": "ئاو بەخۆی خۆی ئاگر دەگرێت.",
        "statement_en": "Water can catch fire on its own.",
        "answer": False,
    },
    {
        "statement_ku": "مانگا هێزی گرانجەذبەی خۆی هەیە.",
        "statement_en": "The Moon has its own gravitational pull.",
        "answer": True,
    },
    {
        "statement_ku": "فیل تەنها ئاژەڵێکە کە نەتوانیت باز بدات.",
        "statement_en": "Elephants are the only animals that cannot jump.",
        "answer": True,
    },
    {
        "statement_ku": "باڵندەکانی پینگوێن بازدەدەن.",
        "statement_en": "Penguins can fly.",
        "answer": False,
    },
    {
        "statement_ku": "خوێن لە مرۆڤدا شینە نەک سوور.",
        "statement_en": "Human blood is blue inside the body.",
        "answer": False,
    },
    {
        "statement_ku": "لازمانی کەلەچووی (Fingerprints) ناو دوو کەسدا یەکسان نییە.",
        "statement_en": "No two people have the same fingerprints.",
        "answer": True,
    },
    {
        "statement_ku": "ئاوی دەریا شیرین دەچێت.",
        "statement_en": "Ocean water is drinkable without treatment.",
        "answer": False,
    },
    {
        "statement_ku": "دۆشکەی کورمانجی (Bats) کۆرن.",
        "statement_en": "Bats are blind.",
        "answer": False,
    },
    {
        "statement_ku": "زەوی ستێرەیەکی بچووکە.",
        "statement_en": "Earth is a small star.",
        "answer": False,
    },
    {
        "statement_ku": "ئاژەڵی مار گوێی دەرەکی نییە.",
        "statement_en": "Snakes have no external ears.",
        "answer": True,
    },
]

async def _tf_timeout(channel, game):
    """Reveal answer after 60 seconds if nobody answers."""
    try:
        for secs_left in range(60, 0, -5):
            if channel.id not in tf_games:
                return
            try:
                countdown_embed = discord.Embed(
                    title="❓ راست یان چەوت؟ / True or False?",
                    description=(
                        f"کلژاکەم زانیاریەی خوارەوە ڕاستە یان چەوت؟\n"
                        f"Is the following statement true or false?\n\n"
                        f"🔊 **{game['statement_ku']}**\n"
                        f"🔊 **{game['statement_en']}**\n\n"
                        f"تەنها بنووسە: **راست** یان **چەوت**\n"
                        f"Just type: **True** or **False**"
                    ),
                    color=discord.Color.from_rgb(114, 137, 218)
                )
                countdown_embed.set_footer(
                    text=f"⏳ {secs_left} چرکەت لەبەردەستە... / {secs_left} seconds remaining..."
                )
                await game["msg"].edit(embed=countdown_embed)
            except Exception:
                pass
            await asyncio.sleep(5)

        tf_games.pop(channel.id, None)
        correct_label = "راست ✅" if game["answer"] else "چەوت ❌"
        correct_en     = "TRUE ✅"  if game["answer"] else "FALSE ❌"
        timeout_embed = discord.Embed(
            title="⏰ کات تەواو بوو! / Time's Up!",
            description=(
                f"کەس وەڵامی دروست نەدا! 😔\n"
                f"Nobody answered! 😔\n\n"
                f"✅ وەڵامی دروست: **{correct_label}**\n"
                f"✅ Correct answer: **{correct_en}**\n\n"
                f"📝 **{game['statement_ku']}**\n"
                f"📝 **{game['statement_en']}**"
            ),
            color=discord.Color.red()
        )
        timeout_embed.set_footer(text="Type $tf to play again! / $tf بنووسە بۆ دووبارە یاریکردن!")
        try:
            await game["msg"].edit(embed=timeout_embed)
        except Exception:
            await channel.send(embed=timeout_embed)
    except asyncio.CancelledError:
        pass

@bot.command(name="tf", aliases=["truefalse", "truorfalse"])
async def tf(ctx):
    if ctx.channel.id in tf_games:
        await ctx.send(
            "⚠️ یاری راست/چەوت بەردەوامە! وەڵامت بنووسە یان چاوەڕێ بکە.\n"
            "⚠️ A True/False game is already running! Answer or wait for it to end.",
            delete_after=8
        )
        return

    game_data = random.choice(TF_STATEMENTS)
    embed = discord.Embed(
        title="❓ راست یان چەوت؟ / True or False?",
        description=(
            f"کلژاکەم زانیاریەی خوارەوە ڕاستە یان چەوت؟\n"
            f"Is the following statement true or false?\n\n"
            f"🔊 **{game_data['statement_ku']}**\n"
            f"🔊 **{game_data['statement_en']}**\n\n"
            f"تەنها بنووسە: **راست** یان **چەوت**\n"
            f"Just type: **True** or **False**"
        ),
        color=discord.Color.from_rgb(114, 137, 218)
    )
    embed.set_footer(text="⏳ ٦٠ چرکەت لەبەردەستە... / 60 seconds remaining...")
    msg = await ctx.send(embed=embed)
    game_data = {**game_data, "msg": msg}
    task = asyncio.get_event_loop().create_task(_tf_timeout(ctx.channel, game_data))
    game_data["task"] = task
    tf_games[ctx.channel.id] = game_data

@bot.command(name="animequiz", aliases=["aq", "aquiz", "animeguess"])
async def animequiz(ctx):
    if ctx.channel.id in anime_quizzes:
        await ctx.send(
            "🎌 یاری ئەنیمەکویز بەردەوامە ئێرە! وەڵامت بنووسە یان چاوەڕێی کۆتایی پێهێنانی خۆکار بکە.\n"
            "🎌 An anime quiz is already running here! Type your answer or wait for it to end.",
            delete_after=8
        )
        return

    loading_embed = discord.Embed(
        title="🎌 ئەنیمەکویز / Anime Quiz",
        description="⏳ کارەکتەرەکە بارکردن... / Loading character...",
        color=discord.Color.blurple()
    )
    msg = await ctx.send(embed=loading_embed)

    char_name = None
    image_url = None
    try:
        async with aiohttp.ClientSession() as session:
            for _ in range(5):
                async with session.get(
                    "https://api.jikan.moe/v4/random/characters",
                    timeout=aiohttp.ClientTimeout(total=8)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        c    = data.get("data", {})
                        name = c.get("name", "")
                        imgs = c.get("images", {})
                        img  = (imgs.get("jpg",  {}).get("image_url") or
                                imgs.get("webp", {}).get("image_url") or "")
                        if name and img:
                            char_name = name
                            image_url = img
                            break
                await asyncio.sleep(0.4)
    except Exception:
        pass

    if not char_name or not image_url:
        err_embed = discord.Embed(
            title="❌ هەڵە / Error",
            description=(
                "نەتوانرا کارەکتەرێک بارکرێت، تکایە دوبارە هەوڵبدەرەوە.\n"
                "Could not load a character, please try again."
            ),
            color=discord.Color.red()
        )
        await msg.edit(embed=err_embed)
        return

    task = asyncio.get_event_loop().create_task(
        _animequiz_runner(ctx.channel, char_name, image_url, msg)
    )
    anime_quizzes[ctx.channel.id] = {
        "name": char_name,
        "image_url": image_url,
        "task": task,
        "msg": msg,
    }

@bot.command()
async def botinfo(ctx):
    total_members = sum(g.member_count or 0 for g in bot.guilds)
    uptime_str = fmt_uptime(int(time.time() - bot_start_time))
    embed = discord.Embed(
        title=f"⚙️ {bot.user.name}",
        description=(
            "A bilingual Kurdish | English Discord bot with XP, economy, games & more!\n"
            "بۆتی دوو زمانی کوردی | ئینگلیزی بۆ Discord بە XP، ئابووری، یاریەکان و زیاتر!"
        ),
        color=discord.Color.from_rgb(88, 101, 242),
        timestamp=datetime.datetime.utcnow(),
    )
    if bot.user.display_avatar:
        embed.set_thumbnail(url=bot.user.display_avatar.url)
    embed.add_field(name="🌐 Servers | سێرڤەرەکان", value=f"`{len(bot.guilds):,}`", inline=True)
    embed.add_field(name="👥 Total Members | کۆی ئەندامان", value=f"`{total_members:,}`", inline=True)
    embed.add_field(name="📡 Latency | خێرایی", value=f"`{round(bot.latency * 1000)}ms`", inline=True)
    embed.add_field(name="⏱️ Uptime | ماوەی چالاکی", value=f"`{uptime_str}`", inline=True)
    embed.add_field(name="📦 Library | کتێبخانە", value=f"`discord.py {discord.__version__}`", inline=True)
    embed.add_field(name="🔧 Prefix | پێشگر", value=f"`{bot.command_prefix}`", inline=True)
    embed.add_field(name="📜 Commands | فەرمانەکان", value=f"`{len(bot.commands):,}`", inline=True)
    embed.set_footer(text=f"Requested by {ctx.author.display_name}")
    await ctx.send(embed=embed)

@bot.command()
async def channelinfo(ctx, channel: discord.TextChannel = None):
    channel = channel or ctx.channel
    embed = discord.Embed(title=f"#{channel.name} - Channel Info | زانیاری کەناڵ")
    embed.add_field(name="ID", value=str(channel.id), inline=True)
    embed.add_field(name="Type | جۆر", value=str(channel.type), inline=True)
    embed.add_field(name="Created | دروستکراوە", value=channel.created_at.strftime("%B %d, %Y"), inline=True)
    if getattr(channel, "topic", None):
        embed.add_field(name="Topic | بابەت", value=channel.topic, inline=False)
    if getattr(channel, "category", None):
        embed.add_field(name="Category | پۆل", value=channel.category.name, inline=True)
    await ctx.send(embed=embed)

@bot.command(name="roles")
async def roles_cmd(ctx):
    if ctx.guild is None:
        await ctx.send("This command can only be used in a server. | ئەم فەرمانە تەنها لە سێرڤەر دەکرێت بەکارهێنرێت.")
        return
    role_list = [r.mention for r in reversed(ctx.guild.roles) if r.name != "@everyone"]
    if not role_list:
        await ctx.send("This server has no roles. | ئەم سێرڤەرە هیچ رۆڵێکی نییە.")
        return
    text = " ".join(role_list)
    if len(text) > 4000:
        text = text[:3997] + "..."
    embed = discord.Embed(title=f"Roles | رۆڵەکان ({len(role_list)})", description=text)
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(manage_messages=True)
async def say(ctx, *, message: str):
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        pass
    await ctx.send(message)

@say.error
async def say_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need the Manage Messages permission to use this. | مووچەی بەڕێوەبردنی پەیامەکان پێویستە.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Usage: $say <message> | بەکارهێنان: $say <پەیام>")

@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason: str = "No reason provided | هیچ هۆکارێک نەدراوە"):
    if member == ctx.author:
        await ctx.send("You cannot kick yourself. | ناتوانیت خۆت لەدەربکەیت.")
        return
    if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
        await ctx.send("You cannot kick someone with an equal or higher role. | ناتوانیت کەسێک لەدەربکەیت کە رۆڵی یەکسان یان بەرزتر هەیە.")
        return
    try:
        await member.kick(reason=reason)
        await ctx.send(f"Kicked {member.mention}. Reason: {reason} | {member.mention} لەدەرکرا. هۆکار: {reason}")
    except discord.Forbidden:
        await ctx.send("I don't have permission to kick that member. | مووچەم نییە ئەم ئەندامە لەدەربکەم.")

@kick.error
async def kick_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need the Kick Members permission. | مووچەی لەدەرکردنی ئەندامان پێویستە.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("Member not found. | ئەندام نەدۆزرایەوە.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Usage: $kick @member [reason] | بەکارهێنان: $kick @ئەندام [هۆکار]")

@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason: str = "No reason provided | هیچ هۆکارێک نەدراوە"):
    if member == ctx.author:
        await ctx.send("You cannot ban yourself. | ناتوانیت خۆت بلۆک بکەیت.")
        return
    if member.top_role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
        await ctx.send("You cannot ban someone with an equal or higher role. | ناتوانیت کەسێک بلۆک بکەیت کە رۆڵی یەکسان یان بەرزتر هەیە.")
        return
    try:
        await member.ban(reason=reason, delete_message_days=0)
        await ctx.send(f"Banned {member.mention}. Reason: {reason} | {member.mention} بلۆک کرا. هۆکار: {reason}")
    except discord.Forbidden:
        await ctx.send("I don't have permission to ban that member. | مووچەم نییە ئەم ئەندامە بلۆک بکەم.")

@ban.error
async def ban_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need the Ban Members permission. | مووچەی بلۆک کردنی ئەندامان پێویستە.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("Member not found. | ئەندام نەدۆزرایەوە.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Usage: $ban @member [reason] | بەکارهێنان: $ban @ئەندام [هۆکار]")

@bot.command()
@commands.has_permissions(ban_members=True)
async def unban(ctx, user_id: int, *, reason: str = "No reason provided | هیچ هۆکارێک نەدراوە"):
    try:
        user = await bot.fetch_user(user_id)
        await ctx.guild.unban(user, reason=reason)
        await ctx.send(f"Unbanned {user}. | بلۆکی {user} لادرا.")
    except discord.NotFound:
        await ctx.send("That user is not banned (or does not exist). | ئەم بەکارهێنەرە بلۆک نەکراوە (یان بوونی نییە).")
    except discord.Forbidden:
        await ctx.send("I don't have permission to unban users. | مووچەم نییە بلۆکی بەکارهێنەران بلادەم.")

@unban.error
async def unban_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need the Ban Members permission. | مووچەی بلۆک کردنی ئەندامان پێویستە.")
    elif isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
        await ctx.send("Usage: $unban <user_id> [reason] | بەکارهێنان: $unban <ناسنامەی بەکارهێنەر> [هۆکار]")

@bot.command()
@commands.has_permissions(moderate_members=True)
async def mute(ctx, member: discord.Member, minutes: int = 10, *, reason: str = "No reason provided | هیچ هۆکارێک نەدراوە"):
    if minutes < 1 or minutes > 40320:
        await ctx.send("Duration must be between 1 minute and 28 days (40320 minutes). | ماوە دەبێت لە نێوان ١ خولەک و ٢٨ رۆژ (40320 خولەک) بێت.")
        return
    until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)
    try:
        await member.timeout(until, reason=reason)
        await ctx.send(f"Muted {member.mention} for {minutes} minute(s). Reason: {reason} | {member.mention} بێدەنگ کرا بۆ {minutes} خولەک. هۆکار: {reason}")
    except discord.Forbidden:
        await ctx.send("I don't have permission to time out that member. | مووچەم نییە ئەم ئەندامە بێدەنگ بکەم.")

@mute.error
async def mute_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need the Moderate Members permission. | مووچەی کونترۆڵی ئەندامان پێویستە.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("Member not found. | ئەندام نەدۆزرایەوە.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Usage: $mute @member [minutes] [reason] | بەکارهێنان: $mute @ئەندام [خولەک] [هۆکار]")

@bot.command()
@commands.has_permissions(moderate_members=True)
async def unmute(ctx, member: discord.Member):
    try:
        await member.timeout(None)
        await ctx.send(f"Unmuted {member.mention}. | بێدەنگیی {member.mention} لادرا.")
    except discord.Forbidden:
        await ctx.send("I don't have permission to remove the timeout. | مووچەم نییە بێدەنگیەکە لابدەم.")

@unmute.error
async def unmute_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need the Moderate Members permission. | مووچەی کونترۆڵی ئەندامان پێویستە.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("Member not found. | ئەندام نەدۆزرایەوە.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Usage: $unmute @member | بەکارهێنان: $unmute @ئەندام")

@bot.command()
@commands.has_permissions(kick_members=True)
async def warn(ctx, member: discord.Member, *, reason: str = "No reason provided | هیچ هۆکارێک نەدراوە"):
    if member.bot:
        await ctx.send("You can't warn a bot. | ناتوانیت بۆتێک ئاگادار بکەیتەوە.")
        return
    g = warnings_data.setdefault(str(ctx.guild.id), {})
    user_warns = g.setdefault(str(member.id), [])
    user_warns.append({
        "reason": reason,
        "moderator": str(ctx.author.id),
        "timestamp": int(time.time()),
    })
    save_warnings()
    await ctx.send(f"Warned {member.mention}. They now have {len(user_warns)} warning(s). Reason: {reason}\n"
                   f"ئاگاداری دراوە {member.mention}. ئێستا {len(user_warns)} ئاگاداری هەیە. هۆکار: {reason}")

@warn.error
async def warn_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need the Kick Members permission to warn. | مووچەی لەدەرکردنی ئەندامان پێویستە بۆ ئاگادارکردنەوە.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("Member not found. | ئەندام نەدۆزرایەوە.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Usage: $warn @member [reason] | بەکارهێنان: $warn @ئەندام [هۆکار]")

@bot.command()
async def warnings(ctx, member: discord.Member = None):
    member = member or ctx.author
    g = warnings_data.get(str(ctx.guild.id), {})
    user_warns = g.get(str(member.id), [])
    if not user_warns:
        await ctx.send(f"{member.display_name} has no warnings. | {member.display_name} هیچ ئاگادارییەکی نییە.")
        return
    embed = discord.Embed(title=f"Warnings | ئاگاداریەکان for {member.display_name} ({len(user_warns)})")
    for i, w in enumerate(user_warns[-10:], start=1):
        when = datetime.datetime.fromtimestamp(w["timestamp"]).strftime("%Y-%m-%d %H:%M")
        mod = ctx.guild.get_member(int(w["moderator"]))
        mod_name = mod.display_name if mod else f"User {w['moderator']}"
        embed.add_field(name=f"#{i} - {when}", value=f"By | لەلایەن {mod_name}: {w['reason']}", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="clearwarns")
@commands.has_permissions(kick_members=True)
async def clearwarns(ctx, member: discord.Member):
    g = warnings_data.get(str(ctx.guild.id), {})
    if str(member.id) in g:
        g.pop(str(member.id))
        save_warnings()
        await ctx.send(f"Cleared all warnings for {member.mention}. | هەموو ئاگاداریەکانی {member.mention} سڕدرایەوە.")
    else:
        await ctx.send(f"{member.display_name} has no warnings. | {member.display_name} هیچ ئاگادارییەکی نییە.")

# --- RACE GAME DATA ---
ANIMALS = [
    "Lion", "Tiger", "Cheetah", "Leopard", "Horse", "Zebra", "Greyhound", "Jackal",
    "Wolf", "Coyote", "Fox", "Deer", "Antelope", "Gazelle", "Rabbit", "Hare",
    "Kangaroo", "Springbok", "Ostrich", "Emu", "Elephant", "Rhino", "Hippo",
    "Giraffe", "Bison", "Buffalo", "Wild Boar", "Pig", "Goat", "Sheep", "Dog",
    "Cat", "Camel", "Llama", "Gorilla", "Monkey", "Baboon", "Bear", "Panda",
    "Koala", "Sloth", "Snail", "Turtle", "Crab", "Frog", "Eagle",
    "Hawk", "Falcon", "Squirrel", "Mouse", "Rat", "Snump (Snail)"
]
RACE_EMOJIS = ["🏁", "🐎", "🐆", "🐇", "🐕", "🐘", "🐒", "🐢", "🐌", "🦊", "🐷", "🦁"]

class Racer:
    def __init__(self, name, emoji):
        self.name = name
        self.emoji = emoji
        self.distance = 0
        self.speed = random.randint(2, 12)
        self.finished = False

def get_race_progress_bar(percent):
    filled = int(round(15 * percent))
    bar = "█" * filled + "░" * (15 - filled)
    return f"[{bar}]"

# --- FUN & GAMES ---

@bot.command(aliases=["nick"])
@commands.has_permissions(manage_nicknames=True)
async def nickname(ctx, member: discord.Member, *, new_nick: str = None):
    try:
        await member.edit(nick=new_nick)
        if new_nick:
            await ctx.send(f"Changed {member.mention}'s nickname to **{new_nick}**. | ناوی نمایشی {member.mention} گۆڕدرا بۆ **{new_nick}**.")
        else:
            await ctx.send(f"Reset {member.mention}'s nickname. | ناوی نمایشی {member.mention} ڕێکخرایەوە.")
    except discord.Forbidden:
        await ctx.send("I don't have permission to change that nickname. | مووچەم نییە ئەو ناوی نمایشیە بگۆڕم.")

@nickname.error
async def nickname_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need the Manage Nicknames permission. | مووچەی بەڕێوەبردنی ناوی نمایشی پێویستە.")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("Member not found. | ئەندام نەدۆزرایەوە.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Usage: $nickname @member [new nickname]  (omit to reset) | بەکارهێنان: $nickname @ئەندام [ناوی نوێ] (بەتاڵ بهێڵە بۆ ڕێکخستنەوە)")

@bot.command(name="timeout", aliases=["tmo"])
@commands.has_permissions(moderate_members=True)
async def timeout_cmd(ctx, member: discord.Member, minutes: int = 10, *, reason: str = "No reason provided | هیچ هۆکارێک نەدراوە"):
    await mute(ctx, member, minutes, reason=reason)

@bot.command(name="createchannel", aliases=["addchannel"])
@commands.has_permissions(manage_channels=True)
async def createchannel(ctx, channel_type: str, *, name: str):
    channel_type = channel_type.lower()
    try:
        if channel_type == "text":
            ch = await ctx.guild.create_text_channel(name)
        elif channel_type == "voice":
            ch = await ctx.guild.create_voice_channel(name)
        else:
            await ctx.send("Channel type must be `text` or `voice`. | جۆری کەناڵ دەبێت `text` یان `voice` بێت.")
            return
        await ctx.send(f"Created {channel_type} channel: {ch.mention} | کەناڵی {channel_type} دروستکرا: {ch.mention}")
    except discord.Forbidden:
        await ctx.send("I don't have permission to create channels. | مووچەم نییە کەناڵ دروست بکەم.")

@createchannel.error
async def createchannel_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need the Manage Channels permission. | مووچەی بەڕێوەبردنی کەناڵەکان پێویستە.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Usage: $createchannel <text|voice> <name> | بەکارهێنان: $createchannel <text|voice> <ناو>")

@bot.command(name="deletechannel")
@commands.has_permissions(manage_channels=True)
async def deletechannel(ctx, channel: discord.abc.GuildChannel = None):
    target = channel or ctx.channel
    name = target.name
    try:
        await target.delete()
        if channel is not None:
            await ctx.send(f"Deleted channel **#{name}**. | کەناڵی **#{name}** سڕدرایەوە.")
    except discord.Forbidden:
        await ctx.send("I don't have permission to delete that channel. | مووچەم نییە ئەو کەناڵە بسڕمەوە.")

@deletechannel.error
async def deletechannel_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need the Manage Channels permission. | مووچەی بەڕێوەبردنی کەناڵەکان پێویستە.")

@bot.command(name="addrole", aliases=["role"])
@commands.has_permissions(manage_roles=True)
async def addrole(ctx, member: discord.Member, *, role: discord.Role):
    if role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
        await ctx.send("You can't assign a role equal to or higher than your own. | ناتوانیت رۆڵێک دابنێیت کە یەکسانە یان بەرزتر لە رۆڵی خۆتە.")
        return
    if role >= ctx.guild.me.top_role:
        await ctx.send("That role is higher than mine, I can't assign it. | ئەو رۆڵە بەرزتر لە رۆڵی منە، ناتوانم دابنێم.")
        return
    try:
        await member.add_roles(role)
        await ctx.send(f"Added **{role.name}** to {member.mention}. | **{role.name}** زیادکرا بۆ {member.mention}.")
    except discord.Forbidden:
        await ctx.send("I don't have permission to add that role. | مووچەم نییە ئەو رۆڵە زیاد بکەم.")

@addrole.error
async def addrole_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need the Manage Roles permission. | مووچەی بەڕێوەبردنی رۆڵەکان پێویستە.")
    elif isinstance(error, (commands.MissingRequiredArgument, commands.RoleNotFound, commands.MemberNotFound)):
        await ctx.send("Usage: $addrole @member <role name> | بەکارهێنان: $addrole @ئەندام <ناوی رۆڵ>")

@bot.command(name="removerole")
@commands.has_permissions(manage_roles=True)
async def removerole(ctx, member: discord.Member, *, role: discord.Role):
    if role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
        await ctx.send("You can't remove a role equal to or higher than your own. | ناتوانیت رۆڵێک لابدەیت کە یەکسانە یان بەرزتر لە رۆڵی خۆتە.")
        return
    if role >= ctx.guild.me.top_role:
        await ctx.send("That role is higher than mine, I can't remove it. | ئەو رۆڵە بەرزتر لە رۆڵی منە، ناتوانم لابدەم.")
        return
    try:
        await member.remove_roles(role)
        await ctx.send(f"Removed **{role.name}** from {member.mention}. | **{role.name}** لەلایەن {member.mention} لادرا.")
    except discord.Forbidden:
        await ctx.send("I don't have permission to remove that role. | مووچەم نییە ئەو رۆڵە لابدەم.")

@removerole.error
async def removerole_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You need the Manage Roles permission. | مووچەی بەڕێوەبردنی رۆڵەکان پێویستە.")
    elif isinstance(error, (commands.MissingRequiredArgument, commands.RoleNotFound, commands.MemberNotFound)):
        await ctx.send("Usage: $removerole @member <role name> | بەکارهێنان: $removerole @ئەندام <ناوی رۆڵ>")


@bot.command()
async def rps(ctx, choice: str = None):
    options = ["rock", "paper", "scissors"]
    if not choice or choice.lower() not in options:
        await ctx.send("Usage: $rps <rock|paper|scissors> | بەکارهێنان: $rps <بەردەوشک|کاغەز|مەقەس>")
        return
    user = choice.lower()
    bot_choice = random.choice(options)
    if user == bot_choice:
        result = "It's a tie! | یەکسانە!"
    elif (user, bot_choice) in [("rock", "scissors"), ("paper", "rock"), ("scissors", "paper")]:
        result = "You win! 🎉 | تۆ بردیت! 🎉"
    else:
        result = "I win! 🤖 | من بردم! 🤖"
    emojis = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
    await ctx.send(f"You | تۆ: {emojis[user]} {user}\nMe | من: {emojis[bot_choice]} {bot_choice}\n**{result}**")

number_games = {}
lucky_games = {}
race_lobbies = {}
anime_quizzes = {}
active_quizzes = {}
tf_games = {}
active_giveaways = {}
active_games = {}

@bot.command()
async def guess(ctx):
    if ctx.channel.id in number_games:
        await ctx.send("A number guessing game is already running here. Type `$stopgame` to end it. | یاری حەزرکردنی ژمارە بەردەوامە ئێرە. `$stopgame` بنووسە بۆ کۆتایی پێهێنان.")
        return
    number_games[ctx.channel.id] = {"number": random.randint(1, 100), "tries": 0, "host": ctx.author.id}
    await ctx.send(
        "🎯 I picked a number between **1** and **100**. | ژمارەیەک هەڵبژاردم لە نێوان **١** و **١٠٠**.\n"
        "Send your guesses in chat. Type `$stopgame` to give up. | حەزرەکانت بنێرە لە چاتدا. `$stopgame` بنووسە بۆ تەرككردن."
    )

@bot.command()
async def stopgame(ctx):
    game = number_games.pop(ctx.channel.id, None)
    hangman = hangman_games.pop(ctx.channel.id, None)
    lucky = lucky_games.pop(ctx.channel.id, None)
    if game:
        await ctx.send(f"Game ended. The number was **{game['number']}**. | یاری کۆتایی هات. ژمارەکە **{game['number']}** بوو.")
    elif hangman:
        await ctx.send(f"Game ended. The word was **{hangman['word']}**. | یاری کۆتایی هات. وشەکە **{hangman['word']}** بوو.")
    elif lucky:
        task = lucky.get("task")
        if task:
            task.cancel()
        await ctx.send(f"Lucky game ended. The number was **{lucky['number']}**. | یاری بەختەوار کۆتایی هات. ژمارەکە **{lucky['number']}** بوو.")
    else:
        await ctx.send("No game is running here. | هیچ یارییەک ئێرە ناکرێت.")

async def _lucky_timeout_watcher(channel_id: int):
    """
    Auto-ends a lucky game after inactivity:
    - 60 s  if nobody has guessed yet
    - 180 s if at least one guess was made
    Checks every 10 seconds so the sleep is interruptible via cancellation.
    """
    try:
        while True:
            await asyncio.sleep(10)
            game = lucky_games.get(channel_id)
            if game is None:
                return
            elapsed = time.time() - game["last_activity"]
            limit = 180 if game["has_guesses"] else 60
            if elapsed >= limit:
                lucky_games.pop(channel_id, None)
                channel = bot.get_channel(channel_id)
                if channel:
                    timeout_embed = discord.Embed(
                        title="⏰ Lucky Game Ended / یاری بەختەوار کۆتایی هات",
                        description=(
                            f"**English:** No activity for {limit} seconds! Game over. The number was **{game['number']}**.\n\n"
                            f"**کوردی:** {limit} چرکە چالاکی نەبوو! یاری کۆتایی هات. ژمارەکە **{game['number']}** بوو."
                        ),
                        color=discord.Color.red()
                    )
                    try:
                        await channel.send(embed=timeout_embed)
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                return
    except asyncio.CancelledError:
        pass

@bot.command()
async def lucky(ctx):
    if ctx.channel.id in lucky_games:
        await ctx.send("A Lucky game is already running here! Type a number to guess, or `$stopgame` to end it. | یاری بەختەوار بەردەوامە ئێرە! ژمارەیەک بنووسە بۆ حەزرکردن، یان `$stopgame` بۆ کۆتایی پێهێنان.")
        return
    task = asyncio.create_task(_lucky_timeout_watcher(ctx.channel.id))
    lucky_games[ctx.channel.id] = {
        "number": random.randint(1, 100),
        "last_activity": time.time(),
        "has_guesses": False,
        "task": task,
        "guesses": 0,
        "guild_id": ctx.guild.id if ctx.guild else None,
    }
    embed = discord.Embed(
        title="🎮 Lucky Game / یاری بەختەوار",
        description=(
            "**English:** Hey there! I picked a number between **1 - 100**. Pick a number!\n\n"
            "**کوردی:** سڵاو! من ժمارەیەکم هەڵبժارد لەنێوان **۱ - ۱۰۰**. تکایە ժمارەیەک ئەگێڕە!\n\n"
            "⏰ **English:** Game auto-ends in **60s** if no one guesses, or **3 min** after last guess.\n"
            "⏰ **کوردی:** یارییەکە بە خۆی کۆتایی دێت لە **۶۰چ** ئەگەر کەس حەزر نەکات، یان **۳ خولەک** پاش کۆتایین حەزر."
        ),
        color=discord.Color.gold()
    )
    await ctx.send(embed=embed)

@bot.command(name="luckylb", aliases=["luckyleaderboard", "luckyboard"])
async def luckylb(ctx):
    if ctx.guild is None:
        await ctx.send("This command can only be used in a server. | ئەم فەرمانە تەنها لە سێرڤەر دەکرێت بەکارهێنرێت.")
        return
    conn = get_db()
    rows = conn.execute(
        "SELECT user_id, wins, total_guesses, best_guesses FROM lucky_leaderboard "
        "WHERE guild_id = ? AND wins > 0 ORDER BY wins DESC, best_guesses ASC LIMIT 10",
        (ctx.guild.id,)
    ).fetchall()
    conn.close()
    if not rows:
        await ctx.send("Nobody has won the Lucky game yet! Start one with `$lucky`. | هێشتا کەس یاری بەختەوارى نەبردووە! بە `$lucky` دەستپێبکە.")
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, row in enumerate(rows):
        member = ctx.guild.get_member(row["user_id"])
        name = member.display_name if member else f"User {row['user_id']}"
        prefix = medals[i] if i < 3 else f"`#{i+1}`"
        avg = round(row["total_guesses"] / row["wins"], 1) if row["wins"] else 0
        best = row["best_guesses"]
        lines.append(
            f"{prefix} **{name}**\n"
            f"> 🏆 Wins: **{row['wins']}** · ⚡ Best: **{best}** guesses · 📊 Avg: **{avg}**"
        )
    embed = discord.Embed(
        title="🎮 Lucky Game Leaderboard / لیستی باشترینانی یاری بەختەوار",
        description="\n\n".join(lines),
        color=discord.Color.gold(),
        timestamp=datetime.datetime.utcnow(),
    )
    embed.set_footer(text=f"{ctx.guild.name} · Play with $lucky!")
    await ctx.send(embed=embed)

@bot.command()
async def bomb(ctx):
    wires = ["Red", "Blue", "Green", "Yellow"]
    correct_wire = random.choice(wires)
    embed = discord.Embed(
        title="💣 Defuse the Bomb! / بۆمبەکە خەڵات بکە!",
        description=(
            "**English:** A bomb has been planted! Cut the correct wire to defuse it before it explodes!\n\n"
            "**کوردی:** بۆمبەیەک دانراوە! وایەری دروست ببڕە بۆ خەڵات کردنی پێش ئەوەی تەقا بکات!"
        ),
        color=discord.Color.orange()
    )
    embed.set_footer(text="Choose wisely! / بە تێڕوانین هەڵبژێرە!")
    view = BombView(correct_wire)
    await ctx.send(embed=embed, view=view)


class RaceLobbyView(discord.ui.View):
    """Persistent join button for the race lobby."""

    def __init__(self, channel_id: int):
        super().__init__(timeout=35)   # slightly longer than the 30s lobby
        self.channel_id = channel_id

    @discord.ui.button(
        label="🏁 Join Race!  |  بەشداربوو!",
        style=discord.ButtonStyle.success,
        custom_id="race_join"
    )
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        channel = interaction.channel
        lobby   = race_lobbies.get(self.channel_id)

        # Lobby gone (race already started or cancelled)
        if not lobby:
            button.disabled = True
            await interaction.response.edit_message(view=self)
            await interaction.followup.send(
                "❌ هیچ لۆبییەک نییە! | No active lobby right now.",
                ephemeral=True
            )
            return

        # Already joined
        if interaction.user.id in lobby["players"]:
            await interaction.response.send_message(
                f"⚠️ {interaction.user.mention} تۆ پێشتر بەشداربووی! | You already joined!",
                ephemeral=True
            )
            return

        # Lobby full
        if len(lobby["players"]) >= 5:
            await interaction.response.send_message(
                "🚫 لۆبی پڕە (٥/٥) | Lobby is full (5/5). Wait for the next race!",
                ephemeral=True
            )
            return

        # Add player
        used_animals = {r.name for r in lobby["players"].values()}
        available    = [a for a in ANIMALS if a not in used_animals] or ANIMALS
        animal_name  = random.choice(available)
        animal_emoji = random.choice(RACE_EMOJIS)
        racer        = Racer(animal_name, animal_emoji)
        lobby["players"][interaction.user.id] = racer

        updated_embed = await _build_lobby_embed(channel, countdown=None)

        # If lobby is now full, disable the button
        if len(lobby["players"]) >= 5:
            button.disabled = True

        try:
            await interaction.response.edit_message(embed=updated_embed, view=self)
        except Exception:
            await interaction.response.defer()

        await interaction.followup.send(
            f"✅ {interaction.user.mention} بەشداربوو! ئاژەڵەکەت {animal_emoji} **{animal_name}**ە! | "
            f"Joined! Your animal is {animal_emoji} **{animal_name}**! 🎉",
            ephemeral=True
        )

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


async def _build_lobby_embed(channel, countdown=None):

    """Build the lobby waiting embed."""
    lobby = race_lobbies.get(channel.id)
    if not lobby:
        return None
    players = lobby["players"]
    lines = ""
    for uid, r in players.items():
        member = channel.guild.get_member(uid)
        name = member.display_name if member else f"User {uid}"
        lines += f"{r.emoji} **{name}** — {r.name}\n"
    if not lines:
        lines = "*No racers yet... / هێشتا کەس نەچووەتە ناو...*"
    timer_line = f"\n⏳ **{countdown}s** remaining / ماوەی بەردەوام" if countdown is not None else ""
    needed = max(0, 3 - len(players))
    status = (
        f"✅ **English:** Lobby open! Click the **Join Race** button below!{timer_line}\n"
        f"✅ **کوردی:** لۆبی کراوەیە! دوگمەی **بەشداربوو** لە خوارەوە دابگرە!\n\n"
        f"👥 **Players ({len(players)}/5):** *(need {needed} more / {needed} زیاتر پێویستە)*\n"
        f"{lines}"
    )
    embed = discord.Embed(
        title="🏁 Animal Race Lobby / لۆبیی پێشبڕکێی ئاژەڵان",
        description=status,
        color=discord.Color.gold()
    )
    embed.set_footer(text="Min 3 players to start · Max 5 · حەدەقەل ٣ بەشداربێ بۆ دەستپێکردن · زۆرینە ٥")
    return embed

async def _run_race(channel):
    """Countdown 30s then run or cancel the race."""
    lobby = race_lobbies.get(channel.id)
    if not lobby:
        return
    try:
        for secs_left in range(30, 0, -5):
            lobby = race_lobbies.get(channel.id)
            if not lobby:
                return
            embed = await _build_lobby_embed(channel, countdown=secs_left)
            if embed and lobby.get("lobby_msg"):
                try:
                    await lobby["lobby_msg"].edit(embed=embed)
                except Exception:
                    pass
            await asyncio.sleep(5)

        lobby = race_lobbies.pop(channel.id, None)
        if not lobby:
            return

        players = lobby["players"]

        if len(players) < 3:
            cancel_embed = discord.Embed(
                title="❌ Race Cancelled / پێشبڕکێ هەڵوەشایەوە",
                description=(
                    f"**English:** Not enough players joined. Need at least 3, only {len(players)} joined.\n\n"
                    f"**کوردی:** بەشداربوانی پێویست نەبوون. حەدەقەل ٣ دەکرێت، تەنها {len(players)} بەشداربوون."
                ),
                color=discord.Color.red()
            )
            try:
                await lobby["lobby_msg"].edit(embed=cancel_embed)
            except Exception:
                await channel.send(embed=cancel_embed)
            return

        racers = list(players.values())
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        user_ids = list(players.keys())

        # Disable the join button — race is starting
        lobby_view = lobby.get("view") if lobby else None
        if lobby_view:
            for child in lobby_view.children:
                child.disabled = True
            try:
                await lobby["lobby_msg"].edit(view=lobby_view)
            except Exception:
                pass

        countdown_embed = discord.Embed(
            title="🚦 Get Ready! / ئامادە بە!",
            description=(
                "**English:** The race is about to begin! 3... 2... 1... GO!\n\n"
                "**کوردی:** پێشبڕکێکە دەست پێ دەکات! ٣... ٢... ١... بڕۆ!"
            ),
            color=discord.Color.orange()
        )
        for i, r in enumerate(racers):
            uid = user_ids[i]
            member = channel.guild.get_member(uid)
            mname = member.display_name if member else f"User {uid}"
            countdown_embed.add_field(name=f"{r.emoji} {r.name}", value=f"Ridden by **{mname}**", inline=True)
        try:
            await lobby["lobby_msg"].edit(embed=countdown_embed)
        except Exception:
            pass
        await asyncio.sleep(3)

        track_length = 100
        winners = []
        msg = lobby["lobby_msg"]

        while len(winners) < len(racers):
            for r in racers:
                if not r.finished:
                    r.distance = min(r.distance + r.speed + random.randint(1, 5), track_length)
                    if r.distance >= track_length:
                        r.finished = True
                        winners.append(r)

            status = ""
            for i, r in enumerate(racers):
                uid = user_ids[i]
                member = channel.guild.get_member(uid)
                mname = member.display_name if member else f"User {uid}"
                pct = r.distance / track_length
                bar = get_race_progress_bar(pct)
                pos_medal = medals[winners.index(r)] if r in winners else "🏃"
                status += f"{pos_medal} {r.emoji} {bar} **{int(pct*100)}%** {r.name} *({mname})*\n"

            race_embed = discord.Embed(
                title="🏃 Racing! / پێشبڕکێ بەردەوامە!",
                description=status,
                color=discord.Color.blue()
            )
            try:
                await msg.edit(embed=race_embed)
            except Exception:
                pass
            if len(winners) < len(racers):
                await asyncio.sleep(0.8)

        res = "**English:** Race complete! Results:\n**کوردی:** پێشبڕکێ تەواو بوو! ئەنجامەکان:\n\n"
        for i, r in enumerate(winners):
            uid = user_ids[racers.index(r)]
            member = channel.guild.get_member(uid)
            mname = member.display_name if member else f"User {uid}"
            res += f"{medals[i]} {r.emoji} **{r.name}** — {mname}\n"

        final_embed = discord.Embed(
            title="🏆 Race Results / ئەنجامەکانی پێشبڕکێ",
            description=res,
            color=discord.Color.green(),
            timestamp=datetime.datetime.utcnow()
        )
        winner_racer = winners[0]
        winner_uid = user_ids[racers.index(winner_racer)]
        winner_member = channel.guild.get_member(winner_uid)
        wname = winner_member.display_name if winner_member else "Someone"
        if "Snail" in winner_racer.name or "Snump" in winner_racer.name:
            final_embed.set_footer(text=f"A Snail won?! GG {wname}! / بزوێنەرەکە بردیەوە؟!")
        elif "Horse" in winners[-1].name:
            final_embed.set_footer(text=f"The Horse came last?! GG {wname}! / ئەسپەکە کۆتایی هات؟!")
        else:
            final_embed.set_footer(text=f"🎉 GG {wname}! Great race! / پێشبڕکێیەکی باش!")
        try:
            await msg.edit(embed=final_embed)
        except Exception:
            await channel.send(embed=final_embed)

    except asyncio.CancelledError:
        pass

@bot.command()
async def race(ctx):
    channel = ctx.channel

    # If a lobby is already open, tell them to click the button
    if channel.id in race_lobbies:
        lobby = race_lobbies[channel.id]
        if ctx.author.id in lobby["players"]:
            await ctx.send(
                f"⚠️ {ctx.author.mention} تۆ پێشتر بەشداربووی! | You already joined this race!",
                delete_after=8
            )
        elif len(lobby["players"]) >= 5:
            await ctx.send(
                f"🚫 لۆبی پڕە (٥/٥) | Lobby full (5/5). Wait for the next race!",
                delete_after=8
            )
        else:
            await ctx.send(
                f"🏁 {ctx.author.mention} **دوگمەی بەشداربوو** لە لۆبیی سەرەوەدا بکە! | "
                f"Click the **Join Race** button on the lobby message above!",
                delete_after=10
            )
        try:
            await ctx.message.delete()
        except Exception:
            pass
        return

    # Create a new lobby
    animal_name  = random.choice(ANIMALS)
    animal_emoji = random.choice(RACE_EMOJIS)
    racer        = Racer(animal_name, animal_emoji)
    view         = RaceLobbyView(channel.id)

    task = asyncio.get_event_loop().create_task(_run_race(channel))
    race_lobbies[channel.id] = {
        "players":   {ctx.author.id: racer},
        "task":      task,
        "lobby_msg": None,
        "view":      view,
    }

    lobby_embed = await _build_lobby_embed(channel, countdown=30)
    lobby_msg   = await ctx.send(embed=lobby_embed, view=view)
    race_lobbies[channel.id]["lobby_msg"] = lobby_msg

    try:
        await ctx.message.delete()
    except Exception:
        pass

@bot.command(name="spystart")
async def spy_start(ctx):
    if not ctx.guild:
        return await ctx.send("This command only works in a server. | ئەم فەرمانە تەنها لە سێرڤەر کاردەکات.")
    gid  = ctx.guild.id
    game = spy_games.get(gid)

    if not game:
        e = discord.Embed(title="❌ No Lobby | هیچ لۆبییەک نییە", description="No lobby found! Use `$spyjoin` first. | هیچ لۆبییەک نەدۆزرایەوە! پێشتر `$spyjoin` بەکارهێنە.", color=0xed4245)
        return await ctx.send(embed=e)
    if game["started"]:
        e = discord.Embed(title="⚠️ Already Running | بەردەوامە", description="Game is already active! Use `$spystop` to cancel. | یاری ئێستا چالاکە! `$spystop` بەکارهێنە بۆ هەڵوەشاندنەوە.", color=0xfee75c)
        return await ctx.send(embed=e)
    if len(game["players"]) < 3:
        e = discord.Embed(
            title="👥 Not Enough Players | یاریزانی پێویست نییە",
            description=f"Need **3+** players. Currently **{len(game['players'])}/3** — invite more! | پێویستمانە بە **٣+** یاریزان. ئێستا **{len(game['players'])}/3** — زیاتر بانگهێشت بکە!",
            color=0xfee75c,
        )
        return await ctx.send(embed=e)

    game["started"]  = True
    game["spy"]      = random.choice(game["players"])
    game["location"] = random.choice(SPY_LOCATIONS)

    roster = "\n".join(f"{SPY_MEDALS[i]} {p.mention}" for i, p in enumerate(game["players"]))
    e = discord.Embed(
        title="🔴 OPERATION SPY HUNT — ACTIVE | کارگێڕی نێچیرکردنی سیخوڕ — چالاک",
        description=(
            "```\n🕵️  یاری سیخوڕ دەستی پێکرد  🕵️\n```\n"
            "**Check your DMs! | پەیامە تایبەتەکانت بپشکنە!** Each round you'll receive a secret clue. | هەر دوورێک ئاگاداری نهێنی وەردەگریت.\n"
            "Civilians — you know the location. Find the spy. | هاووڵاتییەکان — شوێنەکەتان دەزانن. سیخوڕەکە بدۆزنەوە.\n"
            "Spy — you know nothing. Blend in! | سیخوڕ — تۆ هیچ نازانیت. خۆت تێکەڵ بکە!\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 **Agents | ئاجێنتەکان ({len(game['players'])})**\n{roster}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🌀 **{SPY_TOTAL_ROUNDS} rounds | دوور × {SPY_ROUND_SECONDS}s each | هەر یەکێک**\n"
            "`$spyvote` skip to vote | بازبکە بۆ دەنگدان  •  `$spystop` end game | کۆتایی بهێنە"
        ),
        color=0x2b2d31,
    )
    e.set_footer(text="🔴 The spy is among you — good luck, agents! | سیخوڕ لەنێوانتانەوە — بەختی باش، ئاجێنتەکان!")
    await ctx.send(embed=e)

    for rnd in range(1, SPY_TOTAL_ROUNDS + 1):
        cur = spy_games.get(gid)
        if not cur or not cur.get("started") or cur.get("voting"):
            return

        location = cur["location"]

        dm_fails = []
        for player in cur["players"]:
            try:
                if player.id == cur["spy"].id:
                    dm = discord.Embed(
                        title=f"🕵️ دور {rnd}/{SPY_TOTAL_ROUNDS} — YOU ARE THE SPY | تۆ سیخوڕیت",
                        description=(
                            "```diff\n- CLASSIFIED | نهێنی\n```\n"
                            "😈 **تۆ سیخوڕیت! | You are the spy!**\n\n"
                            "You still don't know the location. | هێشتا شوێنەکە نازانیت.\n"
                            "Listen carefully, ask smart questions,\nand **don't get caught! | دەستگیر مەبە!**"
                        ),
                        color=0xed4245,
                    )
                    dm.set_footer(text=f"Round | دوور {rnd}/{SPY_TOTAL_ROUNDS} • Stay cool 🧊 | ئارام بمێنەوە")
                else:
                    dm = discord.Embed(
                        title=f"📍 دور {rnd}/{SPY_TOTAL_ROUNDS} — LOCATION UPDATE | نوێکردنەوەی شوێن",
                        description=(
                            f"```diff\n+ CIVILIAN BRIEFING — ROUND | داواکاری هاووڵاتی — دوور {rnd}\n```\n"
                            f"🗺️ This round's secret location | شوێنی نهێنی ئەم دووری:\n\n"
                            f"# ‎{location}\n\n"
                            "Give **one word** as a clue — don't say the location directly! | **یەک وشە** بدە وەک ئاگادارکردنەوە — شوێنەکە ڕاستەوخۆ مەڵێ!"
                        ),
                        color=0x57f287,
                    )
                    dm.set_footer(text=f"Round | دوور {rnd}/{SPY_TOTAL_ROUNDS} • Find the spy 🔍 | سیخوڕەکە بدۆزەرەوە")
                await player.send(embed=dm)
            except discord.Forbidden:
                dm_fails.append(player.display_name)

        mentions = " ".join(p.mention for p in cur["players"])
        color    = SPY_ROUND_COLORS[rnd - 1]
        progress = "🟥" * rnd + "⬛" * (SPY_TOTAL_ROUNDS - rnd)

        rnd_embed = discord.Embed(
            title=f"🌀 دور {rnd} — ROUND | دوور {rnd} of | لە {SPY_TOTAL_ROUNDS}",
            description=(
                f"{mentions}\n\n"
                f"```\n🎙  SAY ONE WORD about the location! | یەک وشە دەربارەی شوێنەکە بڵێ!\n```\n"
                "Civilians — hint without revealing the place. | هاووڵاتییەکان — ئاگاداری بدە بەبێ ئاشکراکردنی شوێن.\n"
                "Spy — make something up 😈 | سیخوڕ — شتێک دروست بکە 😈\n\n"
                f"**Progress | پێشکەوتن:** {progress}\n"
                f"⏳ **{SPY_ROUND_SECONDS} seconds | چرکە** — then next round! | دواتر دووری داهاتوو!"
            ),
            color=color,
        )
        if dm_fails:
            rnd_embed.add_field(
                name="⚠️ DMs Blocked | پەیامە تایبەتەکان داخراون",
                value=", ".join(dm_fails) + " — enable DMs to receive clues! | DM چالاک بکە بۆ وەرگرتنی ئاگاداری!",
                inline=False,
            )
        rnd_embed.set_footer(text=f"Round | دوور {rnd}/{SPY_TOTAL_ROUNDS} • $spyvote to vote early | بۆ دەنگدانی زوو • $spystop to end | بۆ کۆتایی")
        try:
            await ctx.send(embed=rnd_embed)
        except Exception:
            return

        await asyncio.sleep(SPY_ROUND_SECONDS)

    final = spy_games.get(gid)
    if not final or not final.get("started") or final.get("voting"):
        return

    mentions = " ".join(p.mention for p in final["players"])
    done = discord.Embed(
        title="🏁 ٥ دور کۆتایی هات — ALL ROUNDS DONE! | هەموو دوورەکان تەواو بوون!",
        description=(
            f"{mentions}\n\n"
            "```diff\n- D I S C U S S I O N   O V E R | نووسراوە تەواو بوو\n```\n"
            "🟥🟥🟥🟥🟥 Five rounds complete! | پێنج دوور تەواو!\n\n"
            "Time to vote. **Who is the spy among you? | کێ سیخوڕەکەیە لەنێوانتاندا؟**\n"
            "The ballot opens in just a moment… 🗳️ | دەنگدانەکە لە کاتێکدا دەکرێتەوە…"
        ),
        color=0xfee75c,
    )
    done.set_footer(text="Choose carefully — the spy is counting on your mistakes. | بە وریاییەوە هەڵبژێرە — سیخوڕەکە پشتی بە هەڵەکانت دەبەستێت.")
    try:
        await ctx.send(embed=done)
    except Exception:
        pass
    await _start_spy_vote(gid, ctx.channel)


@bot.command(name="spyvote")
async def spy_vote_cmd(ctx):
    if not ctx.guild:
        return await ctx.send("This command only works in a server. | ئەم فەرمانە تەنها لە سێرڤەر کاردەکات.")
    game = spy_games.get(ctx.guild.id)
    if not game or not game["started"]:
        e = discord.Embed(title="❌ No Active Game | هیچ یارییەکی چالاکی نییە", description="No game running! Use `$spyjoin` to start one. | هیچ یارییەک ناکرێت! `$spyjoin` بەکارهێنە.", color=0xed4245)
        return await ctx.send(embed=e)
    if game.get("voting"):
        e = discord.Embed(title="🗳️ Already Voting | پێشتر دەنگدرا", description="Voting is already open — check the ballot above! | دەنگدان ئێستا کراوەیە — دەنگدانەکەی سەرووت بپشکنە!", color=0xfee75c)
        return await ctx.send(embed=e)
    e = discord.Embed(
        title="🗳️ Early Vote Called! | دەنگدانی زوو داوایکرا!",
        description=f"{ctx.author.mention} called for an early vote! | داوای دەنگدانی زووی کرد!\n\nOpening the ballot now… | ئێستا دەنگدانەکە دەکرێتەوە…",
        color=0xfee75c,
    )
    await ctx.send(embed=e)
    await _start_spy_vote(ctx.guild.id, ctx.channel)


@bot.command(name="spystop")
async def spy_stop(ctx):
    if not ctx.guild:
        return await ctx.send("This command only works in a server. | ئەم فەرمانە تەنها لە سێرڤەر کاردەکات.")
    game = spy_games.pop(ctx.guild.id, None)
    if not game:
        e = discord.Embed(title="❌ Nothing to Stop | هیچ شتێک بۆ هەڵوەشاندنەوە نییە", description="No active Spy Game found. | هیچ یاری سیخوڕی چالاکی نەدۆزرایەوە.", color=0xed4245)
        return await ctx.send(embed=e)
    spy_name = game["spy"].display_name if game.get("spy") else "Unknown | نەزانراو"
    loc      = game.get("location") or "Unknown | نەزانراو"
    e = discord.Embed(
        title="🛑 یاری هەڵوەشاندرایەوە — GAME TERMINATED | یاری کۆتایی هات",
        description=(
            f"```diff\n- OPERATION CANCELLED BY {ctx.author.display_name.upper()} | هەڵوەشاندرایەوە لەلایەن\n```\n"
            f"🕵️ The spy was: **{spy_name}** | سیخوڕەکە بوو: **{spy_name}**\n"
            f"📍 Last location: **{loc}** | شوێنی کۆتایی: **{loc}**"
        ),
        color=0xed4245,
    )
    e.set_footer(text="Game ended early • Use $spyjoin to play again | یاری زوو کۆتایی هات • $spyjoin بەکارهێنە بۆ یاریکردنی دووبارە")
    await ctx.send(embed=e)


@bot.command(name="spyleave")
async def spy_leave(ctx):
    if not ctx.guild:
        return await ctx.send("This command only works in a server. | ئەم فەرمانە تەنها لە سێرڤەر کاردەکات.")
    game = spy_games.get(ctx.guild.id)
    if not game:
        e = discord.Embed(title="❌ No Lobby | هیچ لۆبییەک نییە", description="No active lobby to leave. | هیچ لۆبیی چالاکی بۆ جێهێشتن نییە.", color=0xed4245)
        return await ctx.send(embed=e)
    if game["started"]:
        e = discord.Embed(title="🚫 Game Running | یاری بەردەوامە", description="Can't leave mid-game! Use `$spystop` to end it. | ناتوانیت لە ناوەندی یاری بچیت! `$spystop` بەکارهێنە بۆ کۆتایی.", color=0xed4245)
        return await ctx.send(embed=e)
    player = next((p for p in game["players"] if p.id == ctx.author.id), None)
    if not player:
        e = discord.Embed(title="⚠️ Not in Lobby | لە لۆبیدا نییت", description="You're not in the lobby! | تۆ لە لۆبیدا نییت!", color=0xfee75c)
        return await ctx.send(embed=e)
    game["players"].remove(player)
    remaining = len(game["players"])
    e = discord.Embed(
        title="🚪 Agent Left | ئاجێنت چوو",
        description=f"**{ctx.author.display_name}** left the lobby. | لۆبی جێهێشت.\n`{remaining}` player(s) remaining. | یاریزان ماوەتەوە.",
        color=0x2b2d31,
    )
    e.set_footer(text="$spyjoin to rejoin • $spystart when ready | $spyjoin بۆ بەشداریکردنی دووبارە • $spystart کاتێک ئامادەیت")
    await ctx.send(embed=e)
    if not game["players"]:
        spy_games.pop(ctx.guild.id, None)
        e2 = discord.Embed(
            title="💀 Lobby Empty — Disbanded | لۆبی بەتاڵ — هەڵوەشاندرایەوە",
            description="No players remain. Game cancelled. | هیچ یاریزانێک نەماوە. یاری هەڵوەشاندرایەوە.\nUse `$spyjoin` to start fresh! | `$spyjoin` بەکارهێنە بۆ دەستپێکردنی نوێ!",
            color=0x2b2d31,
        )
        await ctx.send(embed=e2)


@bot.command(name="spystatus")
async def spy_status(ctx):
    if not ctx.guild:
        return await ctx.send("This command only works in a server. | ئەم فەرمانە تەنها لە سێرڤەر کاردەکات.")
    game = spy_games.get(ctx.guild.id)
    if not game:
        e = discord.Embed(title="❌ No Active Game | هیچ یارییەکی چالاکی نییە", description="No Spy Game running.\nUse `$spyjoin` to start one! | `$spyjoin` بەکارهێنە بۆ دەستپێکردن!", color=0xed4245)
        return await ctx.send(embed=e)

    if game["started"] and game.get("voting"):
        phase, color = "🔴 Voting Phase | قۆناغی دەنگدان",     0xfee75c
    elif game["started"]:
        phase, color = "🟢 Discussion — Round in progress | نووسراوە — دوور بەردەوامە", 0x57f287
    else:
        phase, color = "🟡 Lobby — Waiting for players | لۆبی — چاوەڕوانی یاریزانان",    0x5865f2

    roster = "\n".join(
        f"{SPY_MEDALS[i]} **{p.display_name}**" for i, p in enumerate(game["players"])
    ) or "*No players yet | هێشتا هیچ یاریزانێک نییە*"

    e = discord.Embed(title="🕵️ SPY GAME — STATUS REPORT | ڕاپۆرتی دۆخ", color=color)
    e.add_field(name="📡 Phase | قۆناغ",   value=phase,                             inline=True)
    e.add_field(name="👥 Agents | ئاجێنتەکان",  value=f"**{len(game['players'])}**",     inline=True)
    e.add_field(name="\u200b",     value="\u200b",                          inline=True)
    e.add_field(name="🗂️ Roster | لیستی یاریزانان", value=roster,                            inline=False)
    if not game["started"]:
        cnt    = len(game["players"])
        needed = max(0, 3 - cnt)
        bar    = "🟩" * min(cnt, 3) + "⬛" * max(0, 3 - cnt)
        e.add_field(
            name="🎯 Readiness | ئامادەیی",
            value=f"{bar} {'✅ Ready to start! | ئامادەی دەستپێکردن!' if not needed else f'Need **{needed}** more | پێویستمانە بە **{needed}** تری'}",
            inline=False,
        )
    e.set_footer(text="$spyjoin • $spystart • $spyvote • $spystop • $spyleave")
    await ctx.send(embed=e)

# --- MUSIC ---

import yt_dlp as _ytdlp_lib

_music_log = logging.getLogger("music")
logging.getLogger("yt_dlp").setLevel(logging.ERROR)

_pool = _cf.ThreadPoolExecutor(max_workers=4, thread_name_prefix="music")

_YDL_OPTS = {
    # Prefer direct HTTP streams; fallback to adaptive only if nothing else works
    "format": "bestaudio[protocol=https]/bestaudio[protocol=http]/bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "geo_bypass": True,
    "nocheckcertificate": True,
    "ignoreerrors": True,
    "source_address": "0.0.0.0",
    "socket_timeout": 15,
    "retries": 3,
    "extractor_retries": 3,
    "http_chunk_size": 1048576,
}

# Default User-Agent FFmpeg will send when no headers are provided
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

def _build_ffmpeg_opts(http_headers: dict) -> dict:
    """Build FFmpeg options, injecting yt-dlp HTTP headers so CDNs accept the connection."""
    h = dict(http_headers or {})
    h.setdefault("User-Agent", _DEFAULT_UA)
    # Format as FFmpeg -headers value: each header on its own line ending with \r\n
    header_block = "".join(f"{k}: {v}\r\n" for k, v in h.items())
    return {
        "before_options": (
            f"-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 "
            f"-nostdin -loglevel warning "
            f"-headers \"{header_block}\""
        ),
        "options": "-vn",
    }

_mcache: dict = {}
_MCACHE_MAX = 60

# ── Guild state ────────────────────────────────────────────────────────────────

class GuildPlayer:
    __slots__ = ("queue", "current", "volume", "loop", "loop_queue", "start_time")
    def __init__(self):
        self.queue:       list  = []
        self.current:     dict  = None
        self.volume:      float = 0.80
        self.loop:        bool  = False
        self.loop_queue:  bool  = False
        self.start_time:  float = 0.0

_players: dict = {}

def get_player(guild_id: int) -> GuildPlayer:
    if guild_id not in _players:
        _players[guild_id] = GuildPlayer()
    return _players[guild_id]

# ── yt-dlp helpers ─────────────────────────────────────────────────────────────

def _ydl_extract(full_query: str):
    try:
        with _ytdlp_lib.YoutubeDL(_YDL_OPTS) as ydl:
            info = ydl.extract_info(full_query, download=False)
        if not info:
            return None
        if "entries" in info:
            info = next((e for e in info["entries"] if e), None)
        if not info:
            return None
        # Try top-level url first; then walk formats picking best audio bitrate
        stream = info.get("url")
        headers = dict(info.get("http_headers") or {})
        if not stream:
            for fmt in sorted(info.get("formats") or [], key=lambda f: f.get("abr") or 0, reverse=True):
                if fmt.get("acodec", "none") != "none" and fmt.get("url"):
                    stream = fmt["url"]
                    # Use format-level headers if present (they may differ from top-level)
                    if fmt.get("http_headers"):
                        headers = dict(fmt["http_headers"])
                    break
        if not stream:
            return None
        info["_stream_url"] = stream
        info["_http_headers"] = headers
        return info
    except Exception as exc:
        _music_log.debug("ydl error (%s): %s", full_query[:50], exc)
        return None

def _spotify_title(spotify_url: str) -> str:
    """Use Spotify's public oEmbed endpoint (no API key needed) to get track title."""
    import urllib.request, urllib.parse
    try:
        url = f"https://open.spotify.com/oembed?url={urllib.parse.quote(spotify_url, safe='')}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read())
        return data.get("title", "").strip() or ""
    except Exception:
        return ""

def _friendly_source(info: dict) -> str:
    """Return a clean platform name from yt-dlp extractor info."""
    ext = (info.get("extractor_key") or info.get("extractor") or "").lower()
    mapping = {
        "youtube":      "YouTube",
        "soundcloud":   "SoundCloud",
        "spotify":      "Spotify",
        "bandcamp":     "Bandcamp",
        "twitch":       "Twitch",
        "vimeo":        "Vimeo",
        "dailymotion":  "Dailymotion",
        "tiktok":       "TikTok",
        "twitter":      "Twitter/X",
        "instagram":    "Instagram",
        "facebook":     "Facebook",
        "reddit":       "Reddit",
        "bilibili":     "Bilibili",
        "niconico":     "NicoNico",
        "mixcloud":     "Mixcloud",
        "audiomack":    "Audiomack",
        "rumble":       "Rumble",
    }
    for key, label in mapping.items():
        if key in ext:
            return label
    uploader = info.get("extractor_key") or "Direct"
    return uploader

async def fetch_track(query: str):
    loop = asyncio.get_event_loop()
    key = query.strip().lower()
    if key in _mcache:
        return _mcache[key], ""
    is_url = query.startswith(("http://", "https://"))

    # ── Spotify URL: use public oEmbed to get title → search YouTube ──────────
    if is_url and "spotify.com" in query:
        search_q = await loop.run_in_executor(_pool, lambda: _spotify_title(query))
        if not search_q:
            return None, "Couldn't read that Spotify link. Try pasting the song name instead. | ئەم لینکی Spotify نەتوانرا بخوێنرێتەوە. ناوی گۆرانیەکە بنووسەرەوە."
        yt_res = await loop.run_in_executor(_pool, lambda: _ydl_extract("ytsearch1:" + search_q))
        if isinstance(yt_res, dict):
            track = _build_track(yt_res, source="Spotify→YouTube")
            _mcache_put(key, track)
            return track, ""
        sc_res = await loop.run_in_executor(_pool, lambda: _ydl_extract("scsearch1:" + search_q))
        if isinstance(sc_res, dict):
            track = _build_track(sc_res, source="Spotify→SoundCloud")
            _mcache_put(key, track)
            return track, ""
        return None, f"Found Spotify track **{search_q}** but couldn't stream it. | گۆرانیەکە لە Spotify دۆزرایەوە بەڵام ستریمی نەدۆزرایەوە."

    # ── Any other direct URL (YouTube, SoundCloud, Twitch, Bandcamp, etc.) ─────
    if is_url:
        info = await loop.run_in_executor(_pool, lambda: _ydl_extract(query))
        if info:
            src = _friendly_source(info)
            track = _build_track(info, source=src)
            _mcache_put(key, track)
            return track, ""
        return None, "Could not load that URL. It may be private, age-restricted, or from an unsupported site. | ئەم لینکە نەتوانرا بار بکرێت. تایبەتی، مەوایپێکراو، یان مەرجبەندی تەمەنی بۆ کراوە."

    # ── Search term: YouTube + SoundCloud in parallel ─────────────────────────
    yt_fut = loop.run_in_executor(_pool, lambda: _ydl_extract("ytsearch1:" + query))
    sc_fut = loop.run_in_executor(_pool, lambda: _ydl_extract("scsearch1:" + query))
    yt_res, sc_res = await asyncio.gather(yt_fut, sc_fut, return_exceptions=True)
    if isinstance(yt_res, dict):
        track = _build_track(yt_res, source="YouTube")
        _mcache_put(key, track)
        return track, ""
    if isinstance(sc_res, dict):
        track = _build_track(sc_res, source="SoundCloud")
        _mcache_put(key, track)
        return track, ""
    return None, "No results found on YouTube or SoundCloud. Try a different search or paste a direct link. | هیچ ئەنجامێک نەدۆزرایەوە. پەیڤی تر هەوڵبدەرەوە یان لینکی ڕاستەوخۆ بچسپێنە."

def _build_track(info: dict, source: str) -> dict:
    return {
        "title":        info.get("title", "Unknown Title | ناونەزانراو"),
        "url":          info["_stream_url"],
        "webpage_url":  info.get("webpage_url") or info.get("original_url", ""),
        "thumbnail":    info.get("thumbnail"),
        "duration":     info.get("duration"),
        "uploader":     info.get("uploader") or info.get("channel") or "Unknown Artist | هونەرمەندی نەزانراو",
        "source":       source,
        "requester":    None,
        "channel_id":   0,
        "http_headers": info.get("_http_headers") or {},
    }

def _mcache_put(key: str, value: dict):
    if len(_mcache) >= _MCACHE_MAX:
        del _mcache[next(iter(_mcache))]
    _mcache[key] = value

# ── Playback engine ────────────────────────────────────────────────────────────

def _fmt_dur(secs) -> str:
    if not secs:
        return "🔴 Live"
    s = int(secs)
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

def _after_track(guild_id: int, error):
    if error:
        _music_log.warning("Player error guild %s: %s", guild_id, error)
    asyncio.run_coroutine_threadsafe(_advance(guild_id), bot.loop)

async def _advance(guild_id: int):
    guild = bot.get_guild(guild_id)
    if not guild:
        return
    vc = guild.voice_client
    if not vc:
        return
    player = get_player(guild_id)
    if player.loop and player.current:
        track = player.current
    else:
        if player.loop_queue and player.current:
            player.queue.append(player.current)
        if not player.queue:
            player.current = None
            return
        track = player.queue.pop(0)
        player.current = track
    await _play_track(vc, player, track, guild_id)

async def _play_track(vc, player: GuildPlayer, track: dict, guild_id: int):
    # Always re-fetch a fresh stream URL right before playing.
    # yt-dlp stream URLs (YouTube especially) expire within seconds.
    loop = asyncio.get_event_loop()
    wp = track.get("webpage_url") or track.get("url", "")
    fresh = None
    if wp:
        fresh = await loop.run_in_executor(_pool, lambda: _ydl_extract(wp))
    if fresh:
        track["url"]          = fresh["_stream_url"]
        track["http_headers"] = fresh.get("_http_headers") or {}
        if fresh.get("thumbnail"):
            track["thumbnail"] = fresh["thumbnail"]
        if fresh.get("uploader") or fresh.get("channel"):
            track["uploader"] = fresh.get("uploader") or fresh.get("channel")
    else:
        # webpage_url failed — abort cleanly instead of letting FFmpeg crash
        _music_log.warning("Could not refresh stream URL for: %s", track.get("title"))
        ch = bot.get_channel(track.get("channel_id", 0))
        if ch:
            try:
                await ch.send(embed=_m_err(
                    f"⚠️ Couldn't load stream for **{track['title']}**, skipping… | نەتوانرا ستریمی **{track['title']}** بار بکرێت، تێدەپەڕێت…"
                ))
            except discord.Forbidden:
                pass
        await _advance(guild_id)
        return
    # Build FFmpeg options with the headers yt-dlp provided so CDNs accept the connection
    ffmpeg_opts = _build_ffmpeg_opts(track.get("http_headers") or {})
    try:
        src = discord.FFmpegPCMAudio(track["url"], **ffmpeg_opts)
        src = discord.PCMVolumeTransformer(src, volume=player.volume)
        vc.play(src, after=lambda e: _after_track(guild_id, e))
        player.start_time = time.time()
    except Exception as exc:
        _music_log.error("FFmpeg error for %s: %s", track.get("title"), exc)
        ch = bot.get_channel(track.get("channel_id", 0))
        if ch:
            try:
                await ch.send(embed=_m_err(
                    f"⚠️ FFmpeg failed for **{track['title']}**, skipping… | نەتوانرا **{track['title']}** بلێدرێت، تێدەپەڕێت…"
                ))
            except discord.Forbidden:
                pass
        await _advance(guild_id)
        return
    ch = bot.get_channel(track.get("channel_id", 0))
    if ch:
        try:
            await ch.send(embed=_np_embed(track, player, guild_id))
        except discord.Forbidden:
            pass

# ── Embed builders ─────────────────────────────────────────────────────────────

_SRC_COLOR = {
    "YouTube":           discord.Color.from_rgb(255, 0, 0),
    "SoundCloud":        discord.Color.from_rgb(255, 100, 0),
    "Spotify→YouTube":   discord.Color.from_rgb(30, 215, 96),
    "Spotify→SoundCloud":discord.Color.from_rgb(30, 215, 96),
    "Bandcamp":          discord.Color.from_rgb(29, 157, 118),
    "Twitch":            discord.Color.from_rgb(145, 70, 255),
    "Vimeo":             discord.Color.from_rgb(26, 183, 234),
    "Dailymotion":       discord.Color.from_rgb(0, 120, 220),
    "TikTok":            discord.Color.from_rgb(0, 0, 0),
    "Twitter/X":         discord.Color.from_rgb(29, 161, 242),
    "Mixcloud":          discord.Color.from_rgb(82, 46, 139),
    "Audiomack":         discord.Color.from_rgb(255, 165, 0),
    "Rumble":            discord.Color.from_rgb(133, 193, 77),
    "Direct":            discord.Color.from_rgb(88, 101, 242),
}
_SRC_EMOJI = {
    "YouTube":           "▶️",
    "SoundCloud":        "☁️",
    "Spotify→YouTube":   "🟢",
    "Spotify→SoundCloud":"🟢",
    "Bandcamp":          "🎸",
    "Twitch":            "🟣",
    "Vimeo":             "🎬",
    "Dailymotion":       "📺",
    "TikTok":            "🎵",
    "Twitter/X":         "🐦",
    "Mixcloud":          "🎛️",
    "Audiomack":         "🎧",
    "Rumble":            "📹",
}

def _np_embed(track: dict, player: GuildPlayer, guild_id: int) -> discord.Embed:
    src   = track.get("source", "")
    emoji = _SRC_EMOJI.get(src, "🎵")
    color = _SRC_COLOR.get(src, discord.Color.blurple())
    wp    = track.get("webpage_url", "")
    title = f"[{track['title']}]({wp})" if wp else track["title"]
    embed = discord.Embed(
        title=f"{emoji}  Now Playing | ئێستا دەلێدرێت",
        description=f"### {title}",
        color=color,
        timestamp=datetime.datetime.utcnow(),
    )
    if track.get("thumbnail"):
        embed.set_thumbnail(url=track["thumbnail"])
    dur       = _fmt_dur(track.get("duration"))
    vol       = int(player.volume * 100)
    q_len     = len(player.queue)
    elapsed   = time.time() - player.start_time
    total_sec = track.get("duration") or 0
    if total_sec > 0:
        pct    = min(elapsed / total_sec, 1.0)
        filled = int(pct * 18)
        bar    = "▓" * filled + "░" * (18 - filled)
        prog   = f"`{_fmt_dur(elapsed)} {bar} {dur}`"
    else:
        prog = f"`{dur}`"
    embed.add_field(name="⏱️ Progress | پێشکەوتن",   value=prog,                          inline=False)
    embed.add_field(name="🎤 Artist | هونەرمەند",    value=track.get("uploader", "?"),    inline=True)
    embed.add_field(name="🔈 Volume | دەنگ",         value=f"`{vol}%`",                    inline=True)
    embed.add_field(name="🔊 Source | سەرچاوە",      value=f"`{src}`",                     inline=True)
    loop_state = "🔁 Track | گۆرانی" if player.loop else ("🔄 Queue | ڕیز" if player.loop_queue else "➡️ Off | ناچالاک")
    embed.add_field(name="🔁 Loop | دووبارەکردنەوە", value=loop_state,                     inline=True)
    embed.add_field(name="📋 In Queue | لە ڕیزدا",  value=f"`{q_len} track(s)`",          inline=True)
    if track.get("requester"):
        embed.add_field(name="👤 Requested by | داواکراوە لەلایەن", value=f"<@{track['requester']}>", inline=True)
    if q_len:
        embed.add_field(name="⏭️ Up Next | دواتر", value=f"`{player.queue[0]['title'][:50]}`", inline=False)
    embed.set_footer(text="$skip • $pause • $stop • $queue • $np • $loop • $vol  |  تێپەڕاندن • وەستاندن • ڕاگرتن")
    return embed

def _queue_embed(player: GuildPlayer) -> discord.Embed:
    embed = discord.Embed(
        title="📋  Music Queue | ڕیزی مۆسیقا",
        color=discord.Color.blurple(),
        timestamp=datetime.datetime.utcnow(),
    )
    if player.current:
        src   = player.current.get("source", "")
        emoji = _SRC_EMOJI.get(src, "🎵")
        dur   = _fmt_dur(player.current.get("duration"))
        loop_tag = " 🔁" if player.loop else ""
        embed.add_field(
            name=f"▶️  Now Playing | ئێستا دەلێدرێت{loop_tag}",
            value=f"{emoji} **{player.current['title'][:55]}** `[{dur}]`",
            inline=False,
        )
    if not player.queue:
        embed.add_field(
            name="📭  Queue is empty | ڕیزەکە بەتاڵە",
            value="Use `$play <song>` to add music! | `$play <گۆرانی>` بەکارهێنە!",
            inline=False,
        )
    else:
        shown = player.queue[:12]
        lines = []
        for i, t in enumerate(shown):
            src   = t.get("source", "")
            emoji = _SRC_EMOJI.get(src, "🎵")
            dur   = _fmt_dur(t.get("duration"))
            lines.append(f"`{i+1}.` {emoji} **{t['title'][:48]}** `[{dur}]`")
        if len(player.queue) > 12:
            lines.append(f"\n*…و {len(player.queue)-12} گۆرانیی تر | and {len(player.queue)-12} more tracks*")
        embed.add_field(name="📋  Up Next | دواتر", value="\n".join(lines), inline=False)
    total = sum(t.get("duration") or 0 for t in player.queue)
    vol   = int(player.volume * 100)
    lq    = "🔄 Queue loop ON | دووبارەکردنەوەی ڕیز" if player.loop_queue else ""
    embed.set_footer(text=f"{len(player.queue)} track(s) | گۆرانی  •  Total | کۆی گشتی: {_fmt_dur(total)}  •  Vol | دەنگ: {vol}%  {lq}")
    return embed

def _m_ok(msg: str, color=discord.Color.green()) -> discord.Embed:
    return discord.Embed(description=msg, color=color)

def _m_err(msg: str) -> discord.Embed:
    return discord.Embed(description=msg, color=discord.Color.red())

def _m_warn(msg: str) -> discord.Embed:
    return discord.Embed(description=msg, color=discord.Color.orange())

async def _ensure_voice(ctx):
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send(embed=_m_err("🔇  Join a voice channel first! | پێشتر بەشداری کەناڵی دەنگ بکە."))
        return None
    target = ctx.author.voice.channel
    vc = ctx.guild.voice_client
    if vc is None:
        try:
            vc = await target.connect(self_deaf=True)
        except Exception as exc:
            await ctx.send(embed=_m_err(f"❌  Could not join **{target.name}**: `{exc}`"))
            return None
    elif vc.channel != target:
        await vc.move_to(target)
    return vc

# ── Idle disconnect task ───────────────────────────────────────────────────────

@tasks.loop(minutes=5)
async def idle_check():
    for guild in bot.guilds:
        vc = guild.voice_client
        if vc and not vc.is_playing() and not vc.is_paused():
            player = get_player(guild.id)
            if not player.queue and not player.current:
                try:
                    await vc.disconnect()
                except Exception:
                    pass

# ── Music commands ─────────────────────────────────────────────────────────────

@bot.command(name="volume", aliases=["vol"])
async def volume(ctx, vol: int = None):
    player = get_player(ctx.guild.id)
    if vol is None:
        cur = int(player.volume * 100)
        await ctx.send(embed=_m_ok(f"🔈  Current volume | دەنگی ئێستا: **{cur}%**  •  `$volume <1-200>`", discord.Color.blurple()))
        return
    if not 1 <= vol <= 200:
        await ctx.send(embed=_m_err("⚠️  Volume must be between **1** and **200**. | دەنگ دەبێت لە نێوان **١** و **٢٠٠** بێت."))
        return
    player.volume = vol / 100
    vc = ctx.guild.voice_client
    if vc and vc.source and isinstance(vc.source, discord.PCMVolumeTransformer):
        vc.source.volume = player.volume
    bar = "🟩" * min(int(vol / 10), 10) + "⬛" * max(0, 10 - int(vol / 10))
    await ctx.send(embed=discord.Embed(title="🔈  Volume | دەنگ", description=f"{bar}\n**{vol}%**", color=discord.Color.green()))

@bot.command(name="loop")
async def loop_cmd(ctx, mode: str = "track"):
    player = get_player(ctx.guild.id)
    mode = mode.lower()
    if mode in ("track", "song", "t"):
        player.loop       = not player.loop
        player.loop_queue = False
        state = "enabled 🔁 | چالاک کرا" if player.loop else "disabled | ناچالاک کرا"
        await ctx.send(embed=_m_ok(f"🔁  Track loop **{state}**. | دووبارەکردنەوەی گۆرانی"))
    elif mode in ("queue", "q"):
        player.loop_queue = not player.loop_queue
        player.loop       = False
        state = "enabled 🔄 | چالاک کرا" if player.loop_queue else "disabled | ناچالاک کرا"
        await ctx.send(embed=_m_ok(f"🔄  Queue loop **{state}**. | دووبارەکردنەوەی ڕیز"))
    elif mode in ("off", "none", "0"):
        player.loop       = False
        player.loop_queue = False
        await ctx.send(embed=_m_ok("➡️  Loop **disabled**. | چرکەپشتیکردنەوە ناچالاک کرا."))
    else:
        await ctx.send(embed=_m_warn("Usage | بەکارهێنان: `$loop track` | `$loop queue` | `$loop off`"))

@bot.command(name="qremove", aliases=["qrm"])
async def qremove(ctx, index: int):
    player = get_player(ctx.guild.id)
    if not player.queue:
        await ctx.send(embed=_m_warn("⚠️  The queue is empty. | ڕیزەکە بەتاڵە."))
        return
    if not 1 <= index <= len(player.queue):
        await ctx.send(embed=_m_err(f"⚠️  Invalid position. Queue has **{len(player.queue)}** track(s). | ژمارەی نادروستە."))
        return
    removed = player.queue.pop(index - 1)
    await ctx.send(embed=_m_ok(f"🗑️  Removed **{removed['title']}** from the queue. | لە ڕیزەکە لابرا.", discord.Color.orange()))

@bot.command()
async def snipe(ctx):
    s = sniped.get(ctx.channel.id)
    if not s:
        await ctx.send("Nothing to snipe here. | هیچ شتێک بۆ سنایپکردن ئێرە نییە.")
        return
    embed = discord.Embed(description=s["content"], color=discord.Color.red())
    embed.set_author(name=s["author"], icon_url=s["author_avatar"])
    embed.set_footer(text=f"Deleted | سڕدرایەوە • {time.strftime('%H:%M:%S', time.gmtime(s['time']))} UTC")
    await ctx.send(embed=embed)

@bot.command()
async def editsnipe(ctx):
    s = edit_sniped.get(ctx.channel.id)
    if not s:
        await ctx.send("No edits to snipe here. | هیچ دەستکاریکردنێک بۆ سنایپکردن ئێرە نییە.")
        return
    embed = discord.Embed(color=discord.Color.orange())
    embed.set_author(name=s["author"], icon_url=s["author_avatar"])
    embed.add_field(name="Before | پێش", value=s["before"][:1024], inline=False)
    embed.add_field(name="After | دوا", value=s["after"][:1024], inline=False)
    embed.set_footer(text=f"Edited | دەستکاریکرا • {time.strftime('%H:%M:%S', time.gmtime(s['time']))} UTC")
    await ctx.send(embed=embed)

@bot.command()
async def membercount(ctx):
    await ctx.send(f"👥 **{ctx.guild.member_count}** members | ئەندام")

@bot.command()
async def humancount(ctx):
    n = sum(1 for m in ctx.guild.members if not m.bot)
    await ctx.send(f"👤 **{n}** humans | مرۆڤ")

@bot.command()
async def botcount(ctx):
    n = sum(1 for m in ctx.guild.members if m.bot)
    await ctx.send(f"🤖 **{n}** bots | بۆت")

@bot.command()
async def channelcount(ctx):
    await ctx.send(f"📺 **{len(ctx.guild.channels)}** total channels | کۆی کەناڵەکان")

@bot.command()
async def textchannels(ctx):
    await ctx.send(f"💬 **{len(ctx.guild.text_channels)}** text channels | کەناڵی نووسراوە")

@bot.command()
async def voicechannels(ctx):
    await ctx.send(f"🎙️ **{len(ctx.guild.voice_channels)}** voice channels | کەناڵی دەنگ")

@bot.command()
async def rolecount(ctx):
    await ctx.send(f"🏷️ **{len(ctx.guild.roles)}** roles | رۆڵ")

@bot.command()
async def emojis(ctx):
    e = ctx.guild.emojis
    if not e:
        await ctx.send("No custom emojis. | هیچ ئیمۆجیی تایبەتی نییە.")
        return
    text = " ".join(str(em) for em in e[:30])
    extra = "" if len(e) <= 30 else f"\n...and {len(e) - 30} more | و {len(e) - 30} تری"
    await ctx.send(text + extra)

@bot.command()
async def emojicount(ctx):
    await ctx.send(f"😀 **{len(ctx.guild.emojis)}** custom emojis | ئیمۆجیی تایبەت")

@bot.command()
async def boostcount(ctx):
    await ctx.send(f"🚀 Boost level | ئاستی بووست **{ctx.guild.premium_tier}** with | بە **{ctx.guild.premium_subscription_count}** boosts | بووست")

@bot.command()
async def owner(ctx):
    o = ctx.guild.owner
    await ctx.send(f"👑 Server owner | خاوەنی سێرڤەر: {o.mention if o else 'unknown | نەزانراو'}")

@bot.command()
async def oldestmember(ctx):
    members = [m for m in ctx.guild.members if not m.bot and m.joined_at]
    if not members:
        await ctx.send("No members found. | هیچ ئەندامێک نەدۆزرایەوە.")
        return
    m = min(members, key=lambda x: x.joined_at)
    await ctx.send(f"🕰️ Oldest member | کۆنترین ئەندام: {m.mention} (joined | بەشداریکرد {m.joined_at.strftime('%Y-%m-%d')})")

@bot.command()
async def newestmember(ctx):
    members = [m for m in ctx.guild.members if not m.bot and m.joined_at]
    if not members:
        await ctx.send("No members found. | هیچ ئەندامێک نەدۆزرایەوە.")
        return
    m = max(members, key=lambda x: x.joined_at)
    await ctx.send(f"🌱 Newest member | نوێترین ئەندام: {m.mention} (joined | بەشداریکرد {m.joined_at.strftime('%Y-%m-%d')})")

@bot.command()
async def servericon(ctx):
    if ctx.guild.icon:
        embed = discord.Embed(title=f"{ctx.guild.name} icon | ئایکۆنی سێرڤەر")
        embed.set_image(url=ctx.guild.icon.url)
        await ctx.send(embed=embed)
    else:
        await ctx.send("This server has no icon. | ئەم سێرڤەرە هیچ ئایکۆنێکی نییە.")

@bot.command()
async def serverbanner(ctx):
    if ctx.guild.banner:
        embed = discord.Embed(title=f"{ctx.guild.name} banner | بانەری سێرڤەر")
        embed.set_image(url=ctx.guild.banner.url)
        await ctx.send(embed=embed)
    else:
        await ctx.send("This server has no banner. | ئەم سێرڤەرە هیچ بانەرێکی نییە.")

# --- USER STATS ---

@bot.command()
async def userbanner(ctx, member: discord.Member = None):
    member = member or ctx.author
    user = await bot.fetch_user(member.id)
    if user.banner:
        embed = discord.Embed(title=f"{member.display_name}'s banner | بانەری {member.display_name}")
        embed.set_image(url=user.banner.url)
        await ctx.send(embed=embed)
    else:
        await ctx.send(f"{member.display_name} has no banner set. | {member.display_name} هیچ بانەرێکی دانراو نییە.")

@bot.command()
async def joinposition(ctx, member: discord.Member = None):
    member = member or ctx.author
    members = sorted(
        [m for m in ctx.guild.members if m.joined_at],
        key=lambda x: x.joined_at,
    )
    pos = members.index(member) + 1 if member in members else "?"
    await ctx.send(f"{member.mention} joined as member | بەشداری کرد وەک ئەندامی **#{pos}**")

@bot.command()
async def accountage(ctx, member: discord.Member = None):
    member = member or ctx.author
    days = (datetime.datetime.now(datetime.timezone.utc) - member.created_at).days
    await ctx.send(f"{member.mention}'s account is | ئەکاونتی **{days}** days | رۆژ old | کۆنە.")

@bot.command()
async def myid(ctx):
    await ctx.send(f"Your ID | ناسنامەت: `{ctx.author.id}`")

@bot.command(name="mention")
async def mention_cmd(ctx, member: discord.Member):
    await ctx.send(f"`{member.mention}`")

@bot.command()
async def myroles(ctx):
    roles = [r.mention for r in ctx.author.roles if r.name != "@everyone"]
    await ctx.send("Your roles | رۆڵەکانت: " + (", ".join(roles) if roles else "none | هیچ"))

@bot.command()
async def perms(ctx, member: discord.Member = None):
    member = member or ctx.author
    perms_list = [n.replace("_", " ").title() for n, v in member.guild_permissions if v]
    await ctx.send(
        f"**{member.display_name}**'s permissions | مووچەکانی: " +
        (", ".join(perms_list[:25]) + ("..." if len(perms_list) > 25 else "") if perms_list else "none | هیچ")
    )

@bot.command()
async def isbot(ctx, member: discord.Member):
    await ctx.send(f"{'🤖 Yes, ' + member.display_name + ' is a bot. | بەڵێ، بۆتە.' if member.bot else '👤 No, ' + member.display_name + ' is human. | نەخێر، مرۆڤە.'}")

# --- CHANNEL MANAGEMENT ---

@bot.command()
@commands.has_permissions(manage_channels=True)
async def lock(ctx):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = False
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.send("🔒 Channel locked. | کەناڵ داخراو.")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def unlock(ctx):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.send_messages = None
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.send("🔓 Channel unlocked. | کەناڵ کراوەیە.")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def slowmode(ctx, seconds: int):
    if seconds < 0 or seconds > 21600:
        await ctx.send("Pick 0-21600 seconds. | لە ٠ بۆ ٢١٦٠٠ چرکە هەڵبژێرە.")
        return
    await ctx.channel.edit(slowmode_delay=seconds)
    await ctx.send(f"🐌 Slowmode set to | مۆدی هێواشکردن دانرا بۆ **{seconds}s**.")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def renamechannel(ctx, *, name: str):
    old = ctx.channel.name
    await ctx.channel.edit(name=name)
    await ctx.send(f"Renamed | ناو گۆڕدرا `#{old}` to | بۆ `#{name}`.")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def settopic(ctx, *, topic: str):
    await ctx.channel.edit(topic=topic)
    await ctx.send("📝 Topic updated. | بابەتەکە نوێکرایەوە.")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def hidechannel(ctx):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.view_channel = False
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.send("🙈 Channel hidden from @everyone. | کەناڵ شاردرایەوە لە @everyone.")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def showchannel(ctx):
    overwrite = ctx.channel.overwrites_for(ctx.guild.default_role)
    overwrite.view_channel = None
    await ctx.channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
    await ctx.send("👀 Channel visible. | کەناڵ بەرچاوە.")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def nuke(ctx):
    pos = ctx.channel.position
    new_ch = await ctx.channel.clone(reason=f"Nuked by | نوکەکرا لەلایەن {ctx.author}")
    await new_ch.edit(position=pos)
    await ctx.channel.delete()
    await new_ch.send(f"💥 Channel nuked by | کەناڵ نوکەکرا لەلایەن {ctx.author.mention}!")

# --- VOICE MANAGEMENT ---

@bot.command()
@commands.has_permissions(mute_members=True)
async def vcmute(ctx, member: discord.Member):
    await member.edit(mute=True)
    await ctx.send(f"🔇 VC-muted | بێدەنگ کرا لە دەنگدا {member.mention}.")

@bot.command()
@commands.has_permissions(mute_members=True)
async def vcunmute(ctx, member: discord.Member):
    await member.edit(mute=False)
    await ctx.send(f"🔊 VC-unmuted | بێدەنگی لادرا لە دەنگدا {member.mention}.")

@bot.command()
@commands.has_permissions(deafen_members=True)
async def vcdeafen(ctx, member: discord.Member):
    await member.edit(deafen=True)
    await ctx.send(f"🔇 Deafened | گوێ داخرا {member.mention}.")

@bot.command()
@commands.has_permissions(deafen_members=True)
async def vcundeafen(ctx, member: discord.Member):
    await member.edit(deafen=False)
    await ctx.send(f"🔉 Undeafened | گوێ کرایەوە {member.mention}.")

@bot.command()
@commands.has_permissions(move_members=True)
async def vcdisconnect(ctx, member: discord.Member):
    await member.move_to(None)
    await ctx.send(f"⛔ Disconnected | پەیوەندی بڕدرا {member.mention} from voice. | لە دەنگ.")

# --- QUOTES, JOKES, ETC ---

@bot.command()
async def rpsls(ctx, choice: str = None):
    options = ["rock", "paper", "scissors", "lizard", "spock"]
    if not choice or choice.lower() not in options:
        await ctx.send("Usage: `$rpsls <rock|paper|scissors|lizard|spock>` | بەکارهێنان: `$rpsls <بەردەوشک|کاغەز|مەقەس|مار|سپۆک>`")
        return
    user = choice.lower()
    bot_choice = random.choice(options)
    rules = {
        "rock": ["scissors", "lizard"],
        "paper": ["rock", "spock"],
        "scissors": ["paper", "lizard"],
        "lizard": ["paper", "spock"],
        "spock": ["rock", "scissors"],
    }
    if user == bot_choice:
        result = "Tie! | یەکسانە!"
    elif bot_choice in rules[user]:
        result = "You win! 🎉 | تۆ بردیت! 🎉"
    else:
        result = "I win! 🤖 | من بردم! 🤖"
    await ctx.send(f"You | تۆ: **{user}**\nMe | من: **{bot_choice}**\n{result}")

@bot.command()
async def firstmessage(ctx):
    async for m in ctx.channel.history(limit=1, oldest_first=True):
        await ctx.send(f"📜 First message in | یەکەمین پەیام لە #{ctx.channel.name}: {m.jump_url}")
        return
    await ctx.send("No messages found. | هیچ پەیامێک نەدۆزرایەوە.")

@bot.command()
async def vote(ctx, *, question: str):
    msg = await ctx.send(f"📊 **{question}**")
    for emo in ("👍", "👎", "🤷"):
        await msg.add_reaction(emo)

@bot.command()
async def suggest(ctx, *, suggestion: str):
    embed = discord.Embed(title="💡 Suggestion | پێشنیار", description=suggestion[:4000], color=discord.Color.teal())
    embed.set_author(name=str(ctx.author), icon_url=ctx.author.display_avatar.url)
    msg = await ctx.send(embed=embed)
    for emo in ("👍", "👎"):
        await msg.add_reaction(emo)

# --- THE COOLER AFK COMMAND ---

@bot.command()
async def afk(ctx, *, reason: str = "AFK"):
    if ctx.guild is None:
        await ctx.send("AFK only works in a server. | AFK تەنها لە سێرڤەر کاردەکات.")
        return

    key = (ctx.guild.id, ctx.author.id)
    now = time.time()
    if key in afk_cooldowns and (now - afk_cooldowns[key]) < 60:
        remaining = int(60 - (now - afk_cooldowns[key]))
        embed = discord.Embed(
            title="⏰ Slow down! | هێواش بکەرەوە!",
            description=f"You can change your AFK status again in **{remaining}s**. | دەتوانیت دۆخی AFK خۆت بگۆڕیت لە **{remaining}چ** دیکەدا.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return

    old_nick = ctx.author.nick

    new_nick = f"💤 {ctx.author.display_name}"[:32]
    try:
        await ctx.author.edit(nick=new_nick)
    except (discord.Forbidden, discord.HTTPException):
        pass

    image_url = None
    if ctx.message.attachments:
        for att in ctx.message.attachments:
            if att.content_type and "image" in att.content_type:
                image_url = att.url
                break

    try:
        status_text = f"$afk {reason}"[:128]
        new_activities = [discord.CustomActivity(name=status_text)]
        for a in ctx.author.activities:
            if not isinstance(a, discord.CustomActivity):
                new_activities.append(a)
        await ctx.author.edit(activities=new_activities)
    except Exception:
        pass

    afk_since = time.time()
    afk_users[key] = {
        "reason": reason,
        "since": afk_since,
        "old_nick": old_nick,
        "image_url": image_url
    }
    afk_cooldowns[key] = now
    save_afk()

    afk_moods = ["🌙", "😴", "💤", "🛌", "🎧", "🌌", "☕", "🔇"]
    mood = random.choice(afk_moods)

    embed = discord.Embed(
        title=f"{mood}  Gone AFK | ڕوحستی",
        color=discord.Color.from_rgb(88, 101, 242),
        timestamp=datetime.datetime.utcfromtimestamp(afk_since)
    )
    embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)
    embed.add_field(name="📝 Reason | هۆکار", value=f"*{reason}*", inline=False)
    embed.add_field(name="🕐 AFK Since | AFK لەکاتی", value=f"<t:{int(afk_since)}:R>", inline=True)
    embed.add_field(name="🔔 Pings? | پینگ؟", value="I'll notify others who ping you | ئاگادارکردنەوەی ئەوانەی پینگت دەکەن", inline=True)
    if image_url:
        embed.set_image(url=image_url)
    else:
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
    embed.set_footer(text="Send any message to come back • Your nickname & status are updated | هەر پەیامێک بنێرە بۆ گەڕانەوە • ناو و دۆخت نوێکراوەتەوە")
    await ctx.send(embed=embed)

@bot.command()
async def github(ctx, user: str):
    await ctx.send(f"🐙 https://github.com/{user}")

@bot.command()
async def youtube(ctx, *, query: str):
    q = query.replace(" ", "+")
    await ctx.send(f"▶️ https://www.youtube.com/results?search_query={q}")

@bot.command()
async def google(ctx, *, query: str):
    q = query.replace(" ", "+")
    await ctx.send(f"🔎 https://www.google.com/search?q={q}")

@bot.command()
async def lmgtfy(ctx, *, query: str):
    q = query.replace(" ", "+")
    await ctx.send(f"https://lmgtfy.app/?q={q}")

@bot.command()
async def serverid(ctx):
    await ctx.send(f"Server ID | ناسنامەی سێرڤەر: `{ctx.guild.id}`")

@bot.command()
async def channelid(ctx):
    await ctx.send(f"Channel ID | ناسنامەی کەناڵ: `{ctx.channel.id}`")

@bot.command()
async def messageid(ctx):
    await ctx.send(f"Your message ID | ناسنامەی پەیامەکەت: `{ctx.message.id}`")

@bot.command()
async def invite(ctx):
    try:
        inv = await ctx.channel.create_invite(max_age=3600, max_uses=1, reason="!invite")
        await ctx.send(f"📨 {inv.url}")
    except discord.Forbidden:
        await ctx.send("I don't have permission to create invites. | مووچەم نییە داواکاری دروست بکەم.")

@bot.command()
async def about(ctx):
    embed = discord.Embed(
        title="About | دەربارە",
        description="A multipurpose Discord bot built with discord.py — moderation, leveling, fun, games, music, economy and more. | بۆتێکی پرپشکی Discord دروستکراوە بە discord.py — کونترۆڵ، ئاست، کێف، یاری، مۆسیقا، ئابووری و زیاتر.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Commands | فەرمانەکان", value="Use `$help` to see them all. | `$help` بەکارهێنە بۆ بینینی هەموویان.", inline=False)
    embed.add_field(name="Uptime | ماوەی چالاکی", value=fmt_uptime(time.time() - START_TIME), inline=False)
    await ctx.send(embed=embed)

@bot.command(name="help")
async def help_cmd(ctx, *, command_name: str = None):
    """Show all NON-game commands. Use $helpgame for games, $helpall for everything."""
    if command_name:
        cmd = bot.get_command(command_name.lstrip("$").lower())
        if cmd is None:
            await ctx.send(
                f"No command named `{command_name}` found. "
                f"Use `$help`, `$helpgame`, or `$helpall`. | "
                f"هیچ فەرمانێک بە ناوی `{command_name}` نەدۆزرایەوە. "
                f"`$help`، `$helpgame`، یان `$helpall` بەکارهێنە."
            )
            return
        for category, items in HELP_CATEGORIES:
            for usage, desc in items:
                tokens = [t.lstrip("$").lower() for t in usage.split() if t.startswith("$")]
                if cmd.name.lower() in tokens or any(a.lower() in tokens for a in cmd.aliases):
                    embed = discord.Embed(
                        title=f"${cmd.name}",
                        description=f"**Usage | بەکارهێنان:** `{usage}`\n{desc}",
                        color=discord.Color.blurple()
                    )
                    embed.set_footer(text=f"Category | پۆل: {category}")
                    await ctx.send(embed=embed)
                    return
        embed = discord.Embed(title=f"${cmd.name}", description="(no help entry | هیچ زانیارییەک نییە)", color=discord.Color.blurple())
        await ctx.send(embed=embed)
        return

    non_game = [(c, i) for c, i in HELP_CATEGORIES if c not in GAME_CATEGORIES_KEYS]
    pages = _help_pages(
        bot, non_game,
        title=f"📖 {bot.user.name} — Commands | فەرمانەکان",
        subtitle=(
            "**Prefix:** `$`  ·  `$help <command>` for details\n"
            "**پێشگر:** `$`  ·  `$help <فەرمان>` بۆ وردەکاری\n\n"
            "🎮 For game commands use `$helpgame` | بۆ فەرمانەکانی یاری `$helpgame` بەکارهێنە\n"
            "📚 For ALL commands use `$helpall` | بۆ هەموو فەرمانەکان `$helpall` بەکارهێنە"
        )
    )
    pages[-1].set_footer(text=f"📜 $helpgame · $helpall · $help <cmd>")
    for page in pages:
        await ctx.send(embed=page)

@bot.command(name="helpgame", aliases=["hgame", "gamehelp", "ghelp"])
async def helpgame_cmd(ctx):
    """Show all Games & Fun commands."""
    game_cats = [(c, i) for c, i in HELP_CATEGORIES if c in GAME_CATEGORIES_KEYS]
    if not game_cats:
        await ctx.send("No game categories found. | هیچ پۆلێکی یاری نەدۆزرایەوە.")
        return
    pages = _help_pages(
        bot, game_cats,
        title="🎮 Game Commands | فەرمانەکانی یاری",
        subtitle=(
            "**Prefix:** `$`  ·  All game & fun commands below!\n"
            "**پێشگر:** `$`  ·  هەموو فەرمانەکانی یاری و کێف لە خوارەوە!\n\n"
            "📖 Other commands: `$help` | فەرمانەکانی تر: `$help`"
        )
    )
    pages[-1].set_footer(text="🎮 Game Commands · $help · $helpall")
    for page in pages:
        await ctx.send(embed=page)

@bot.command(name="helpall", aliases=["allhelp", "fullhelp"])
async def helpall_cmd(ctx):
    """Show every single command across all categories."""
    pages = _help_pages(
        bot, HELP_CATEGORIES,
        title=f"📚 {bot.user.name} — All Commands | هەموو فەرمانەکان",
        subtitle=(
            "**Prefix:** `$`  ·  Every command listed below.\n"
            "**پێشگر:** `$`  ·  هەموو فەرمانەکان لە خوارەوە تۆمارکراون.\n\n"
            "⚡ **XP:** 15–25/msg · 10/min voice · `level² × 100` total"
        )
    )
    total_cmds = len(bot.commands)
    pages[-1].set_footer(text=f"📜 {total_cmds} commands | فەرمان  ·  $help · $helpgame")
    for page in pages:
        await ctx.send(embed=page)

@bot.command()
@commands.has_role(secret_role)
async def secret(ctx):
    await ctx.send("Welcome to the club! | بەخێربووت بۆ کلوبەکە!")

@secret.error
async def secret_error(ctx, error):
    if isinstance(error, commands.MissingRole):
        await ctx.send("You do not have permission to do that! | مووچەت نییە ئەوە بکەیت!")

# 🌍 LANGUAGE COMMAND
@bot.command()
async def language(ctx, lang=None):
    if lang not in ["en", "ku"]:
        await ctx.send("Choose: `en` or `ku` | هەڵبژێرە: `en` یان `ku`")
        return

    user_langs[ctx.author.id] = lang

    if lang == "ku":
        await ctx.send("🇹🇯 زمانی بۆتەکە گۆڕدرا بۆ کوردی | Bot language changed to Kurdish")
    else:
        await ctx.send("🇺🇸 Bot language changed to English | زمانی بۆتەکە گۆڕدرا بۆ ئینگلیزی")

# ─────────────────────────────────────────────────────────────────────────────
# --- TICKET SYSTEM ---
# ─────────────────────────────────────────────────────────────────────────────

def build_ticket_panel_embed(guild_name: str) -> discord.Embed:
    embed = discord.Embed(
        color=0xFFD700,
        title=f"🎫 {guild_name} — پشتگیری | Support",
        description=(
            "🇬🇧 **Need help? Create a ticket!**\n"
            "🇮🇶 **بێنویست بە یارمەتیە؟ تیکەتێک دروست بکە!**\n\n"
            "──────────────────────\n\n"
            "🇬🇧 **English:**\n"
            "Click the button below to open a support ticket.\n"
            "Our team will respond as soon as possible.\n\n"
            "**What we can help with:**\n"
            "• General support and questions\n"
            "• Product purchases and pricing\n"
            "• Technical issues and bugs\n"
            "• Report rule violations\n"
            "• Any other inquiries\n\n"
            "**Response time:** Usually within 5–30 minutes\n\n"
            "──────────────────────\n\n"
            "🇮🇶 **کوردی:**\n"
            "کرتە بکەرە لەسەر دوگمەکە بۆ کردنەوەی تیکەتی پشتگیری.\n"
            "تیمەکەمان زووترین کات وەڵامت دەداتەوە.\n\n"
            "**چۆن یارمەتیت دەدەین:**\n"
            "• پشتگیری گشتی و پرسیارەکان\n"
            "• کریێنی بەرهەم و نرخەکان\n"
            "• کێشەی تەکنیکی و هەڵەکان\n"
            "• ڕاپۆرتکردنی پێشێلکاری یاساکان\n"
            "• هەر پرسیارێکی تر\n\n"
            "**کاتی وەڵام:** بە زۆری لە ماوەی 5–30 خوەلێک\n\n"
            "──────────────────────\n"
            f"*{guild_name} | پشتگیری بیشەی 24/7*"
        ),
        timestamp=datetime.datetime.utcnow(),
    )
    embed.set_footer(text=f"{guild_name} Support System")
    return embed


class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🎫 Create Ticket | تیکەت دروست بکە",
        style=discord.ButtonStyle.success,
        custom_id="ticket:create",
    )
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        cfg = get_ticket_cfg(guild.id)
        key = (str(guild.id), str(interaction.user.id))

        existing_cid = open_tickets_map.get(key)
        if existing_cid:
            existing_ch = guild.get_channel(int(existing_cid))
            if existing_ch:
                return await interaction.followup.send(
                    f"❌ تیکەتێکی کراوەت هەیە | You already have an open ticket: {existing_ch.mention}",
                    ephemeral=True,
                )
            else:
                open_tickets_map.pop(key, None)

        safe_name = "".join(c for c in interaction.user.name.lower() if c.isalnum())[:20] or "user"
        category = guild.get_channel(int(cfg["category_id"])) if cfg.get("category_id") else None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, read_message_history=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, manage_channels=True
            ),
        }
        staff_rid = cfg.get("staff_role_id")
        if staff_rid:
            staff_role = guild.get_role(int(staff_rid))
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True,
                    read_message_history=True, manage_messages=True,
                )

        try:
            ticket_ch = await guild.create_text_channel(
                name=f"ticket-{safe_name}",
                category=category,
                overwrites=overwrites,
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            return await interaction.followup.send(
                f"❌ نەتوانرا تیکەت دروست بکرێت | Could not create ticket: {e}", ephemeral=True
            )

        open_tickets_map[key] = ticket_ch.id
        save_open_tickets()

        ticket_embed = discord.Embed(
            color=0xFFD700,
            title=f"🎫 Ticket — {interaction.user.display_name}",
            description=(
                f"سڵاو {interaction.user.mention} 👋\n\n"
                f"🇬🇧 Welcome to your support ticket! Please describe your issue and a staff member will assist you shortly.\n\n"
                f"🇮🇶 بەخێربێیت بۆ تیکەتەکەت! تکایە کێشەکەت شرۆڤە بکە و ئەندامێکی تیم زووترین کات یارمەتیت دەدات.\n\n"
                f"──────────────────────\n"
                f"🔒 To close this ticket, click the button below.\n"
                f"🔒 بۆ داخستنی تیکەت، دوگمەکە دابگرە."
            ),
            timestamp=datetime.datetime.utcnow(),
        )
        ticket_embed.set_thumbnail(url=interaction.user.display_avatar.url)
        ticket_embed.set_footer(text=f"{guild.name} Support")

        mention_str = interaction.user.mention
        if staff_rid:
            mention_str += f" <@&{staff_rid}>"

        await ticket_ch.send(content=mention_str, embed=ticket_embed, view=TicketControlView())
        await interaction.followup.send(
            f"✅ تیکەتەکەت دروستکرا | Your ticket has been created: {ticket_ch.mention}",
            ephemeral=True,
        )
        await ticket_log(guild, f"🎫 **Ticket opened** by {interaction.user.mention} → {ticket_ch.mention}")


class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🔒 Close Ticket | داخستنی تیکەت",
        style=discord.ButtonStyle.danger,
        custom_id="ticket:close",
    )
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "🔒 داخستنی تیکەت لە 5 چرکەدا... | Closing ticket in 5 seconds...", ephemeral=False
        )
        for (gid, uid), cid in list(open_tickets_map.items()):
            if cid == interaction.channel_id:
                open_tickets_map.pop((gid, uid), None)
                break
        save_open_tickets()
        await ticket_log(
            interaction.guild,
            f"🔒 **Ticket closed** {interaction.channel.mention} by {interaction.user.mention}"
        )
        await asyncio.sleep(5)
        try:
            await interaction.channel.delete(reason="Ticket closed")
        except (discord.Forbidden, discord.HTTPException):
            pass

    @discord.ui.button(
        label="✋ Claim Ticket | وەرگرتنی تیکەت",
        style=discord.ButtonStyle.primary,
        custom_id="ticket:claim",
    )
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = get_ticket_cfg(interaction.guild.id)
        staff_rid = cfg.get("staff_role_id")
        if staff_rid:
            staff_role = interaction.guild.get_role(int(staff_rid))
            if staff_role and staff_role not in interaction.user.roles:
                return await interaction.response.send_message(
                    "❌ تەنها ستاف دەتوانێت تیکەت وەربگرێت | Only staff can claim tickets.",
                    ephemeral=True,
                )
        await interaction.response.send_message(
            f"✋ ئەم تیکەتە وەرگیرا لەلایەن | This ticket has been claimed by {interaction.user.mention}"
        )
        await ticket_log(
            interaction.guild,
            f"✋ **Ticket claimed** {interaction.channel.mention} by {interaction.user.mention}"
        )


# --- TICKET SETUP COMMANDS ---

@bot.command(name="setpanel", aliases=["ticketpanel"])
@commands.has_permissions(manage_guild=True)
async def setpanel(ctx, channel: discord.TextChannel = None):
    """Send the ticket panel embed to a channel."""
    if ctx.guild is None:
        await ctx.send("Server only. | تەنها لە سێرڤەر.")
        return
    target = channel or ctx.channel
    cfg = ticket_settings.setdefault(str(ctx.guild.id), {})
    cfg["panel_channel_id"] = target.id
    save_ticket_settings()

    embed = build_ticket_panel_embed(ctx.guild.name)
    await target.send(embed=embed, view=TicketPanelView())
    if target != ctx.channel:
        await ctx.send(
            f"✅ پانێلی تیکەت نێردرا بۆ {target.mention} | Ticket panel sent to {target.mention}.",
            delete_after=10,
        )
    try:
        await ctx.message.delete()
    except (discord.Forbidden, discord.HTTPException):
        pass

@bot.command(name="setstaffrole", aliases=["ticketstaffrole"])
@commands.has_permissions(manage_guild=True)
async def setstaffrole(ctx, role: discord.Role):
    """Set the staff role that can see and manage tickets."""
    if ctx.guild is None:
        return
    cfg = ticket_settings.setdefault(str(ctx.guild.id), {})
    cfg["staff_role_id"] = role.id
    save_ticket_settings()
    await ctx.send(
        f"✅ رۆڵی ستاف دانرا: {role.mention} | Staff role set to {role.mention}."
    )

@bot.command(name="setticketcategory", aliases=["ticketcategory"])
@commands.has_permissions(manage_guild=True)
async def setticketcategory(ctx, *, category_name: str):
    """Set the category where ticket channels are created."""
    if ctx.guild is None:
        return
    cat = discord.utils.get(ctx.guild.categories, name=category_name)
    if not cat:
        await ctx.send(f"❌ کاتێگۆری نەدۆزرایەوە | Category `{category_name}` not found.")
        return
    cfg = ticket_settings.setdefault(str(ctx.guild.id), {})
    cfg["category_id"] = cat.id
    save_ticket_settings()
    await ctx.send(
        f"✅ کاتێگۆری دانرا: **{cat.name}** | Ticket category set to **{cat.name}**."
    )

@bot.command(name="setticketlog", aliases=["ticketlog"])
@commands.has_permissions(manage_guild=True)
async def setticketlog(ctx, channel: discord.TextChannel = None):
    """Set the channel where ticket actions are logged."""
    if ctx.guild is None:
        return
    target = channel or ctx.channel
    cfg = ticket_settings.setdefault(str(ctx.guild.id), {})
    cfg["log_channel_id"] = target.id
    save_ticket_settings()
    await ctx.send(
        f"✅ کەناڵی لۆگی تیکەت دانرا: {target.mention} | Ticket log channel set to {target.mention}."
    )

@bot.command(name="ticketstatus")
@commands.has_permissions(manage_guild=True)
async def ticketstatus(ctx):
    """Show the current ticket system configuration."""
    if ctx.guild is None:
        return
    cfg = get_ticket_cfg(ctx.guild.id)
    staff_rid = cfg.get("staff_role_id")
    cat_id = cfg.get("category_id")
    log_cid = cfg.get("log_channel_id")
    panel_cid = cfg.get("panel_channel_id")

    staff_val = f"<@&{staff_rid}>" if staff_rid else "❌ Not set | دانەنراوە"
    cat_val = f"<#{cat_id}>" if cat_id else "❌ Not set | دانەنراوە"
    log_val = f"<#{log_cid}>" if log_cid else "❌ Not set | دانەنراوە"
    panel_val = f"<#{panel_cid}>" if panel_cid else "❌ Not set | دانەنراوە"
    open_count = sum(1 for (gid, _), _ in open_tickets_map.items() if gid == str(ctx.guild.id))

    embed = discord.Embed(
        color=0xFFD700,
        title="🎫 Ticket System Status | دۆخی سیستەمی تیکەت",
        timestamp=datetime.datetime.utcnow(),
    )
    embed.add_field(name="👮 Staff Role | رۆڵی ستاف", value=staff_val, inline=True)
    embed.add_field(name="📂 Category | کاتێگۆری", value=cat_val, inline=True)
    embed.add_field(name="📋 Log Channel | کەناڵی لۆگ", value=log_val, inline=True)
    embed.add_field(name="📢 Panel Channel | کەناڵی پانێل", value=panel_val, inline=True)
    embed.add_field(name="🎫 Open Tickets | تیکەتی کراوە", value=f"`{open_count}`", inline=True)
    embed.set_footer(text=f"Requested by {ctx.author.display_name}")
    await ctx.send(embed=embed)


# ─────────────────────────────────────────────────────────────────────────────
# --- GIVEAWAY SYSTEM ---
# ─────────────────────────────────────────────────────────────────────────────

def format_duration(seconds: int) -> str:
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s   = divmod(rem, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s or not parts: parts.append(f"{s}s")
    return " ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# --- GIVEAWAY SYSTEM ---
# ─────────────────────────────────────────────────────────────────────────────

GIVEAWAY_EMOJI = "🌟"


def _build_giveaway_embed(
    prize: str,
    host: discord.Member,
    winners_count: int,
    end_time: datetime.datetime,
    ended: bool = False,
    winners=None,
) -> discord.Embed:
    color = 0xFFD700
    if ended:
        title = "🎉 بەخشین کۆتایی هات! | Giveaway Ended!"
        desc = f"**خەڵات | Prize:** {prize}\n\n"
        if winners:
            mentions = " ".join(w.mention for w in winners)
            desc += f"🏆 **بەختەوار | Winner(s):** {mentions}"
        else:
            desc += "😢 هیچ بەشداریێک نەبوو — بەختەوار نییە | No valid entries — no winner."
    else:
        title = "🌟 G I V E A W A Y | بەخشین 🌟"
        desc = (
            f"**خەڵات | Prize:** {prize}\n\n"
            f"React with {GIVEAWAY_EMOJI} to enter! | بکرتە لەسەر {GIVEAWAY_EMOJI} بۆ بەشداری!\n\n"
            f"**کۆتایی | Ends:** <t:{int(end_time.timestamp())}:R>\n"
            f"**بەختەوار | Winners:** {winners_count}"
        )
    embed = discord.Embed(title=title, description=desc, color=color)
    embed.set_footer(
        text=f"لەلایەن | Hosted by {host.display_name}  •  "
             f"{'Ended' if ended else 'Ends'}: {end_time.strftime('%m/%d/%y %I:%M %p UTC')}"
    )
    embed.timestamp = end_time
    return embed


async def _end_giveaway(message_id: int, channel: discord.TextChannel):
    data = active_giveaways.pop(message_id, None)
    if not data:
        return
    try:
        msg = await channel.fetch_message(message_id)
    except discord.NotFound:
        return
    entrants = []
    for reaction in msg.reactions:
        if str(reaction.emoji) == GIVEAWAY_EMOJI:
            async for user in reaction.users():
                if not user.bot:
                    entrants.append(user)
            break
    winner_count = data["winners_count"]
    winners = random.sample(entrants, min(winner_count, len(entrants))) if entrants else []
    ended_embed = _build_giveaway_embed(
        data["prize"], data["host"], winner_count, data["end_time"], ended=True, winners=winners
    )
    await msg.edit(embed=ended_embed)
    if winners:
        winner_mentions = " ".join(w.mention for w in winners)
        await channel.send(
            f"🎊 دەستخۆش {winner_mentions}! تۆ **{data['prize']}** بەردیت!\n"
            f"🎊 Congratulations {winner_mentions}! You won **{data['prize']}**!\n"
            f"> [Jump to giveaway | بازدە بۆ بەخشینەکە]({msg.jump_url})"
        )
    else:
        await channel.send(
            f"😢 هیچ بەشداریێکی دروستی نەبوو بۆ **{data['prize']}**. بەختەوار هەڵنەبژێرا.\n"
            f"😢 No valid entries for **{data['prize']}**. No winner was selected."
        )


@bot.command(name="giveaway", aliases=["gstart", "gcreate"])
@commands.has_permissions(manage_guild=True)
async def giveaway_cmd(ctx, duration: str = None, winners: str = None, *, prize: str = None):
    """
    Start a giveaway.
    Usage: $giveaway <duration> <Nw> <prize>
    Examples: $giveaway 10m 1w Nitro Classic
              $giveaway 2h 3w Discord Nitro
    Duration: s=seconds m=minutes h=hours d=days
    """
    if not duration or not winners or not prize:
        embed = discord.Embed(
            title="❌ بەکارهێنانی هەڵە | Invalid Usage",
            description=(
                "**بەکارهێنانی دروست | Correct usage:**\n"
                "`$giveaway <duration> <Nw> <prize>`\n\n"
                "**نموونە | Examples:**\n"
                "`$giveaway 10m 1w Nitro Classic`\n"
                "`$giveaway 2h 3w Discord Nitro`\n\n"
                "**ماوە | Duration:** `s` `m` `h` `d`"
            ),
            color=0xFF4444,
        )
        await ctx.send(embed=embed, delete_after=15)
        return
    seconds = parse_duration(duration)
    if seconds is None:
        await ctx.send("❌ ماوەی نادروست | Invalid duration. Use formats like `10m`, `2h`, `1d`.", delete_after=10)
        return
    if not winners.lower().endswith("w") or not winners[:-1].isdigit():
        await ctx.send("❌ ژمارەی بەختەوار بنووسە وەک `1w`، `3w` | Winners must be like `1w`, `3w`.", delete_after=10)
        return
    winner_count = int(winners[:-1])
    if winner_count < 1:
        await ctx.send("❌ دەبێت لانیکەم ١ بەختەوار هەبێت | Must have at least 1 winner.", delete_after=10)
        return
    if seconds < 10:
        await ctx.send("❌ ماوە دەبێت لانیکەم ١٠ چرکە بێت | Duration must be at least 10 seconds.", delete_after=10)
        return
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        pass
    end_time = datetime.datetime.utcnow() + datetime.timedelta(seconds=seconds)
    embed = _build_giveaway_embed(prize, ctx.author, winner_count, end_time)
    giveaway_msg = await ctx.send(embed=embed)
    await giveaway_msg.add_reaction(GIVEAWAY_EMOJI)
    active_giveaways[giveaway_msg.id] = {
        "prize":         prize,
        "host":          ctx.author,
        "winners_count": winner_count,
        "end_time":      end_time,
        "channel_id":    ctx.channel.id,
        "message_id":    giveaway_msg.id,
    }
    await asyncio.sleep(seconds)
    await _end_giveaway(giveaway_msg.id, ctx.channel)


@bot.command(name="greroll")
@commands.has_permissions(manage_guild=True)
async def greroll(ctx, message_id: int = None):
    """Reroll a giveaway winner. | بەختەوارێکی نوێ هەڵبژێرە. Usage: $greroll <message_id>"""
    if not message_id:
        await ctx.send("❌ ناسنامەی پەیامی بەخشینەکە بدە | Provide the giveaway message ID. `$greroll <message_id>`", delete_after=10)
        return
    try:
        msg = await ctx.channel.fetch_message(message_id)
    except discord.NotFound:
        await ctx.send("❌ پەیامەکە نەدۆزرایەوە لەم کەناڵە | Message not found in this channel.", delete_after=10)
        return
    entrants = []
    for reaction in msg.reactions:
        if str(reaction.emoji) == GIVEAWAY_EMOJI:
            async for user in reaction.users():
                if not user.bot:
                    entrants.append(user)
            break
    if not entrants:
        await ctx.send("😢 هیچ بەشداریێکی دروستی نییە | No valid entries to reroll.", delete_after=10)
        return
    winner = random.choice(entrants)
    await ctx.send(
        f"🎊 بەختەوارەکەی نوێ: {winner.mention}! دەستخۆش!\n"
        f"🎊 New winner: {winner.mention}! Congratulations!\n"
        f"> [Jump to giveaway | بازدە بۆ بەخشینەکە]({msg.jump_url})"
    )


@bot.command(name="gend")
@commands.has_permissions(manage_guild=True)
async def gend(ctx, message_id: int = None):
    """Force-end an active giveaway. | بەخشینێک زووتر کۆتایی پێبهێنە. Usage: $gend <message_id>"""
    if not message_id:
        await ctx.send("❌ ناسنامەی پەیامی بەخشینەکە بدە | Provide the giveaway message ID. `$gend <message_id>`", delete_after=10)
        return
    if message_id not in active_giveaways:
        await ctx.send("❌ هیچ بەخشینی چالاکی نەدۆزرایەوە بە ئەو ناسنامەیە | No active giveaway found with that ID.", delete_after=10)
        return
    data = active_giveaways[message_id]
    channel = bot.get_channel(data["channel_id"])
    if channel:
        await _end_giveaway(message_id, channel)
    await ctx.send("✅ کۆتایی هێنرا بەخشینەکە | Giveaway ended early.", delete_after=5)


@bot.command(name="glist")
@commands.has_permissions(manage_guild=True)
async def glist(ctx):
    """List all active giveaways. | بەخشینە چالاکەکان نیشان بدە."""
    server_giveaways = {
        mid: data for mid, data in active_giveaways.items()
        if data.get("channel_id") and bot.get_channel(data["channel_id"]) and
        bot.get_channel(data["channel_id"]).guild.id == ctx.guild.id
    }
    if not server_giveaways:
        await ctx.send("📋 هیچ بەخشینی چالاکی نییە ئێستا | No active giveaways right now.")
        return
    embed = discord.Embed(
        title="🎉 بەخشینە چالاکەکان | Active Giveaways",
        color=0xF5A623,
        timestamp=datetime.datetime.utcnow(),
    )
    for msg_id, data in server_giveaways.items():
        embed.add_field(
            name=f"🌟 {data['prize']}",
            value=(
                f"🏆 بەختەوار | Winners: `{data['winners_count']}`\n"
                f"⏳ کۆتایی | Ends: <t:{int(data['end_time'].timestamp())}:R>\n"
                f"🔑 Message ID: `{msg_id}`"
            ),
            inline=False,
        )
    embed.set_footer(text=f"لەلایەن | Requested by {ctx.author.display_name}")
    await ctx.send(embed=embed)


@giveaway_cmd.error
@greroll.error
@gend.error
async def _giveaway_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ پێویستت بە مووچەی **Manage Server** هەیە | You need **Manage Server** permission.", delete_after=10)
    else:
        await ctx.send(f"❌ هەڵە | Error: {error}", delete_after=10)


# ─────────────────────────────────────────────────────────────────────────────
# --- BOMB NUMBER GAME ---
# ─────────────────────────────────────────────────────────────────────────────

BOOM_MESSAGES = [
    "💥 **BOOOOOM!** تۆ بۆمبەکەت دۆزیەوە! بەختەوارتر بە داهاتوودا | You found the bomb! Better luck next time, kaboom king 👑",
    "🧨 **KA-BOOOOM!** ئەوە بۆمبەکە بوو! | That was the bomb! You just blew up the whole server 😂",
    "💣 **BOOM!** بەڵێ... ئەوە بەتایبەتی بۆمبەکە بوو. | Yep... that was definitely the bomb. F in the chat 🙏",
    "💥 **TEQÎN HATE DÎTIN!** ژمارەی هەڵەت هەڵبژارد | You picked the wrong number, soldier 💀",
    "🔥 **BOOM!** بۆمبەکە ئەوێبوو و باشی کرت دایت | The bomb was RIGHT THERE and you still pressed it 😭",
    "💣 **KABOOM!** تۆ خۆت لەخۆت گوڕدی. بۆمبەکە سڵاوت دەدات | You played yourself. The bomb says hi 👋💥",
    "💥 **BOOM!** ئەوەی هەڵبژاردیت بۆمبەکەت بوو بە تەواوی 🪦",
    "🧨 **BOOM!** ژمارەی {bomb} ناویت تێیدا نووسرابوو 😂",
]

SAFE_MESSAGES = [
    "✅ **سەلامەتە! | Safe!** فوو... {number} بۆمب نەبوو. بەختەوار بوویت 😅",
    "😮‍💨 **سەلامەتە! | Safe!** نزیک بوو... {number} پاکە! بەردەوام بە!",
    "🟢 **سەلامەتە! | Safe!** {number} سەلامەتە. بەڵام بۆمبەکە هێشتا لەوێیە 👀",
    "😅 **ووه!** {number} مایەوە! {remaining} ژمارەی تر...",
    "✨ **بۆمب نییە ئێرە! | No bomb here!** {number} سەلامەتە. زیادە متمانە مەبە 😏",
]

WIN_MESSAGES = [
    "🏆 **بردیت! | YOU WIN!** تۆ لە بۆمبی ژمارەی {bomb} فرییت! بەتوانا! 🎉",
    "🎉 **بەختەوار! | WINNER!** هەموو ژمارەکانت بە سەلامەتی تێپەڕاند! بۆمبەکە {bomb} بوو!",
    "👑 **شامپیۆن! | CHAMPION!** هەموو ژمارەکانت دابەزاند و مایتەوە! بۆمبەکە {bomb} بوو!",
    "🥇 **ناباوەڕ! | INCREDIBLE!** تۆ یاری بۆمب بردیت! شاراوە لە {bomb} بوو. GG EZ! 😎",
]


def build_bomb_grid(safe_picks, remaining):
    grid = ""
    for n in range(1, 11):
        if n in safe_picks:
            grid += f"~~`{n}`~~ "
        elif n in remaining:
            grid += f"`{n}` "
    return grid.strip()


@bot.command(name="td")
async def truth_or_dare(ctx):
    """Start a 1v1 Truth or Dare game. | یارییەکی ١ بەرامبەر ١ راستی یان جوریمەی دەستپێ بکە."""
    cid = ctx.channel.id

    if cid in td_sessions:
        await ctx.send(
            "⚠️ هەر یارییەکی Truth or Dare چالاک هەیە لەم کەناڵەدا! | "
            "There\'s already an active Truth or Dare game in this channel! Wait for it to finish.",
            delete_after=10
        )
        return

    session = TDSession(ctx.author.id, cid)
    td_sessions[cid] = session

    view  = TDLobbyView(cid, ctx.author.id)
    embed = _td_lobby_embed(ctx.author, "waiting")
    lobby_msg = await ctx.send(embed=embed, view=view)
    session.lobby_msg = lobby_msg

    try:
        await ctx.message.delete()
    except Exception:
        pass

# ─── $tdstop — Force stop a Truth or Dare game ────────────────────────────────
@bot.command(name="tdstop")
@commands.has_permissions(manage_messages=True)
async def td_stop(ctx):
    """Force-stop a Truth or Dare game in this channel. | یارییەکی Truth or Dare لەم کەناڵەدا بوەستێنە."""
    cid = ctx.channel.id
    if cid not in td_sessions:
        await ctx.send(
            "❌ هیچ یارییەکی چالاکی Truth or Dare نییە لەم کەناڵەدا. | "
            "No active Truth or Dare game in this channel.",
            delete_after=8
        )
        return
    td_sessions.pop(cid, None)
    embed = discord.Embed(
        title="🛑 یاری هەڵوەشایەوە | Game Stopped",
        description=(
            f"یارییەکی Truth or Dare بە هێزی لەلایەن {ctx.author.mention} وەستاندرا.\n"
            f"The Truth or Dare game was force-stopped by {ctx.author.mention}."
        ),
        color=0xEF4444,
    )
    await ctx.send(embed=embed)
    try:
        await ctx.message.delete()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# --- AMONG THE SHADOWS ($shadow) — Social Deduction Game ---
# Min 4 players · Max 10 players
# Roles: Shadow 🕷️ | Detective 🔍 | Medic 💉 | Civilian 👤
# ─────────────────────────────────────────────────────────────────────────────

SHADOW_ROLES = {
    "SHADOW": {
        "name": "Shadow | سێبەر",
        "emoji": "🕷️",
        "color": 0x1A1A2E,
        "desc_en": "You are a **Shadow**. Each night, choose a player to **eliminate**. Blend in during the day and avoid being voted out!",
        "desc_ku": "تۆ **سێبەرێک**ی. هەر شەوێک یەک یاریزان **دەرببە**. ڕۆژانە خۆت بشارەوە و هەوڵبدە دەنگ لەبەرامبەرت نەدرێت!",
        "team": "shadows",
    },
    "DETECTIVE": {
        "name": "Detective | کارئاگا",
        "emoji": "🔍",
        "color": 0x1E40AF,
        "desc_en": "You are the **Detective**. Each night, investigate one player to learn if they are a Shadow.",
        "desc_ku": "تۆ **کارئاگا**یی. هەر شەوێک یەک یاریزان لێبکۆڵەرەوە تا بزانیت سێبەرن یان نا.",
        "team": "town",
    },
    "MEDIC": {
        "name": "Medic | پزیشک",
        "emoji": "💉",
        "color": 0x166534,
        "desc_en": "You are the **Medic**. Each night, protect one player from elimination. You can protect yourself once per game.",
        "desc_ku": "تۆ **پزیشک**ی. هەر شەوێک یەک یاریزان بپارێزە. دەتوانیت خۆت یەک جار بپارێزیت لە یاری.",
        "team": "town",
    },
    "CIVILIAN": {
        "name": "Civilian | باژێرگەر",
        "emoji": "👤",
        "color": 0x78716C,
        "desc_en": "You are a **Civilian**. No special power — but your vote counts! Find and eliminate the Shadows.",
        "desc_ku": "تۆ **باژێرگەر**ی. هیچ تایبەتمەندییەکی تایبەتت نییە، بەڵام دەنگەکەت گرنگە! سێبەرەکان بدۆزەرەوە.",
        "team": "town",
    },
}

shadow_sessions = {}   # channel_id -> ShadowSession


class ShadowSession:
    def __init__(self, host_id: int, channel_id: int, guild_id: int):
        self.host_id      = host_id
        self.channel_id   = channel_id
        self.guild_id     = guild_id
        self.state        = "lobby"      # lobby|night|day_discussion|day_vote|ended
        self.players      = {}           # uid -> {"alive": bool}
        self.role_map     = {}           # uid -> role key
        self.phase        = 0
        self.night_actions = {}          # role_key -> target_uid
        self.votes        = {}           # voter_uid -> target_uid
        self.elim_log     = []           # [{"uid", "phase", "by"}]
        self.medic_self_used = False
        self.lobby_msg    = None

    def alive_players(self):
        return [uid for uid, p in self.players.items() if p["alive"]]

    def shadows_alive(self):
        return [uid for uid in self.alive_players() if self.role_map.get(uid) == "SHADOW"]

    def town_alive(self):
        return [uid for uid in self.alive_players() if self.role_map.get(uid) != "SHADOW"]

    def check_win(self):
        s = len(self.shadows_alive())
        t = len(self.town_alive())
        if s == 0:
            return "town"
        if s >= t:
            return "shadows"
        return None

    def assign_roles(self):
        pids = list(self.players.keys())
        random.shuffle(pids)
        n = len(pids)
        shadow_count = 1 if n <= 6 else 2
        i = 0
        for _ in range(shadow_count):
            self.role_map[pids[i]] = "SHADOW"; i += 1
        self.role_map[pids[i]] = "DETECTIVE"; i += 1
        self.role_map[pids[i]] = "MEDIC";     i += 1
        while i < n:
            self.role_map[pids[i]] = "CIVILIAN"; i += 1


# ── Embed builders ────────────────────────────────────────────────────────────

def _sh_lobby_embed(session: ShadowSession, enabled: bool) -> discord.Embed:
    player_list = "\n".join(f"• <@{uid}>" for uid in session.players) or "*هیچ یاریزانێک هێشتا نییە | No players yet...*"
    count = len(session.players)
    embed = discord.Embed(
        title="🌑 لەنێو سێبەرەکان | Among the Shadows",
        description=(
            "> *لەم شارەدا تاریکی لەنێو بێگوناهەکان شاردەوەت...*\n"
            "> *In this town, darkness hides amongst the innocent...*\n\n"
            "یارییەکی کۆمەڵایەتی بۆ **خاپووریکردن، لێکۆڵینەوە و مانەوە.**\n"
            "A social deduction game of **deception, deduction & survival.**\n\n"
            "**ڕۆڵەکان | Roles:**\n"
            "🕷️ **سێبەر | Shadow** — شەوانە یاریزان دەکوژێت\n"
            "🔍 **کارئاگا | Detective** — هەر شەوێک یەک یاریزان لێدەکۆڵێتەوە\n"
            "💉 **پزیشک | Medic** — هەر شەوێک یەک یاریزان دەپارێزێت\n"
            "👤 **باژێرگەر | Civilian** — ڕۆژانە دەنگ دەدات بۆ دەرکردنی گومانلێکراوان\n\n"
            f"**یاریزانان ({count}/10) | Players ({count}/10):**\n{player_list}"
        ),
        color=0x0F0F1A,
    )
    embed.add_field(name="📌 کەمترین یاریزان | Min Players", value="4", inline=True)
    embed.add_field(name="👑 میواندار | Host", value=f"<@{session.host_id}>", inline=True)
    embed.set_footer(text="بەشداربە بزووە ← میوانداری دەست بکات کە ٤+ یاریزان هاتن | Press Join → Host starts when 4+ players ready")
    embed.timestamp = datetime.datetime.utcnow()
    return embed


def _sh_night_embed(phase: int) -> discord.Embed:
    embed = discord.Embed(
        title=f"🌑 شەوی {phase} دەهاتە خوارەوە | Night {phase} Falls...",
        description=(
            "> *شارەکە بێدەنگ دەبێت. شتێک لە تاریکییەکەدا دەجوڵێت...*\n"
            "> *The town goes quiet. Something stirs in the dark...*\n\n"
            "**پەیامی تایبەتەکانت بپشکنە! | Check your DMs!** ڕۆڵە تایبەتەکان دەبێت ئێستا کار بکەن:\n"
            "🕷️ سێبەر | Shadow → ئەوی هەڵبژێرە کە دەرببەیت | Choose who to eliminate\n"
            "🔍 کارئاگا | Detective → یەکێک لێبکۆڵەرەوە | Investigate someone\n"
            "💉 پزیشک | Medic → یەکێک بپارێزە | Protect someone\n\n"
            "*باژێرگەرەکان چاوەڕوان دەبن... | Civilians wait patiently...*"
        ),
        color=0x0F0F1A,
    )
    embed.set_footer(text="قۆناغی شەو — ٩٠ چرکە | Night phase — 90 seconds")
    embed.timestamp = datetime.datetime.utcnow()
    return embed


def _sh_day_embed(phase: int, events: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"☀️ ڕۆژی {phase} — شار شیان دەبێتەوە | Day {phase} — The Town Awakens",
        description=(
            f"> *بەیانی بوو، شار چاودێری ئەو شەوەی دەکات...*\n"
            f"> *As the sun rises, the town gathers to share what they know...*\n\n"
            f"**بۆچوونەکانی شەوی ڕابردوو | Last Night's Events:**\n{events}\n\n"
            f"**قۆناغی گفتوگۆ | Discussion Phase:** ٦٠ چرکەتان هەیە بۆ گفتوگۆ | 60 seconds to discuss.\n"
            f"دواتر دەنگدان دەستپێدەکات! | Then voting begins!"
        ),
        color=0xF59E0B,
    )
    embed.set_footer(text="ئێستا گفتوگۆ بکە! دەنگدان دەستپێدەکات زوو | Discuss now! Voting starts soon.")
    embed.timestamp = datetime.datetime.utcnow()
    return embed


def _sh_vote_embed(phase: int, alive: list) -> discord.Embed:
    player_list = "\n".join(f"• <@{uid}>" for uid in alive)
    embed = discord.Embed(
        title=f"🗳️ ڕۆژی {phase} — دەنگ بدە بۆ دەرکردن | Day {phase} — Vote to Eliminate",
        description=(
            "> *شار دەبێت بڕیار بدات. یەکێک دەرکرابێت.*\n"
            "> *The town must decide. One will be cast out.*\n\n"
            "هەر یاریزانی زیندوو **یەک دەنگی** هەیە | Each alive player gets **one vote**.\n"
            "زۆرترین دەنگ = دەرکراو | Most votes = eliminated.\n"
            "ئەگەر یەکسان بوو، **کەس دەرناکرێت** | Tie = **no one** eliminated.\n\n"
            f"**یاریزانانی زیندوو | Alive Players:**\n{player_list}"
        ),
        color=0xDC2626,
    )
    embed.set_footer(text="لیستەکە بەکاربهێنە بۆ دەنگدان | Use the dropdown to vote • 60 seconds")
    embed.timestamp = datetime.datetime.utcnow()
    return embed


def _sh_win_embed(winner: str, session: ShadowSession) -> discord.Embed:
    is_town = winner == "town"
    shadows_were = ", ".join(f"<@{uid}>" for uid, r in session.role_map.items() if r == "SHADOW") or "کەس | None"
    log_lines = "\n".join(f"• <@{e['uid']}> — شەو/ڕۆژ {e['phase']} ({e['by']})" for e in session.elim_log) or "هیچکەس دەرنەکرا | No one was eliminated."
    embed = discord.Embed(
        title="☀️ شار بردی! | The Town Wins!" if is_town else "🕷️ سێبەرەکان بردیان! | The Shadows Win!",
        description=(
            ("> *ڕووناکی گەڕایەوە. سێبەرەکان شکستیان خوارد!*\n> *Light has returned. The shadows are defeated!*\n\nشار هەموو سێبەرەکانی دۆزیەوە!\nThe town successfully rooted out all the Shadows!")
            if is_town else
            ("> *تاریکی هەموو شتی لەخۆ گرت. سێبەرەکان سەروەریان کرد.*\n> *Darkness consumes all. The Shadows reign.*\n\nسێبەرەکان شار خستەژێر دەستیان!\nThe Shadows have taken over the town!")
        ),
        color=0x22C55E if is_town else 0x1A1A2E,
    )
    embed.add_field(name="🕷️ سێبەرەکان | Shadows Were", value=shadows_were, inline=False)
    embed.add_field(name="📜 لۆگی دەرکراوان | Elimination Log", value=log_lines, inline=False)
    embed.set_footer(text="سوپاس بۆ یاریکردن! | Thanks for playing! Use $shadow for a new game.")
    embed.timestamp = datetime.datetime.utcnow()
    return embed


# ── Night DM Views ─────────────────────────────────────────────────────────────

class ShadowKillView(discord.ui.View):
    def __init__(self, session: ShadowSession, targets: list):
        super().__init__(timeout=90)
        self.session = session
        options = [discord.SelectOption(label=f"یاریزان ...{uid[-4:]} | Player ...{uid[-4:]}", value=uid) for uid in targets]
        sel = discord.ui.Select(placeholder="🕷️ قوربانییەکە هەڵبژێرە | Choose victim...", options=options, custom_id="sh_kill")
        sel.callback = self._cb
        self.add_item(sel)

    async def _cb(self, interaction: discord.Interaction):
        if self.session.state != "night":
            await interaction.response.send_message("❌ ئێستا شەو نییە | Not night phase.", ephemeral=True)
            return
        self.session.night_actions["SHADOW"] = interaction.data["values"][0]
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=discord.Embed(title="🕷️ ئامانج تەسبیت کرا | Target Locked", description="هەڵبژاردنەکەت تۆمار کرا. چاوەڕوانی بەیانی بکە | Your choice has been recorded. Wait for morning...", color=0x1A1A2E),
            view=self,
        )


class ShadowInvestigateView(discord.ui.View):
    def __init__(self, session: ShadowSession, targets: list):
        super().__init__(timeout=90)
        self.session = session
        options = [discord.SelectOption(label=f"یاریزان ...{uid[-4:]} | Player ...{uid[-4:]}", value=uid) for uid in targets]
        sel = discord.ui.Select(placeholder="🔍 یەکێک هەڵبژێرە بۆ لێکۆڵینەوە | Choose to investigate...", options=options, custom_id="sh_invest")
        sel.callback = self._cb
        self.add_item(sel)

    async def _cb(self, interaction: discord.Interaction):
        if self.session.state != "night":
            await interaction.response.send_message("❌ ئێستا شەو نییە | Not night phase.", ephemeral=True)
            return
        self.session.night_actions["DETECTIVE"] = interaction.data["values"][0]
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=discord.Embed(title="🔍 لێکۆڵینەوە...", description="ئەنجامەکان لە بەیانیدا دەگەیشتنەت | You'll receive results at dawn.", color=0x1E40AF),
            view=self,
        )


class ShadowProtectView(discord.ui.View):
    def __init__(self, session: ShadowSession, targets: list):
        super().__init__(timeout=90)
        self.session = session
        options = [discord.SelectOption(label=f"یاریزان ...{uid[-4:]} | Player ...{uid[-4:]}", value=uid) for uid in targets]
        sel = discord.ui.Select(placeholder="💉 یەکێک هەڵبژێرە بۆ پاراستن | Choose to protect...", options=options, custom_id="sh_protect")
        sel.callback = self._cb
        self.add_item(sel)

    async def _cb(self, interaction: discord.Interaction):
        if self.session.state != "night":
            await interaction.response.send_message("❌ ئێستا شەو نییە | Not night phase.", ephemeral=True)
            return
        uid = interaction.data["values"][0]
        if uid == str(interaction.user.id):
            self.session.medic_self_used = True
        self.session.night_actions["MEDIC"] = uid
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=discord.Embed(title="💉 پاراستن دیاری کرا | Protection Set", description="ئەو شەوە لەژێر چاودێرییەتدایە | They're under your watch tonight.", color=0x166534),
            view=self,
        )


class ShadowVoteView(discord.ui.View):
    def __init__(self, session: ShadowSession, voter_id: int, targets: list):
        super().__init__(timeout=60)
        self.session  = session
        self.voter_id = voter_id
        options = [discord.SelectOption(label=f"یاریزان ...{uid[-4:]} | Player ...{uid[-4:]}", value=uid) for uid in targets]
        sel = discord.ui.Select(placeholder="🗳️ ئەوی هەڵبژێرە کە دەرببەیت | Choose who to eliminate...", options=options, custom_id="sh_vote")
        sel.callback = self._cb
        self.add_item(sel)

    async def _cb(self, interaction: discord.Interaction):
        if self.session.state != "day_vote":
            await interaction.response.send_message("❌ دەنگدان چالاک نییە | Voting is not active.", ephemeral=True)
            return
        uid = str(interaction.user.id)
        if not self.session.players.get(uid, {}).get("alive"):
            await interaction.response.send_message("❌ تۆ دەرکرایت، دەنگت نییە | You are eliminated and cannot vote.", ephemeral=True)
            return
        self.session.votes[uid] = interaction.data["values"][0]
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=discord.Embed(title="🗳️ دەنگەکەت دراو | Vote Cast", description="دەنگەکەت تۆمار کرا | Your vote has been recorded.", color=0xDC2626),
            view=self,
        )


# ── Lobby View ─────────────────────────────────────────────────────────────────

class ShadowLobbyView(discord.ui.View):
    def __init__(self, session: ShadowSession):
        super().__init__(timeout=300)
        self.session = session

    def _enough(self):
        return len(self.session.players) >= 4

    @discord.ui.button(label="🌑 بەشداربە | Join Game", style=discord.ButtonStyle.secondary, custom_id="shadow_join")
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        s = self.session
        if s.state != "lobby":
            await interaction.response.send_message("❌ لۆبی چالاک نییە | No active lobby.", ephemeral=True); return
        uid = str(interaction.user.id)
        if uid in s.players:
            await interaction.response.send_message("✅ پێشتر بەشداربووی | You're already in!", ephemeral=True); return
        if len(s.players) >= 10:
            await interaction.response.send_message("❌ یاری پڕ بووە | Game is full!", ephemeral=True); return
        s.players[uid] = {"alive": True}
        self.start_btn.disabled = not self._enough()
        await interaction.response.edit_message(embed=_sh_lobby_embed(s, self._enough()), view=self)

    @discord.ui.button(label="🚪 دەربچوون | Leave", style=discord.ButtonStyle.danger, custom_id="shadow_leave")
    async def leave_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        s = self.session
        if s.state != "lobby":
            await interaction.response.send_message("❌ لۆبی نییە | No lobby to leave.", ephemeral=True); return
        uid = str(interaction.user.id)
        if uid not in s.players:
            await interaction.response.send_message("❌ تۆ لەم یاریەدا نیت | You're not in this game.", ephemeral=True); return
        if interaction.user.id == s.host_id:
            await interaction.response.send_message("❌ میوانداری نەتوانیت بچیت | Host can't leave. Cancel instead.", ephemeral=True); return
        del s.players[uid]
        self.start_btn.disabled = not self._enough()
        await interaction.response.edit_message(embed=_sh_lobby_embed(s, self._enough()), view=self)

    @discord.ui.button(label="▶️ دەستپێبکە | Start Game", style=discord.ButtonStyle.success, custom_id="shadow_start", disabled=True)
    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        s = self.session
        if s.state != "lobby":
            await interaction.response.send_message("❌ لۆبی نییە | No lobby.", ephemeral=True); return
        if interaction.user.id != s.host_id:
            await interaction.response.send_message("❌ تەنها میوانداری دەتوانێت دەستپێبکات | Only the host can start.", ephemeral=True); return
        if len(s.players) < 4:
            await interaction.response.send_message("❌ لانیکەم ٤ یاریزان پێویستە | Need at least 4 players!", ephemeral=True); return

        s.assign_roles()
        s.state = "night"
        s.phase = 1

        for child in self.children:
            child.disabled = True
        started_embed = _sh_lobby_embed(s, True)
        started_embed.description = "✅ یاری دەستپێکرد! پەیامی تایبەتەکانت بپشکنە بۆ ڕۆڵەکەت.\n✅ Game started! Check your DMs for your role."
        started_embed.color = 0x22C55E
        await interaction.response.edit_message(embed=started_embed, view=self)

        # DM roles
        for uid, role_key in s.role_map.items():
            rd = SHADOW_ROLES[role_key]
            try:
                member = await interaction.guild.fetch_member(int(uid))
                role_embed = discord.Embed(
                    title=f"{rd['emoji']} ڕۆڵەکەت | Your Role: {rd['name']}",
                    description=f"**کوردی:**\n{rd['desc_ku']}\n\n**English:**\n{rd['desc_en']}",
                    color=rd["color"],
                )
                role_embed.set_footer(text="ڕۆڵەکەت نهێنی بگرە! | Keep your role secret!")
                await member.send(embed=role_embed)
            except Exception:
                pass

        await interaction.channel.send(embed=_sh_night_embed(1))
        await _sh_send_night_dms(s, interaction.guild)
        bot.loop.create_task(_sh_night_timer(s, interaction.channel, interaction.guild))

    async def on_timeout(self):
        shadow_sessions.pop(self.session.channel_id, None)


# ── Night DM sender ────────────────────────────────────────────────────────────

async def _sh_send_night_dms(session: ShadowSession, guild: discord.Guild):
    for uid in session.alive_players():
        role_key = session.role_map.get(uid)
        try:
            member = await guild.fetch_member(int(uid))
            user   = member.user
            alive  = session.alive_players()

            if role_key == "SHADOW":
                targets = [t for t in alive if t != uid]
                if not targets:
                    continue
                shadows_other = [f"<@{s}>" for s in session.shadows_alive() if s != uid]
                embed = discord.Embed(
                    title="🕷️ سێبەر — ئامانجەکەت هەڵبژێرە | Shadow — Choose Your Target",
                    description=(
                        f"شەوی {session.phase}: یەک یاریزان هەڵبژێرە بۆ **دەرکردن | eliminate**.\n"
                        f"سێبەرانی تر | Fellow shadows: {', '.join(shadows_other) if shadows_other else '*تەنها یی | You are alone.*'}"
                    ),
                    color=0x1A1A2E,
                )
                await user.send(embed=embed, view=ShadowKillView(session, targets))

            elif role_key == "DETECTIVE":
                targets = [t for t in alive if t != uid]
                if not targets:
                    continue
                embed = discord.Embed(
                    title="🔍 کارئاگا — لێبکۆڵەرەوە | Detective — Investigate",
                    description=f"شەوی {session.phase}: یەک یاریزان هەڵبژێرە بۆ **لێکۆڵینەوە | investigate**. بزانیت سێبەرن یان نا.",
                    color=0x1E40AF,
                )
                await user.send(embed=embed, view=ShadowInvestigateView(session, targets))

            elif role_key == "MEDIC":
                targets = [t for t in alive if t != uid] if session.medic_self_used else alive
                if not targets:
                    continue
                self_note = "\n*(دەتوانیت خۆت یەک جار بپارێزیت | You may protect yourself once.)*" if not session.medic_self_used else ""
                embed = discord.Embed(
                    title="💉 پزیشک — یەکێک بپارێزە | Medic — Protect Someone",
                    description=f"شەوی {session.phase}: یەک یاریزان هەڵبژێرە بۆ **پاراستن | protect** ئەو شەوە.{self_note}",
                    color=0x166534,
                )
                await user.send(embed=embed, view=ShadowProtectView(session, targets))

            else:
                embed = discord.Embed(
                    title="👤 باژێرگەر — چاوەڕوان بە | Civilian — Rest...",
                    description=f"شەوی {session.phase}: هیچ تایبەتمەندییەکی تایبەتت نییە. چاوەڕوانی بەیانی بکە و ڕۆژانە سێبەرەکان بدۆزەرەوە!\nNight {session.phase}: You have no special ability. Wait for morning and find the Shadows!",
                    color=0x78716C,
                )
                await user.send(embed=embed)
        except Exception:
            pass


# ── Night timer & resolution ───────────────────────────────────────────────────

async def _sh_night_timer(session: ShadowSession, channel, guild: discord.Guild):
    await asyncio.sleep(90)
    if session.state == "night":
        await _sh_resolve_night(session, channel, guild)


async def _sh_resolve_night(session: ShadowSession, channel, guild: discord.Guild):
    kill     = session.night_actions.get("SHADOW")
    protect  = session.night_actions.get("MEDIC")
    invest   = session.night_actions.get("DETECTIVE")
    events   = ""

    if protect and protect == kill:
        events += "💉 *یەک کەس لە کاتی گونجاودا پارێزرا... | Someone was protected in the nick of time...*\n"

    if kill and kill != protect:
        session.players[kill]["alive"] = False
        session.elim_log.append({"uid": kill, "phase": session.phase, "by": "Shadow | سێبەر"})
        events += f"🪦 **<@{kill}>** بەیانی دۆزرایەوە کەکرا... | was found eliminated at dawn...\n"
    elif not kill:
        events += "😌 *شەوەکە ئارام تێپەڕی. کەس دەرنەکرا. | The night passed peacefully. No one was eliminated.*\n"

    if invest:
        role_key = session.role_map.get(invest)
        is_shadow = role_key == "SHADOW"
        det_id = next((uid for uid, r in session.role_map.items() if r == "DETECTIVE"), None)
        if det_id:
            try:
                det_member = await guild.fetch_member(int(det_id))
                color = 0xEF4444 if is_shadow else 0x22C55E
                label = "🕷️ **سێبەرە! | A SHADOW!**" if is_shadow else "✅ **سێبەر نییە | Not a Shadow.**"
                await det_member.send(embed=discord.Embed(
                    title="🔍 ئەنجامی لێکۆڵینەوە | Investigation Result",
                    description=f"یاریزان `...{invest[-4:]}` {label}",
                    color=color,
                ))
            except Exception:
                pass

    session.night_actions = {}
    session.state = "day_discussion"
    session.phase += 1

    win = session.check_win()
    if win:
        await _sh_end_game(session, channel, win)
        return

    await _sh_start_day(session, channel, guild, events or "*هیچ ڕووی نەدا شەوی ڕابردوو | Nothing happened last night.*")


async def _sh_start_day(session: ShadowSession, channel, guild: discord.Guild, events: str):
    alive_mentions = " ".join(f"<@{uid}>" for uid in session.alive_players())
    await channel.send(content=alive_mentions, embed=_sh_day_embed(session.phase, events))
    await asyncio.sleep(60)
    if session.state != "ended":
        await _sh_start_voting(session, channel, guild)


async def _sh_start_voting(session: ShadowSession, channel, guild: discord.Guild):
    if session.state == "ended":
        return
    session.state = "day_vote"
    session.votes = {}
    alive = session.alive_players()
    await channel.send(embed=_sh_vote_embed(session.phase, alive))

    for voter_id in alive:
        targets = [t for t in alive if t != voter_id]
        if not targets:
            continue
        try:
            member = await guild.fetch_member(int(voter_id))
            vote_embed = discord.Embed(
                title="🗳️ دەنگەکەت بدە | Cast Your Vote",
                description="ئەوی هەڵبژێرە کە بەباوەڕت سێبەرە | Choose who you think is a Shadow!",
                color=0xDC2626,
            )
            await member.send(embed=vote_embed, view=ShadowVoteView(session, voter_id, targets))
        except Exception:
            pass

    await asyncio.sleep(60)
    if session.state != "ended":
        await _sh_resolve_votes(session, channel, guild)


async def _sh_resolve_votes(session: ShadowSession, channel, guild: discord.Guild):
    if session.state == "ended":
        return
    tally = {}
    for target in session.votes.values():
        tally[target] = tally.get(target, 0) + 1

    eliminated = None
    max_votes   = 0
    tie         = False
    for uid, count in tally.items():
        if count > max_votes:
            max_votes = count; eliminated = uid; tie = False
        elif count == max_votes:
            tie = True

    if tie or not eliminated:
        await channel.send(embed=discord.Embed(
            title="🤝 یەکسانە! | It's a Tie!",
            description="> *شار نەیتوانی یەک بڕیار بدات. ئەمڕۆ کەس دەرنەکرا.*\n> *The town couldn't agree. No one is eliminated today.*\n\nشەو نزیکدەبێتەوە... | The night approaches...",
            color=0x6B7280,
        ))
    else:
        session.players[eliminated]["alive"] = False
        session.elim_log.append({"uid": eliminated, "phase": session.phase, "by": "Town Vote | دەنگی شار"})
        role_key = session.role_map.get(eliminated, "CIVILIAN")
        rd = SHADOW_ROLES[role_key]
        await channel.send(embed=discord.Embed(
            title="⚖️ شار قسەی کرد | The Town Has Spoken",
            description=f"> *خەڵک سەری بۆ ئەوەی هاتن...*\n\n<@{eliminated}> دەرکرا.\nThey were a **{rd['emoji']} {rd['name']}**.",
            color=0xDC2626,
        ))

    win = session.check_win()
    if win:
        await _sh_end_game(session, channel, win)
        return

    session.state = "night"
    await channel.send(embed=_sh_night_embed(session.phase + 1))
    await _sh_send_night_dms(session, guild)
    bot.loop.create_task(_sh_night_timer(session, channel, guild))


async def _sh_end_game(session: ShadowSession, channel, winner: str):
    session.state = "ended"
    shadow_sessions.pop(session.channel_id, None)
    await channel.send(embed=_sh_win_embed(winner, session))


# ── Commands ───────────────────────────────────────────────────────────────────

@bot.command(name="shadow")
async def shadow_cmd(ctx):
    """Start an Among the Shadows game (4-10 players). | یارییەکی لەنێو سێبەرەکان (٤-١٠ یاریزان)."""
    cid = ctx.channel.id
    if cid in shadow_sessions:
        await ctx.send(
            "⚠️ هەر یارییەکی لەنێو سێبەرەکان چالاکە لەم کەناڵەدا | "
            "There's already an active Among the Shadows game here!",
            delete_after=8
        )
        return

    session = ShadowSession(ctx.author.id, cid, ctx.guild.id)
    session.players[str(ctx.author.id)] = {"alive": True}
    shadow_sessions[cid] = session

    view = ShadowLobbyView(session)
    msg  = await ctx.send(embed=_sh_lobby_embed(session, False), view=view)
    session.lobby_msg = msg
    try:
        await ctx.message.delete()
    except Exception:
        pass


@bot.command(name="shadowstop")
@commands.has_permissions(manage_messages=True)
async def shadow_stop(ctx):
    """Force-stop an Among the Shadows game. | یارییەکی لەنێو سێبەرەکان بوەستێنە."""
    cid = ctx.channel.id
    if cid not in shadow_sessions:
        await ctx.send("❌ هیچ یارییەکی چالاکی لەنێو سێبەرەکان نییە لەم کەناڵەدا | No active Among the Shadows game here.", delete_after=8)
        return
    shadow_sessions.pop(cid, None)
    embed = discord.Embed(
        title="🛑 یاری هەڵوەشایەوە | Game Stopped",
        description=f"یارییەکی لەنێو سێبەرەکان بە هێزی لەلایەن {ctx.author.mention} وەستاندرا.\nThe Among the Shadows game was force-stopped by {ctx.author.mention}.",
        color=0xEF4444,
    )
    await ctx.send(embed=embed)
    try:
        await ctx.message.delete()
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# --- RUSSIAN ROULETTE: LAST ONE STANDING ($roulette) ---
# ─────────────────────────────────────────────────────────────────────────────

RR_SUSPENSE = [
    "🎰 سیلیندەرەکە دەسوڕێتەوە... | The cylinder spins...",
    "😰 مەرمیلکەکە دەکێشرێتەوە... | The hammer pulls back...",
    "🤫 ژوورەکە تەواو بێدەنگ دەبێت... | The room goes dead silent...",
    "💀 دانەیەک ئارەق لە ڕووخساریان دادەڕژێت... | A bead of sweat rolls down their face...",
    "🫀 دڵیان بەتیزی دەلێت... | Their heart is pounding...",
    "🌀 کلیک... کلیک... کلیک... | Click... click... click...",
    "😶 چاویان دادەخەن... | They close their eyes...",
    "🎲 چارەنووس بڕیاری دەدات... | Fate decides...",
    "🕯️ مووچاکە دەلەرزێت... | The candle flickers...",
    "⏳ کات هێواش دەبێتەوە... | Time slows down...",
]

RR_SURVIVED = [
    "💨 **کلیک.** هیچ. هەواڵێکی سووک دەردەکەن. هێشتا زیندوون. | **CLICK.** Nothing. They exhale slowly. Still alive.",
    "😮‍💨 **کلیک.** مەرمیلکەی بەتاڵ. کۆمەڵەکە ئەلقەیان دەبێت. | **CLICK.** Empty chamber. The crowd gasps.",
    "🫡 **کلیک.** خەتا کرد. خودا دلۆڤانی بوو... ئەمجار. | **CLICK.** A miss. The gods were merciful... this time.",
    "😅 **کلیک.** بێدەنگی. مانەوی — بە زۆر. | **CLICK.** Silence. They survived — barely.",
    "🤍 **کلیک.** مەرمیلکەکە لەسەر هیچ دەکەوێت. گیانێکی خۆشبەخت. | **CLICK.** The hammer falls on nothing. Lucky soul.",
    "🫶 **کلیک.** بەتاڵ. قاچیان هێندەک لەبار دەبێت لە ئارامیەوە. | **CLICK.** Empty. Their knees nearly buckle with relief.",
]

RR_ELIMINATED = [
    "💥 **بانگ!!** مەرمیلکەکە پڕ بوو. دەریان کرد. | **BANG!!** The chamber was loaded. They're out.",
    "🔥 **بانگ!!** چوون. هەر بەو شێوەیە. | **BANG!!** Gone. Just like that.",
    "😵 **بانگ!!** گولولەکە نیشانەکەی دۆزیەوە. دەریان کرد. | **BANG!!** The bullet found its mark. Eliminated.",
    "💀 **بانگ!!** ئەمشەو بەخشینی نییە. تەواو بوون. | **BANG!!** No mercy tonight. They're done.",
    "⚡ **بانگ!!** مەترسیەکە لەدایان گەیشت. | **BANG!!** The odds finally caught up with them.",
    "🩸 **بانگ!!** ئەوەی ماوەتەوە کەمتر بووە... | **BANG!!** And then there were fewer...",
]

RR_WIN_MSGS = [
    "لە دوژمنی و ژیانی دووپاتەوە چوو. | walked through hell and came out the other side.",
    "ڕووبەڕووی مەرگ بوو و ئاخری چاوی پووچ کرد. | stared death in the face and blinked last.",
    "دوایین کەسی نەفەسەکێشەرە. ڕێز. | is the last one breathing. Respect.",
    "لەسەر هەموو کێشەیەک مانەوی. بەلاتوانرا. | survived every pull. Unbelievable.",
    "یان خۆشبەختترینە یان جەسورترین. هەر شێوەیەک — براوە. | is either the luckiest or the bravest. Either way — winner.",
]

roulette_sessions = {}   # channel_id -> RouletteGame


class RouletteGame:
    def __init__(self, host_id: int, channel_id: int):
        self.host_id      = host_id
        self.channel_id   = channel_id
        self.state        = "lobby"      # lobby | playing | ended
        self.players      = []           # ordered list of str user ids
        self.alive        = set()        # alive str user ids
        self.current_idx  = 0
        self.round        = 1
        self.chamber      = 0
        self.bullet_pos   = 0
        self.pulls        = 0
        self.stats        = {}           # uid -> {"survived": N, "pulls": N}
        self.pull_lock    = False        # prevents double-processing a pull

    def alive_players(self):
        return [uid for uid in self.players if uid in self.alive]

    def current_player(self):
        alive = self.alive_players()
        if not alive:
            return None
        return alive[self.current_idx % len(alive)]

    def advance_turn(self):
        alive = self.alive_players()
        if alive:
            self.current_idx = (self.current_idx + 1) % len(alive)

    def spin_cylinder(self):
        self.bullet_pos = random.randint(0, 5)
        self.chamber    = 0
        self.pulls      = 0

    def pull_trigger(self):
        hit = (self.chamber == self.bullet_pos)
        self.chamber = (self.chamber + 1) % 6
        self.pulls  += 1
        if self.pulls % 6 == 0:
            self.spin_cylinder()
        return hit

    def chamber_display(self):
        checked = self.pulls % 6
        return " ".join("⬛" if i < checked else "⬜" for i in range(6))


def _rr_lobby_embed(game: RouletteGame) -> discord.Embed:
    player_list = "\n".join(f"{i+1}. <@{uid}>" for i, uid in enumerate(game.players)) or "*هیچ یاریزانێک هێشتا نییە | No players yet...*"
    enough = len(game.players) >= 2
    embed = discord.Embed(
        title="🔫 ڕووسی ڕووڵێت: دوایین کەسی مانەوە | Russian Roulette: Last One Standing",
        description=(
            "> *یەک چەک. یەک گولولە. چەند کەسی شێتی ئامادەی کێشانی ماشەیە.*\n"
            "> *One gun. One bullet. Multiple fools willing to pull the trigger.*\n\n"
            "**چۆن کار دەکات | How it works:**\n"
            "• یاریزانان لە خوارەوەی خۆشکلاوی دانیشتووەتەوە و نۆبەت بەنۆبەت ماشە دەکێشن | Players sit in a circle and take turns pulling the trigger\n"
            "• ڕیڤۆڵڤەرەکە **٦ مەرمیلکەی** هەیە — یەکێکیان گولولە هەیە | The revolver has **6 chambers** — one has a bullet\n"
            "• **کلیک** = مانەوە. **بانگ** = دەرکردن | **CLICK** = survived. **BANG** = eliminated\n"
            "• سیلیندەرەکە دوای هەر ٦ کێشان دەسوڕێتەوە | The cylinder re-spins every full rotation\n"
            "• دوایین کەسی زیندووی ببرێت 🏆 | Last one alive wins 🏆\n\n"
            f"**یاریزانان ({len(game.players)}/8) | Players ({len(game.players)}/8):**\n{player_list}"
        ),
        color=0x1A1A1A,
    )
    embed.add_field(name="👑 میوانداری | Host", value=f"<@{game.host_id}>", inline=True)
    embed.add_field(name="⚙️ کەمترین | Min", value="2", inline=True)
    embed.set_footer(text="ئامادەیە! میوانداری دەتوانێت دەستپێبکات | Ready! Host can start." if enough else "چاوەڕوانی یاریزانی زیاتر... | Waiting for more players...")
    embed.timestamp = datetime.datetime.utcnow()
    return embed


def _rr_turn_embed(game: RouletteGame, current_id: str) -> discord.Embed:
    alive = game.alive_players()
    remaining = 6 - (game.pulls % 6)
    alive_list = "\n".join(f"• <@{uid}>" for uid in alive)
    embed = discord.Embed(
        title=f"🔫 قۆناغی {game.round} — نۆبەتی تۆیە | Round {game.round} — It's your turn",
        description=(
            "> *ڕیڤۆڵڤەرەکە لەسەر میزەکەدا دەسوڕێت...*\n"
            "> *The revolver slides across the table...*\n\n"
            f"<@{current_id}>، نۆبەتی تۆیە. **ماشەی بکێشە | Pull the trigger.**\n\n"
            f"**مەرمیلکەکان | Cylinder:** {game.chamber_display()}\n"
            f"**مەرمیلکەی ماوە ئەم سوڕانەدا | Chambers left:** {remaining}/6\n\n"
            f"**هێشتا زیندوون ({len(alive)}) | Still alive ({len(alive)}):**\n{alive_list}"
        ),
        color=0xDC2626,
    )
    embed.set_footer(text="٣٠ چرکەت هەیە — ئەگەر نەکێشیت خۆکارانە دەکێشرێت | 30 seconds to pull or you're auto-pulled!")
    embed.timestamp = datetime.datetime.utcnow()
    return embed


def _rr_win_embed(winner_id: str, game: RouletteGame) -> discord.Embed:
    log_lines = "\n".join(
        f"• <@{uid}> — {game.stats.get(uid, {}).get('survived', 0)} کێشانی سەلامەتی | pull(s) survived"
        for uid in game.players if uid not in game.alive
    ) or "*کەس دەرنەکرا؟! | No one was eliminated?!*"
    win_msg = random.choice(RR_WIN_MSGS)
    embed = discord.Embed(
        title="🏆 براوەمان هەیە! | We Have a Winner!",
        description=(
            f"<@{winner_id}> {win_msg}\n\n"
            f"**ئامارەکانی | Stats for** <@{winner_id}>: {game.stats.get(winner_id, {}).get('survived', 0)} کێشانی سەلامەتی | successful pull(s)\n\n"
            f"**ڕیزی دەرکراوان | Eliminated order:**\n{log_lines}"
        ),
        color=0xFFD700,
    )
    embed.set_footer(text="$roulette بەکاربهێنە بۆ یارییەکی نوێ | Use $roulette to start a new game.")
    embed.timestamp = datetime.datetime.utcnow()
    return embed


# ── Pull Trigger View ──────────────────────────────────────────────────────────

class RoulettePullView(discord.ui.View):
    def __init__(self, game: RouletteGame, channel):
        super().__init__(timeout=30)
        self.game    = game
        self.channel = channel

    @discord.ui.button(label="🔫 ماشە بکێشە | Pull the Trigger", style=discord.ButtonStyle.danger, custom_id="rr_pull")
    async def pull_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = str(interaction.user.id)
        g   = self.game
        if g.state != "playing":
            await interaction.response.send_message("❌ یاری چالاک نییە | No active game.", ephemeral=True); return
        if uid != g.current_player():
            await interaction.response.send_message("❌ نۆبەتی تۆ نییە! | It's not your turn!", ephemeral=True); return
        if g.pull_lock:
            await interaction.response.send_message("❌ هێشتا کار دەکات... | Processing...", ephemeral=True); return
        g.pull_lock = True
        self.stop()
        for child in self.children:
            child.disabled = True
        try:
            await interaction.response.edit_message(view=self)
        except Exception:
            pass
        await _rr_process_pull(g, self.channel, uid)

    async def on_timeout(self):
        g = self.game
        if g.state != "playing" or g.pull_lock:
            return
        g.pull_lock = True
        uid = g.current_player()
        if uid is None:
            return
        try:
            await self.channel.send(content=f"⏰ <@{uid}> زیاتر کاتی بوو... چەکەکە خۆکارانە دەکێشرێت! | took too long... the gun goes off automatically!")
        except Exception:
            pass
        await _rr_process_pull(g, self.channel, uid)


# ── Lobby View ─────────────────────────────────────────────────────────────────

class RouletteLobbyView(discord.ui.View):
    def __init__(self, game: RouletteGame):
        super().__init__(timeout=180)
        self.game = game

    def _rebuild_start_btn(self):
        for child in self.children:
            if hasattr(child, "custom_id") and child.custom_id == "rr_start":
                child.disabled = len(self.game.players) < 2

    @discord.ui.button(label="🔫 بەشداربە | Join Game", style=discord.ButtonStyle.danger, custom_id="rr_join")
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        g   = self.game
        uid = str(interaction.user.id)
        if g.state != "lobby":
            await interaction.response.send_message("❌ لۆبی چالاک نییە | No active lobby.", ephemeral=True); return
        if uid in g.players:
            await interaction.response.send_message("✅ پێشتر بەشداربووی | You're already in!", ephemeral=True); return
        if len(g.players) >= 8:
            await interaction.response.send_message("❌ یاری پڕ بووە (٨ زۆرترین) | Game is full (8 max)!", ephemeral=True); return
        g.players.append(uid)
        g.alive.add(uid)
        self._rebuild_start_btn()
        await interaction.response.edit_message(embed=_rr_lobby_embed(g), view=self)

    @discord.ui.button(label="🚪 دەربچوون | Leave", style=discord.ButtonStyle.secondary, custom_id="rr_leave")
    async def leave_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        g   = self.game
        uid = str(interaction.user.id)
        if g.state != "lobby":
            await interaction.response.send_message("❌ لۆبی نییە | No lobby to leave.", ephemeral=True); return
        if uid not in g.players:
            await interaction.response.send_message("❌ تۆ لەم یاریەدا نیت | You're not in this game.", ephemeral=True); return
        if int(uid) == g.host_id:
            await interaction.response.send_message("❌ میوانداری نەتوانیت بچێت | Host can't leave. Use $roulettestop.", ephemeral=True); return
        g.players.remove(uid)
        g.alive.discard(uid)
        self._rebuild_start_btn()
        await interaction.response.edit_message(embed=_rr_lobby_embed(g), view=self)

    @discord.ui.button(label="▶️ دەستپێبکە | Start", style=discord.ButtonStyle.success, custom_id="rr_start", disabled=True)
    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        g   = self.game
        uid = str(interaction.user.id)
        if g.state != "lobby":
            await interaction.response.send_message("❌ لۆبی نییە | No lobby.", ephemeral=True); return
        if int(uid) != g.host_id:
            await interaction.response.send_message("❌ تەنها میوانداری دەتوانێت دەستپێبکات | Only the host can start.", ephemeral=True); return
        if len(g.players) < 2:
            await interaction.response.send_message("❌ لانیکەم ٢ یاریزان پێویستە | Need at least 2 players!", ephemeral=True); return

        g.state = "playing"
        random.shuffle(g.players)
        g.alive = set(g.players)
        g.spin_cylinder()

        for child in self.children:
            child.disabled = True
        started_embed = _rr_lobby_embed(g)
        started_embed.description = "💥 یاری دەستپێدەکات! خۆشبەختی... پێویستتتە. | Game starting! Good luck... you'll need it."
        started_embed.color = 0xDC2626
        await interaction.response.edit_message(embed=started_embed, view=self)

        await asyncio.sleep(1.5)

        order_text = "\n".join(f"{i+1}. <@{uid}>" for i, uid in enumerate(g.players))
        begin_embed = discord.Embed(
            title="🔫 یاری دەستپێکرد | The Game Begins",
            description=(
                f"> *{len(g.players)} یاریزان. یەک گولولە. کێ دەگەڕێتەوە ماڵ؟ | {len(g.players)} players. One bullet. Who goes home?*\n\n"
                f"**ڕیزی نۆبەت | Turn order:**\n{order_text}\n\n"
                "سیلیندەرەکە سوڕاوەتەوە. هیچکەس نازانێت گولولەکە لەکوێیە.\n"
                "The cylinder has been spun. No one knows where the bullet is.\n"
                "**قۆناغی ١ ئێستا دەستپێدەکات | Round 1 starts now.**"
            ),
            color=0x1A1A1A,
        )
        await interaction.channel.send(embed=begin_embed)
        await asyncio.sleep(2)
        await _rr_run_turn(g, interaction.channel)

    async def on_timeout(self):
        roulette_sessions.pop(self.game.channel_id, None)


# ── Turn runner ────────────────────────────────────────────────────────────────

async def _rr_run_turn(game: RouletteGame, channel):
    if game.state != "playing":
        return
    game.pull_lock = False
    current_id = game.current_player()
    if current_id is None:
        return
    view  = RoulettePullView(game, channel)
    embed = _rr_turn_embed(game, current_id)
    await channel.send(content=f"<@{current_id}>", embed=embed, view=view)


async def _rr_process_pull(game: RouletteGame, channel, uid: str):
    suspense_msg = await channel.send(random.choice(RR_SUSPENSE))
    await asyncio.sleep(2.2)

    if uid not in game.stats:
        game.stats[uid] = {"survived": 0, "pulls": 0}
    game.stats[uid]["pulls"] += 1

    eliminated = game.pull_trigger()

    if eliminated:
        game.alive.discard(uid)
        await suspense_msg.edit(content=random.choice(RR_ELIMINATED) + f"\n> <@{uid}> دەریکرا | has been eliminated.")
        await asyncio.sleep(1.5)

        alive = game.alive_players()
        if len(alive) == 1:
            game.state = "ended"
            roulette_sessions.pop(game.channel_id, None)
            await asyncio.sleep(0.8)
            await channel.send(embed=_rr_win_embed(alive[0], game))
            return
        if len(alive) == 0:
            game.state = "ended"
            roulette_sessions.pop(game.channel_id, None)
            await channel.send(embed=discord.Embed(
                title="💀 هەموویان چوون... | Everyone's gone...",
                description="هیچ مانەوەیەک نییە. بە ڕاستی کاووس بوو. | No survivors. Truly chaotic.",
                color=0x1A1A1A,
            ))
            return
        await asyncio.sleep(0.8)
    else:
        game.stats[uid]["survived"] += 1
        await suspense_msg.edit(content=random.choice(RR_SURVIVED))
        await asyncio.sleep(1.2)
        game.advance_turn()

    alive = game.alive_players()
    if alive and game.current_idx % len(alive) == 0 and not eliminated:
        game.round += 1

    await _rr_run_turn(game, channel)


# ── Commands ───────────────────────────────────────────────────────────────────

@bot.command(name="roulette")
async def roulette_cmd(ctx):
    """Start a Russian Roulette game (2-8 players). | یارییەکی ڕووسی ڕووڵێت دەستپێبکە (٢-٨ یاریزان)."""
    cid = ctx.channel.id
    if cid in roulette_sessions:
        await ctx.send(
            "⚠️ هەر یارییەکی ڕووسی ڕووڵێت چالاکە لەم کەناڵەدا! | "
            "There's already an active Russian Roulette game in this channel!",
            delete_after=8
        )
        return

    game = RouletteGame(ctx.author.id, cid)
    game.players.append(str(ctx.author.id))
    game.alive.add(str(ctx.author.id))
    roulette_sessions[cid] = game

    view = RouletteLobbyView(game)
    await ctx.send(embed=_rr_lobby_embed(game), view=view)
    try:
        await ctx.message.delete()
    except Exception:
        pass


@bot.command(name="roulettestop")
@commands.has_permissions(manage_messages=True)
async def roulette_stop(ctx):
    """Force-stop a Russian Roulette game. | یارییەکی ڕووسی ڕووڵێت بوەستێنە."""
    cid = ctx.channel.id
    if cid not in roulette_sessions:
        await ctx.send("❌ هیچ یارییەکی چالاکی ڕووسی ڕووڵێت نییە لەم کەناڵەدا | No active Russian Roulette game here.", delete_after=8)
        return
    roulette_sessions.pop(cid, None)
    embed = discord.Embed(
        title="🛑 یاری هەڵوەشایەوە | Game Stopped",
        description=f"یارییەکی ڕووسی ڕووڵێت بە هێزی لەلایەن {ctx.author.mention} وەستاندرا.\nThe Russian Roulette game was force-stopped by {ctx.author.mention}.",
        color=0xEF4444,
    )
    await ctx.send(embed=embed)
    try:
        await ctx.message.delete()
    except Exception:
        pass



bot.run(token, log_handler=handler, log_level=logging.DEBUG)
