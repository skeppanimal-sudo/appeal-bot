import discord
import os
import asyncio
import random
import re
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta, UTC
from discord.ext import commands, tasks
from discord.ui import View, Modal, TextInput
from discord import app_commands

TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# ---------------- DEBUG CONFIG ---------------- #

DEBUG_CHANNEL_ID = 1485269074962415777

async def debug_log(text: str):
    print(f"[DEBUG] {text}")
    if 'bot' in globals():
        channel = bot.get_channel(DEBUG_CHANNEL_ID)
        if channel:
            try:
                await channel.send(f"[DEBUG] {text}")
            except:
                pass

# ---------------- POSTGRES DATABASE ---------------- #

print("[DEBUG] Connecting to PostgreSQL...")
conn = psycopg2.connect(DATABASE_URL, sslmode="require")
cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
print("[DEBUG] Connected to PostgreSQL.")

# Giveaway persistence tables
cursor.execute("""
CREATE TABLE IF NOT EXISTS giveaways (
    message_id BIGINT PRIMARY KEY,
    channel_id BIGINT,
    title TEXT,
    end_time TIMESTAMPTZ,
    winner_count INTEGER,
    host_id BIGINT
);
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS giveaway_entries (
    message_id BIGINT,
    user_id BIGINT,
    PRIMARY KEY (message_id, user_id)
);
""")

# Invite table
cursor.execute("""
CREATE TABLE IF NOT EXISTS invites (
    user_id TEXT PRIMARY KEY,
    regular INTEGER DEFAULT 0,
    left_count INTEGER DEFAULT 0
);
""")
conn.commit()

# ---------------- CONFIG ---------------- #

MAIN_GUILD_ID = 1476717006764900372
APPEAL_GUILD_ID = 1482442062862356573

BANNED_ROLE_ID = 1482444637795782919
NOT_BANNED_ROLE_ID = 1482444680313442345

PANEL_CHANNEL_ID = 1482443249594400993
APPEAL_REVIEW_CHANNEL = 1482443249594400996

ACCEPTED_CHANNEL = 1482442063592161594
ACCEPTED_ROLE = 1482444757178388673

SUPPORT_CHANNEL_ID = 1476717007717142735
INVITE_CHANNEL = 1476717008010870813

# Giveaway + auto-lock
GIVEAWAY_CHANNEL_ID = 1476717007717142731
AUTO_LOCK_CHANNEL_ID = 1486456524187631869

MAIN_SYNC_ROLE = 1485407866453102732
APPEAL_SYNC_ROLE = 1482444572687859773

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

invite_cache = {}
GIVEAWAYS = {}

# ---------------- TIME PARSER ---------------- #

def parse_time_string(time_str: str) -> timedelta:
    pattern = r"(\d+)\s*([dhm])"
    matches = re.findall(pattern, time_str.lower())
    if not matches:
        raise ValueError("Invalid time format. Use '1h 30m', '2h', '45m', '1d 2h'.")
    total_seconds = 0
    for amount, unit in matches:
        amount = int(amount)
        if unit == "d":
            total_seconds += amount * 86400
        elif unit == "h":
            total_seconds += amount * 3600
        elif unit == "m":
            total_seconds += amount * 60
    return timedelta(seconds=total_seconds)
# ---------------- GIVEAWAY VIEW (DB‑BACKED) ---------------- #

class GiveawayView(View):
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id

    @discord.ui.button(
        label="Join",
        style=discord.ButtonStyle.success,
        emoji="🎉",
        custom_id="giveaway_join"
    )
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):

        # Load giveaway from memory
        giveaway = GIVEAWAYS.get(self.message_id)
        if not giveaway:
            await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
            return

        user_id = interaction.user.id

        # Check if already joined (DB check)
        cursor.execute(
            "SELECT 1 FROM giveaway_entries WHERE message_id = %s AND user_id = %s",
            (self.message_id, user_id)
        )
        if cursor.fetchone():
            await interaction.response.send_message("You already joined.", ephemeral=True)
            return

        # Add to DB
        cursor.execute(
            """
            INSERT INTO giveaway_entries (message_id, user_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (self.message_id, user_id)
        )
        conn.commit()

        # Add to memory
        giveaway["entries"].add(user_id)

        # Update embed
        try:
            message = await interaction.channel.fetch_message(self.message_id)
        except:
            await interaction.response.send_message("Entry saved but embed couldn't update.", ephemeral=True)
            return

        unix = int(giveaway["end_time"].timestamp())

        embed = discord.Embed(
            title=giveaway["title"],
            color=discord.Color.from_rgb(255, 255, 255)
        )
        embed.add_field(name="Ends", value=f"<t:{unix}:R>", inline=False)
        embed.add_field(name="Hosted by", value=f"<@{giveaway['host_id']}>", inline=False)
        embed.add_field(name="Entries", value=f"**{len(giveaway['entries'])}**", inline=False)
        embed.set_footer(text=f"Winners:{giveaway['winner_count']}")

        await message.edit(embed=embed, view=self)
        await interaction.response.send_message("You joined the giveaway!", ephemeral=True)


# ---------------- END GIVEAWAY (DB‑BACKED) ---------------- #

async def end_giveaway(message_id: int, channel_id: int):
    await bot.wait_until_ready()

    giveaway = GIVEAWAYS.get(message_id)
    if not giveaway:
        return

    now = datetime.now(UTC)
    remaining = (giveaway["end_time"] - now).total_seconds()

    if remaining > 0:
        await asyncio.sleep(remaining)

    # Reload giveaway (in case memory changed)
    giveaway = GIVEAWAYS.get(message_id)
    if not giveaway:
        return

    # Load entries from DB
    cursor.execute(
        "SELECT user_id FROM giveaway_entries WHERE message_id = %s",
        (message_id,)
    )
    rows = cursor.fetchall()
    entries = [row["user_id"] for row in rows]

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except:
            return

    try:
        message = await channel.fetch_message(message_id)
    except:
        return

    # Pick winners
    if not entries:
        winners_text = "No valid entries."
    else:
        winner_count = giveaway["winner_count"]
        if winner_count > len(entries):
            winner_count = len(entries)
        winners = random.sample(entries, winner_count)
        winners_text = ", ".join(f"<@{uid}>" for uid in winners)

    unix = int(giveaway["end_time"].timestamp())

    embed = discord.Embed(
        title=giveaway["title"],
        color=discord.Color.from_rgb(255, 255, 255)
    )
    embed.add_field(name="Ended", value=f"<t:{unix}:R>", inline=False)
    embed.add_field(name="Hosted by", value=f"<@{giveaway['host_id']}>", inline=False)
    embed.add_field(name="Entries", value=f"**{len(entries)}**", inline=False)
    embed.add_field(name="Winners", value=winners_text, inline=False)
    embed.set_footer(text=f"Winners:{giveaway['winner_count']}")

    await message.edit(embed=embed, view=None)

    # Delete from DB
    cursor.execute("DELETE FROM giveaway_entries WHERE message_id = %s", (message_id,))
    cursor.execute("DELETE FROM giveaways WHERE message_id = %s", (message_id,))
    conn.commit()

    GIVEAWAYS.pop(message_id, None)


# ---------------- RECOVER GIVEAWAYS (DB‑BACKED) ---------------- #

async def recover_giveaways():
    await bot.wait_until_ready()
    await debug_log("Recovering giveaways from DB...")

    # Load all active giveaways
    cursor.execute(
        "SELECT * FROM giveaways WHERE end_time > NOW()"
    )
    rows = cursor.fetchall()

    for row in rows:
        message_id = row["message_id"]
        channel_id = row["channel_id"]

        # Load entries
        cursor.execute(
            "SELECT user_id FROM giveaway_entries WHERE message_id = %s",
            (message_id,)
        )
        entry_rows = cursor.fetchall()
        entries = {r["user_id"] for r in entry_rows}

        GIVEAWAYS[message_id] = {
            "entries": entries,
            "end_time": row["end_time"],
            "winner_count": row["winner_count"],
            "title": row["title"],
            "host_id": row["host_id"],
            "channel_id": channel_id
        }

        # Reattach view
        channel = bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(channel_id)
            except:
                continue

        try:
            msg = await channel.fetch_message(message_id)
            await msg.edit(view=GiveawayView(message_id))
        except:
            continue

        # Restart countdown
        bot.loop.create_task(end_giveaway(message_id, channel_id))

    await debug_log("Giveaway recovery complete.")
# ---------------- GIVEAWAY COMMAND (DB‑BACKED) ---------------- #

@bot.tree.command(name="giveaway", description="Create a giveaway")
@app_commands.describe(
    title="Title of the giveaway",
    time="Duration like '1h 30m', '2h', '45m', '1d 2h'",
    winnercount="Number of winners"
)
@app_commands.checks.has_permissions(manage_guild=True)
async def giveaway(interaction: discord.Interaction, title: str, time: str, winnercount: int):

    # Parse time
    try:
        delta = parse_time_string(time)
    except ValueError as e:
        await interaction.response.send_message(str(e), ephemeral=True)
        return

    if winnercount < 1:
        await interaction.response.send_message("Winner count must be at least 1.", ephemeral=True)
        return

    end_time = datetime.now(UTC) + delta
    unix = int(end_time.timestamp())

    # Build embed
    embed = discord.Embed(
        title=title,
        color=discord.Color.from_rgb(255, 255, 255)
    )
    embed.add_field(name="Ends", value=f"<t:{unix}:R>", inline=False)
    embed.add_field(name="Hosted by", value=interaction.user.mention, inline=False)
    embed.add_field(name="Entries", value="**0**", inline=False)

    # Store winner count in footer for recovery
    embed.set_footer(text=f"Winners:{winnercount}")

    await interaction.response.defer()

    # Send giveaway message
    message = await interaction.channel.send(embed=embed)

    # Save giveaway to DB
    cursor.execute(
        """
        INSERT INTO giveaways (message_id, channel_id, title, end_time, winner_count, host_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (message_id) DO NOTHING
        """,
        (message.id, interaction.channel.id, title, end_time, winnercount, interaction.user.id)
    )
    conn.commit()

    # Save in memory
    GIVEAWAYS[message.id] = {
        "entries": set(),
        "end_time": end_time,
        "winner_count": winnercount,
        "title": title,
        "host_id": interaction.user.id,
        "channel_id": interaction.channel.id
    }

    # Attach view
    view = GiveawayView(message.id)
    await message.edit(view=view)

    # Start countdown
    bot.loop.create_task(end_giveaway(message.id, interaction.channel.id))

    await interaction.followup.send(
        f"Giveaway created for **{title}** ending at `<t:{unix}:R>`.",
        ephemeral=True
    )

    await debug_log(f"Giveaway created: {title} | message_id={message.id} | host={interaction.user.id}")
# ---------------- BAN ROLE SYSTEM ---------------- #

async def update_roles(member):
    main_guild = bot.get_guild(MAIN_GUILD_ID)
    appeal_guild = bot.get_guild(APPEAL_GUILD_ID)

    banned_role = appeal_guild.get_role(BANNED_ROLE_ID)
    not_banned_role = appeal_guild.get_role(NOT_BANNED_ROLE_ID)

    try:
        await main_guild.fetch_ban(member)
        banned = True
    except discord.NotFound:
        banned = False

    if banned:
        if banned_role not in member.roles:
            await member.add_roles(banned_role)
        if not_banned_role in member.roles:
            await member.remove_roles(not_banned_role)
    else:
        if not_banned_role not in member.roles:
            await member.add_roles(not_banned_role)
        if banned_role in member.roles:
            await member.remove_roles(banned_role)


@tasks.loop(minutes=10)
async def check_bans():
    guild = bot.get_guild(APPEAL_GUILD_ID)
    for member in guild.members:
        await update_roles(member)


# ---------------- ROLE SYNC MAIN → APPEAL ---------------- #

async def sync_member_roles(member):
    main_guild = bot.get_guild(MAIN_GUILD_ID)
    appeal_guild = bot.get_guild(APPEAL_GUILD_ID)

    main_member = main_guild.get_member(member.id)
    appeal_member = appeal_guild.get_member(member.id)

    if not main_member or not appeal_member:
        return

    main_role = main_guild.get_role(MAIN_SYNC_ROLE)
    appeal_role = appeal_guild.get_role(APPEAL_SYNC_ROLE)

    if main_role in main_member.roles:
        if appeal_role not in appeal_member.roles:
            await appeal_member.add_roles(appeal_role)
    else:
        if appeal_role in appeal_member.roles:
            await appeal_member.remove_roles(appeal_role)


@tasks.loop(seconds=60)
async def sync_roles_task():
    appeal_guild = bot.get_guild(APPEAL_GUILD_ID)
    for member in appeal_guild.members:
        await sync_member_roles(member)


# ---------------- AUTO THUMBS UP ---------------- #

REACTION_CHANNELS = [
    1482516620730433625,
    1478798153288384624,
    1482514244997091479
]

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if message.channel.id in REACTION_CHANNELS:
        try:
            await message.add_reaction("👍")
        except:
            pass

    await bot.process_commands(message)


async def react_to_old_messages():
    await bot.wait_until_ready()
    for channel_id in REACTION_CHANNELS:
        channel = bot.get_channel(channel_id)
        if channel is None:
            continue

        async for msg in channel.history(limit=None):
            try:
                if not any(str(r.emoji) == "👍" for r in msg.reactions):
                    await msg.add_reaction("👍")
            except:
                pass


# ---------------- APPEAL PANEL ---------------- #

class AppealModal(Modal):
    def __init__(self):
        super().__init__(title="RoomMates Ban Appeal")

        self.username = TextInput(
            label="What's your username?"
        )
        self.justified = TextInput(
            label="Do you think your ban was justified?",
            style=discord.TextStyle.paragraph
        )
        self.reason = TextInput(
            label="Why should you be unbanned?",
            style=discord.TextStyle.paragraph
        )

        self.add_item(self.username)
        self.add_item(self.justified)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        review_channel = bot.get_channel(APPEAL_REVIEW_CHANNEL)
        main_guild = bot.get_guild(MAIN_GUILD_ID)

        try:
            ban = await main_guild.fetch_ban(interaction.user)
            ban_reason = ban.reason or "No reason provided"
        except:
            ban_reason = "Unknown"

        embed = discord.Embed(
            title="New Ban Appeal",
            color=discord.Color.orange()
        )
        embed.add_field(name="User", value=f"{interaction.user} ({interaction.user.id})", inline=False)
        embed.add_field(name="Username", value=self.username.value, inline=False)
        embed.add_field(name="Ban Reason", value=ban_reason, inline=False)
        embed.add_field(name="Was Ban Justified?", value=self.justified.value, inline=False)
        embed.add_field(name="Why Unban?", value=self.reason.value, inline=False)

        await review_channel.send(embed=embed)
        await interaction.response.send_message("Your appeal has been submitted.", ephemeral=True)


class AppealPanel(View):
    def __init__(self):
        super().__init__(timeout=None)  # REQUIRED for persistent views

    @discord.ui.button(
        label="DISCORD APPEAL",
        style=discord.ButtonStyle.success,
        emoji="🔨",
        custom_id="appeal_button"  # REQUIRED for persistent views
    )
    async def appeal(self, interaction: discord.Interaction, button):
        banned_role = interaction.guild.get_role(BANNED_ROLE_ID)

        if banned_role not in interaction.user.roles:
            await interaction.response.send_message("You are not banned.", ephemeral=True)
            return

        await interaction.response.send_modal(AppealModal())


async def send_panel():
    channel = bot.get_channel(PANEL_CHANNEL_ID)

    # Prevent duplicates
    async for msg in channel.history(limit=20):
        if msg.author == bot.user:
            return

    embed = discord.Embed(
        title="🏠 RoomMates VC Ban Appeals",
        description="Press **DISCORD APPEAL** to submit a ban appeal.",
        color=discord.Color.green()
    )

    await channel.send(embed=embed, view=AppealPanel())


# ---------------- SUPPORT PANEL ---------------- #

class SupportView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Discord Support Ticket", style=discord.ButtonStyle.success, emoji="📩")
    async def discord_ticket(self, interaction: discord.Interaction, button):
        await interaction.response.send_message("Support ticket system not connected yet.", ephemeral=True)


async def send_support_panel():
    channel = bot.get_channel(SUPPORT_CHANNEL_ID)
    async for msg in channel.history(limit=20):
        if msg.author == bot.user:
            return

    embed = discord.Embed(
        title="Support",
        description="Need help? Open a ticket.",
        color=discord.Color.purple()
    )
    await channel.send(embed=embed, view=SupportView())


async def send_panel():
    channel = bot.get_channel(PANEL_CHANNEL_ID)
    async for msg in channel.history(limit=20):
        if msg.author == bot.user:
            return

    embed = discord.Embed(
        title="🏠 RoomMates VC Ban Appeals",
        description="Press **DISCORD APPEAL** to submit a ban appeal.",
        color=discord.Color.green()
    )
    await channel.send(embed=embed, view=AppealPanel())


# ---------------- AUTO‑LOCK SYSTEM ---------------- #

async def perform_channel_lock(channel: discord.TextChannel):
    everyone = channel.guild.default_role
    overwrites = channel.overwrites
    perms = overwrites.get(everyone, discord.PermissionOverwrite())

    if perms.send_messages is False:
        return

    perms.send_messages = False
    overwrites[everyone] = perms
    await channel.edit(overwrites=overwrites)


async def lock_channel_after_24h():
    await bot.wait_until_ready()

    channel = bot.get_channel(AUTO_LOCK_CHANNEL_ID)
    if channel is None:
        return

    created = channel.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)

    target = created + timedelta(hours=24)
    now = datetime.now(UTC)

    if now >= target:
        await perform_channel_lock(channel)
        return

    delay = (target - now).total_seconds()
    await asyncio.sleep(delay)
    await perform_channel_lock(channel)


# ---------------- INVITE TRACKING ---------------- #

def get_invites(user_id: str):
    cursor.execute("SELECT regular, left_count FROM invites WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    if row:
        return row["regular"], row["left_count"]
    return 0, 0


def add_invite(user_id: str):
    regular, left_count = get_invites(user_id)
    regular += 1
    cursor.execute("""
        INSERT INTO invites (user_id, regular, left_count)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id)
        DO UPDATE SET regular = EXCLUDED.regular
    """, (user_id, regular, left_count))
    conn.commit()


def remove_invite(user_id: str):
    regular, left_count = get_invites(user_id)
    if regular > 0:
        regular -= 1
        left_count += 1
    cursor.execute("""
        INSERT INTO invites (user_id, regular, left_count)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id)
        DO UPDATE SET regular = EXCLUDED.regular, left_count = EXCLUDED.left_count
    """, (user_id, regular, left_count))
    conn.commit()
# ---------------- READY EVENT ---------------- #

@bot.event
async def on_ready():
    print(f"[DEBUG] Logged in as {bot.user}")

    # Build invite cache
    for guild in bot.guilds:
        try:
            invites = await guild.invites()
            invite_cache[guild.id] = {i.code: i.uses for i in invites}
        except:
            invite_cache[guild.id] = {}

    # Register persistent views
    bot.add_view(AppealPanel())
    bot.add_view(SupportView())

    # Start tasks
    check_bans.start()
    sync_roles_task.start()
    bot.loop.create_task(react_to_old_messages())
    bot.loop.create_task(recover_giveaways())
    bot.loop.create_task(lock_channel_after_24h())

    # Send panels if missing
    await send_panel()
    await send_support_panel()

    # Sync slash commands
    try:
        await bot.tree.sync()
    except Exception as e:
        print(f"[DEBUG] Failed to sync commands: {e}")

    print("[DEBUG] Bot is fully ready.")


# ---------------- MEMBER JOIN ---------------- #

@bot.event
async def on_member_join(member):
    guild = member.guild

    # Update ban roles if joining appeal server
    if guild.id == APPEAL_GUILD_ID:
        await update_roles(member)
        await sync_member_roles(member)

    # Fetch current invites
    try:
        invites = await guild.invites()
    except:
        return

    used = None
    for invite in invites:
        old_uses = invite_cache.get(guild.id, {}).get(invite.code, 0)
        if invite.uses > old_uses:
            used = invite
            break

    # Update cache
    invite_cache[guild.id] = {i.code: i.uses for i in invites}

    if used:
        inviter_id = str(used.inviter.id)
        add_invite(inviter_id)
        regular, left_count = get_invites(inviter_id)

        channel = bot.get_channel(INVITE_CHANNEL)
        if channel:
            await channel.send(
                f"{member.mention} joined using {used.inviter.mention}'s invite! "
                f"They now have **{regular} invites.**"
            )


# ---------------- MEMBER LEAVE ---------------- #

@bot.event
async def on_member_remove(member):
    guild = member.guild

    # Remove 1 invite from top inviter (your original logic)
    cursor.execute("SELECT user_id, regular FROM invites ORDER BY regular DESC LIMIT 1")
    row = cursor.fetchone()

    if row:
        inviter_id = row["user_id"]
        remove_invite(inviter_id)
        regular, left_count = get_invites(inviter_id)

        channel = bot.get_channel(INVITE_CHANNEL)
        if channel:
            await channel.send(
                f"{member.name} left the server. "
                f"Removed **1 invite** from <@{inviter_id}>.\n"
                f"They now have **{regular} regular** and **{left_count} left** invites."
            )


# ---------------- INVITES COMMAND ---------------- #

@bot.tree.command(name="invites", description="Check how many invites a user has")
@app_commands.describe(user="User to check")
async def invites(interaction: discord.Interaction, user: discord.Member = None):
    if user is None:
        user = interaction.user

    regular, left_count = get_invites(str(user.id))

    embed = discord.Embed(
        title=f"{user.display_name}",
        description=f"You currently have **{regular} invites.**",
        color=discord.Color.from_rgb(255, 255, 255)
    )
    embed.add_field(
        name="Invites",
        value=f"{regular} regular\n{left_count} left",
        inline=False
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text=f"Requested by {interaction.user.display_name}")

    await interaction.response.send_message(embed=embed)


# ---------------- INVITETOP COMMAND ---------------- #

@bot.tree.command(name="invitetop", description="Show the top inviters in the server")
async def invitetop(interaction: discord.Interaction):
    cursor.execute("""
        SELECT user_id, regular, left_count
        FROM invites
        ORDER BY regular DESC
        LIMIT 10
    """)
    rows = cursor.fetchall()

    if not rows:
        await interaction.response.send_message("No invite data found.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🏆 Top Inviters",
        color=discord.Color.from_rgb(255, 255, 255)
    )

    description = ""
    for index, row in enumerate(rows, start=1):
        user_id = int(row["user_id"])
        regular = row["regular"]
        left_count = row["left_count"]

        user = interaction.guild.get_member(user_id) or await bot.fetch_user(user_id)

        description += (
            f"**#{index} — {user.mention if user else user_id}**\n"
            f"Invites: **{regular}** | Left: **{left_count}**\n\n"
        )

    embed.description = description
    embed.set_footer(text=f"Requested by {interaction.user.display_name}")

    await interaction.response.send_message(embed=embed)


# ---------------- ADD INVITE COMMAND ---------------- #

@bot.tree.command(name="addinvite", description="Add invites to a user")
@app_commands.checks.has_permissions(manage_guild=True)
@app_commands.describe(user="User to add invites to", amount="How many invites to add")
async def addinvite(interaction: discord.Interaction, user: discord.Member, amount: int):
    if amount < 1:
        await interaction.response.send_message("Amount must be at least 1.", ephemeral=True)
        return

    regular, left_count = get_invites(str(user.id))
    regular += amount

    cursor.execute("""
        INSERT INTO invites (user_id, regular, left_count)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id)
        DO UPDATE SET regular = EXCLUDED.regular
    """, (str(user.id), regular, left_count))
    conn.commit()

    await interaction.response.send_message(
        f"Added **{amount} invites** to {user.mention}. They now have **{regular} regular invites**.",
        ephemeral=True
    )


# ---------------- GAMELINK COMMAND ---------------- #

@bot.command()
async def gamelink(ctx):
    await ctx.send("https://www.roblox.com/share?code=91a1d9f9e2d8234f9d477e1e75736b34&type=ExperienceDetails&stamp=1773867741632")


# ---------------- RUN BOT ---------------- #

bot.run(TOKEN)
