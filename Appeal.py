import discord
import os
import asyncio
import random
import re
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta, UTC
from discord.ext import commands, tasks
from discord.ui import View, Modal, TextInput, Button
from discord import app_commands

TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

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

print("[DEBUG] Connecting to PostgreSQL...")
conn = psycopg2.connect(DATABASE_URL, sslmode="require")
cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
print("[DEBUG] Connected to PostgreSQL.")

cursor.execute("""
CREATE TABLE IF NOT EXISTS invites (
    user_id TEXT PRIMARY KEY,
    regular INTEGER DEFAULT 0,
    left_count INTEGER DEFAULT 0
)
""")
conn.commit()

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

MAIN_SYNC_ROLE = 1485407866453102732
APPEAL_SYNC_ROLE = 1482444572687859773

TARGET_THREAD_CHANNEL = 1486456524187631869
THREAD_LIFETIME = 24 * 60 * 60

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

invite_cache = {}
GIVEAWAYS = {}

def parse_time_string(time_str: str) -> timedelta:
    pattern = r"(\d+)\s*([dhm])"
    matches = re.findall(pattern, time_str.lower())
    if not matches:
        raise ValueError("Invalid time format.")
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
class GiveawayView(View):
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success, emoji="🎉", custom_id="giveaway_join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        giveaway = GIVEAWAYS.get(self.message_id)
        if not giveaway:
            await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
            return

        user_id = interaction.user.id
        if user_id in giveaway["entries"]:
            await interaction.response.send_message("You already joined.", ephemeral=True)
            return

        giveaway["entries"].add(user_id)

        try:
            message = await interaction.channel.fetch_message(self.message_id)
        except:
            await interaction.response.send_message("Entry saved but embed couldn't update.", ephemeral=True)
            return

        unix = int(giveaway["end_time"].timestamp())

        embed = discord.Embed(
            title=giveaway['title'],
            color=discord.Color.from_rgb(255, 255, 255)
        )
        embed.add_field(name="Ends", value=f"<t:{unix}:R>", inline=False)
        embed.add_field(name="Hosted by", value=f"<@{giveaway['host_id']}>", inline=False)
        embed.add_field(name="Entries", value=f"**{len(giveaway['entries'])}**", inline=False)

        await message.edit(embed=embed, view=self)
        await interaction.response.send_message("You joined the giveaway!", ephemeral=True)

async def end_giveaway(message_id: int, channel_id: int):
    await bot.wait_until_ready()
    giveaway = GIVEAWAYS.get(message_id)
    if not giveaway:
        return

    now = datetime.now(UTC)
    remaining = (giveaway["end_time"] - now).total_seconds()
    if remaining > 0:
        await asyncio.sleep(remaining)

    giveaway = GIVEAWAYS.get(message_id)
    if not giveaway:
        return

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except:
            GIVEAWAYS.pop(message_id, None)
            return

    try:
        message = await channel.fetch_message(message_id)
    except:
        GIVEAWAYS.pop(message_id, None)
        return

    entries = list(giveaway["entries"])
    winner_count = giveaway["winner_count"]

    if not entries:
        winners_text = "No valid entries."
    else:
        if winner_count > len(entries):
            winner_count = len(entries)
        winners = random.sample(entries, winner_count)
        winners_text = ", ".join(f"<@{uid}>" for uid in winners)

    unix = int(giveaway["end_time"].timestamp())

    embed = discord.Embed(
        title=giveaway['title'],
        color=discord.Color.from_rgb(255, 255, 255)
    )
    embed.add_field(name="Ended", value=f"<t:{unix}:R>", inline=False)
    embed.add_field(name="Hosted by", value=f"<@{giveaway['host_id']}>", inline=False)
    embed.add_field(name="Entries", value=f"**{len(entries)}**", inline=False)
    embed.add_field(name="Winners", value=winners_text, inline=False)

    await message.edit(embed=embed, view=None)
    GIVEAWAYS.pop(message_id, None)

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

async def sync_member_roles(member):
    main_guild = bot.get_guild(MAIN_GUILD_ID)
    appeal_guild = bot.get_guild(APPEAL_GUILD_ID)

    if not main_guild or not appeal_guild:
        return

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
    main_guild = bot.get_guild(MAIN_GUILD_ID)
    appeal_guild = bot.get_guild(APPEAL_GUILD_ID)

    if not main_guild or not appeal_guild:
        return

    for member in appeal_guild.members:
        await sync_member_roles(member)
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

        async for message in channel.history(limit=None):
            try:
                if not any(str(r.emoji) == "👍" for r in message.reactions):
                    await message.add_reaction("👍")
            except:
                pass

@bot.event
async def on_thread_update(before: discord.Thread, after: discord.Thread):
    if after.archived and not after.locked:
        try:
            await after.edit(locked=True)
        except:
            pass

@tasks.loop(minutes=30)
async def auto_lock_existing_threads():
    await bot.wait_until_ready()
    for guild in bot.guilds:
        for channel in guild.text_channels:
            try:
                threads = await channel.threads()
            except:
                continue
            for thread in threads:
                if thread.archived and not thread.locked:
                    try:
                        await thread.edit(locked=True)
                    except:
                        pass

@tasks.loop(minutes=10)
async def auto_close_old_threads():
    await bot.wait_until_ready()
    channel = bot.get_channel(TARGET_THREAD_CHANNEL)
    if channel is None:
        return

    try:
        threads = await channel.threads()
    except:
        return

    now = datetime.now(UTC)

    for thread in threads:
        if thread.locked:
            continue

        age = (now - thread.created_at).total_seconds()

        if age >= THREAD_LIFETIME:
            try:
                await thread.edit(archived=True, locked=True)
            except:
                pass

class AppealModal(Modal):
    def __init__(self):
        super().__init__(title="RoomMates Ban Appeal")
        self.username = TextInput(label="What's your username?")
        self.justified = TextInput(label="Do you think your ban was justified?", style=discord.TextStyle.paragraph)
        self.reason = TextInput(label="Why should you be unbanned?", style=discord.TextStyle.paragraph)

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

        embed = discord.Embed(title="New Ban Appeal", color=discord.Color.orange())
        embed.add_field(name="User", value=f"{interaction.user} ({interaction.user.id})", inline=False)
        embed.add_field(name="Username", value=self.username.value, inline=False)
        embed.add_field(name="Ban Reason", value=ban_reason, inline=False)
        embed.add_field(name="Was Ban Justified?", value=self.justified.value, inline=False)
        embed.add_field(name="Why Unban?", value=self.reason.value, inline=False)

        view = StaffReviewView(interaction.user.id)
        await review_channel.send(embed=embed, view=view)

        await interaction.response.send_message("Your appeal has been submitted.", ephemeral=True)

class StaffReviewView(View):
    def __init__(self, user_id):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        main_guild = bot.get_guild(MAIN_GUILD_ID)
        appeal_guild = bot.get_guild(APPEAL_GUILD_ID)
        accepted_channel = bot.get_channel(ACCEPTED_CHANNEL)

        user = await bot.fetch_user(self.user_id)

        try:
            await main_guild.unban(user)
        except:
            pass

        member = appeal_guild.get_member(self.user_id)
        if member:
            role = appeal_guild.get_role(ACCEPTED_ROLE)
            await member.add_roles(role)

        await accepted_channel.send(f"{user.mention} your appeal has been accepted.")

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.add_field(name="Result", value=f"Accepted by {interaction.user.mention}", inline=False)

        await interaction.message.edit(embed=embed, view=None)
        await interaction.response.send_message("Appeal accepted.", ephemeral=True)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()
        embed.add_field(name="Result", value=f"Denied by {interaction.user.mention}", inline=False)

        await interaction.message.edit(embed=embed, view=None)
        await interaction.response.send_message("Appeal denied.", ephemeral=True)
class AppealPanel(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="DISCORD APPEAL", style=discord.ButtonStyle.success, emoji="🔨", custom_id="appeal_here")
    async def appeal(self, interaction: discord.Interaction, button: discord.ui.Button):
        banned_role = interaction.guild.get_role(BANNED_ROLE_ID)

        if banned_role not in interaction.user.roles:
            await interaction.response.send_message("You cannot appeal because you are not banned.", ephemeral=True)
            return

        await interaction.response.send_modal(AppealModal())

    @discord.ui.button(label="GAME APPEAL", style=discord.ButtonStyle.primary, emoji="🎮", custom_id="game_appeal")
    async def game_appeal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Game appeal system coming soon.", ephemeral=True)

    @discord.ui.button(label="Ban Case", style=discord.ButtonStyle.secondary, emoji="📄", custom_id="ban_case")
    async def case(self, interaction: discord.Interaction, button: discord.ui.Button):
        main_guild = bot.get_guild(MAIN_GUILD_ID)

        try:
            ban = await main_guild.fetch_ban(interaction.user)
            reason = ban.reason or "No reason provided"
            embed = discord.Embed(title="Your Ban Case", description=f"Reason: {reason}", color=discord.Color.red())
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except:
            await interaction.response.send_message("You are not banned in the main server.", ephemeral=True)

class SupportView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Discord Support Ticket", style=discord.ButtonStyle.success, emoji="📩", custom_id="support_discord")
    async def discord_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Support ticket feature not connected yet.", ephemeral=True)

    @discord.ui.button(label="Create In-game Support Ticket", style=discord.ButtonStyle.secondary, emoji="📩", custom_id="support_ingame")
    async def ingame_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("In-game support not connected yet.", ephemeral=True)

async def send_support_panel():
    channel = bot.get_channel(SUPPORT_CHANNEL_ID)
    if channel is None:
        channel = await bot.fetch_channel(SUPPORT_CHANNEL_ID)

    async for msg in channel.history(limit=20):
        if msg.author == bot.user:
            return

    embed = discord.Embed(
        title="Support",
        description=(
            "**🎟️ Need Help?**\n\n"
            "If you're experiencing an issue, our support team is here to help.\n\n"
            "**Before opening a ticket, please remember:**\n"
            "• Staff will respond as soon as possible after your ticket is created\n"
            "• You can also use the **in-game support button**\n"
        ),
        color=discord.Color.purple()
    )

    await channel.send(embed=embed, view=SupportView())

async def send_panel():
    channel = bot.get_channel(PANEL_CHANNEL_ID)
    if channel is None:
        channel = await bot.fetch_channel(PANEL_CHANNEL_ID)

    async for msg in channel.history(limit=20):
        if msg.author == bot.user:
            return

    embed = discord.Embed(
        title="🏠 RoomMates VC Ban Appeals",
        description=(
            "Welcome to the **RoomMates VC Ban Appeal System**.\n\n"
            "**How to appeal**\n"
            "Press **🔨 DISCORD APPEAL** and complete the form.\n\n"
            "**What happens next?**\n"
            "• Staff will review your appeal.\n"
            "• If accepted you will be notified.\n"
            "• If declined after **7 days**, you may appeal again.\n\n"
            "You can view your **ban reason** using the Ban Case button."
        ),
        color=discord.Color.green()
    )

    await channel.send(embed=embed, view=AppealPanel())

@bot.tree.command(name="slowmode", description="Set slowmode for a channel")
@app_commands.describe(
    channel="Channel to apply slowmode to",
    time="Slowmode duration in seconds (0 to disable)"
)
@app_commands.checks.has_permissions(manage_channels=True)
async def slowmode(interaction: discord.Interaction, channel: discord.TextChannel, time: int):
    if time < 0:
        await interaction.response.send_message("Slowmode time cannot be negative.", ephemeral=True)
        return

    try:
        await channel.edit(slowmode_delay=time)

        if time == 0:
            await interaction.response.send_message(
                f"Slowmode disabled in {channel.mention}.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"Slowmode set to **{time} seconds** in {channel.mention}.", ephemeral=True
            )

    except Exception:
        await interaction.response.send_message("Failed to update slowmode.", ephemeral=True)
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

@bot.tree.command(name="giveaway", description="Create a giveaway")
@app_commands.describe(
    title="Title of the giveaway",
    time="Duration like '1h 30m', '2h', '45m', '1d 2h'",
    winnercount="Number of winners"
)
@app_commands.checks.has_permissions(manage_guild=True)
async def giveaway(interaction: discord.Interaction, title: str, time: str, winnercount: int):
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

    embed = discord.Embed(title=title, color=discord.Color.from_rgb(255, 255, 255))
    embed.add_field(name="Ends", value=f"<t:{unix}:R>", inline=False)
    embed.add_field(name="Hosted by", value=interaction.user.mention, inline=False)
    embed.add_field(name="Entries", value="**0**", inline=False)

    await interaction.response.defer()
    message = await interaction.channel.send(embed=embed)

    GIVEAWAYS[message.id] = {
        "entries": set(),
        "end_time": end_time,
        "winner_count": winnercount,
        "title": title,
        "host_id": interaction.user.id,
        "channel_id": interaction.channel.id
    }

    view = GiveawayView(message.id)
    await message.edit(view=view)

    bot.loop.create_task(end_giveaway(message.id, interaction.channel.id))

    await interaction.followup.send(
        f"Giveaway created for **{title}** ending at `<t:{unix}:R>`.",
        ephemeral=True
    )
@bot.event
async def on_ready():
    print(f"[DEBUG] Logged in as {bot.user}")

    for guild in bot.guilds:
        try:
            invites = await guild.invites()
            invite_cache[guild.id] = {i.code: i.uses for i in invites}
        except:
            invite_cache[guild.id] = {}

    bot.add_view(AppealPanel())
    bot.add_view(SupportView())

    check_bans.start()
    sync_roles_task.start()
    bot.loop.create_task(react_to_old_messages())
    auto_lock_existing_threads.start()
    auto_close_old_threads.start()

    await send_panel()
    await send_support_panel()

    try:
        await bot.tree.sync()
    except:
        pass

@bot.event
async def on_member_join(member):
    if member.guild.id == APPEAL_GUILD_ID:
        await update_roles(member)
        await sync_member_roles(member)

    guild = member.guild

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

@bot.event
async def on_member_remove(member):
    guild = member.guild

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

@bot.command()
async def gamelink(ctx):
    await ctx.send("https://www.roblox.com/share?code=91a1d9f9e2d8234f9d477e1e75736b34&type=ExperienceDetails&stamp=1773867741632")

print("[DEBUG] Starting bot...")
bot.run(TOKEN)
