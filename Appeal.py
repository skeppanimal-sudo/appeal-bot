import discord
import os
import asyncio
import random
import re
import traceback

import psycopg2
import psycopg2.extras

from datetime import datetime, timedelta, UTC
from discord.ext import commands, tasks
from discord.ui import View, Modal, TextInput, Button
from discord import app_commands

# ========================
# ENV
# ========================

TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

DEBUG_CHANNEL_ID = 1487755467949211709

# ========================
# BOT SETUP
# ========================

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

invite_cache = {}
GIVEAWAYS = {}

# ========================
# DATABASE
# ========================

print("[DEBUG] Connecting to PostgreSQL...")


def get_connection():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


conn = get_connection()

print("[DEBUG] Connected to PostgreSQL.")


def ensure_connection():
    global conn
    try:
        conn.poll()
    except Exception:
        print("[DEBUG] Reconnecting to PostgreSQL...")
        conn = get_connection()


def setup_database():
    ensure_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS invites (
            user_id TEXT PRIMARY KEY,
            regular INTEGER DEFAULT 0,
            left_count INTEGER DEFAULT 0
        )
        """)
        conn.commit()


setup_database()

# ========================
# LOGGING SYSTEM
# ========================

async def debug_log(text: str):
    print(f"[DEBUG] {text}")

    try:
        if bot.is_ready():
            channel = bot.get_channel(DEBUG_CHANNEL_ID)

            if channel is None:
                channel = await bot.fetch_channel(DEBUG_CHANNEL_ID)

            await channel.send(f"```[DEBUG]\n{text}\n```")

    except Exception as e:
        print(f"[DEBUG ERROR] {e}")


async def log_error(context: str, error: Exception):
    err = f"{context}\n{str(error)}\n\n{traceback.format_exc()}"
    await debug_log(err)

# ========================
# INVITE FUNCTIONS
# ========================

def get_invites(user_id: str):
    ensure_connection()

    with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cursor:
        cursor.execute(
            "SELECT regular, left_count FROM invites WHERE user_id = %s",
            (user_id,)
        )
        row = cursor.fetchone()

        if row:
            return row["regular"], row["left_count"]

        return 0, 0


def add_invite(user_id: str):
    ensure_connection()

    regular, left_count = get_invites(user_id)
    regular += 1

    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT INTO invites (user_id, regular, left_count)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id)
            DO UPDATE SET regular = EXCLUDED.regular
        """, (user_id, regular, left_count))

        conn.commit()


def remove_invite(user_id: str):
    ensure_connection()

    regular, left_count = get_invites(user_id)

    if regular > 0:
        regular -= 1
        left_count += 1

    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT INTO invites (user_id, regular, left_count)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id)
            DO UPDATE SET regular = EXCLUDED.regular,
                          left_count = EXCLUDED.left_count
        """, (user_id, regular, left_count))

        conn.commit()
        # ========================
# CONSTANTS
# ========================

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

MESSAGE_PANEL_CHANNEL_ID = 1487531080335626380

QOTD_ROLE_ID = 1480033021326524427
QOTD_CHANNEL_ID = 1486456524187631869

REACTION_CHANNELS = [
    1482516620730433625,
    1478798153288384624,
    1482514244997091479
]

# ========================
# UTILITIES
# ========================

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

# ========================
# GIVEAWAY SYSTEM
# ========================

class GiveawayView(View):
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success, emoji="🎉", custom_id="giveaway_join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            giveaway = GIVEAWAYS.get(self.message_id)

            if not giveaway:
                await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
                return

            user_id = interaction.user.id

            if user_id in giveaway["entries"]:
                await interaction.response.send_message("You already joined.", ephemeral=True)
                return

            giveaway["entries"].add(user_id)

            message = await interaction.channel.fetch_message(self.message_id)

            unix = int(giveaway["end_time"].timestamp())

            embed = discord.Embed(
                title=giveaway['title'],
                color=discord.Color.white()
            )
            embed.add_field(name="Ends", value=f"<t:{unix}:R>", inline=False)
            embed.add_field(name="Hosted by", value=f"<@{giveaway['host_id']}>", inline=False)
            embed.add_field(name="Entries", value=f"**{len(giveaway['entries'])}**", inline=False)

            await message.edit(embed=embed, view=self)

            await interaction.response.send_message("You joined the giveaway!", ephemeral=True)

            await debug_log(f"{interaction.user} joined giveaway {self.message_id}")

        except Exception as e:
            await log_error("Giveaway join failed", e)


async def end_giveaway(message_id: int, channel_id: int):
    await bot.wait_until_ready()

    try:
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

        channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
        message = await channel.fetch_message(message_id)

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
            color=discord.Color.white()
        )
        embed.add_field(name="Ended", value=f"<t:{unix}:R>", inline=False)
        embed.add_field(name="Hosted by", value=f"<@{giveaway['host_id']}>", inline=False)
        embed.add_field(name="Entries", value=f"**{len(entries)}**", inline=False)
        embed.add_field(name="Winners", value=winners_text, inline=False)

        await message.edit(embed=embed, view=None)

        GIVEAWAYS.pop(message_id, None)

        await debug_log(f"Giveaway ended: {message_id} | Winners: {winners_text}")

    except Exception as e:
        await log_error("End giveaway failed", e)
        # ========================
# ROLE + BAN SYSTEM
# ========================

async def update_roles(member):
    try:
        main_guild = bot.get_guild(MAIN_GUILD_ID)
        appeal_guild = bot.get_guild(APPEAL_GUILD_ID)

        if not main_guild or not appeal_guild:
            return

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

        await debug_log(f"Updated roles for {member} | banned={banned}")

    except Exception as e:
        await log_error("update_roles failed", e)


@tasks.loop(minutes=10)
async def check_bans():
    await bot.wait_until_ready()

    try:
        guild = bot.get_guild(APPEAL_GUILD_ID)

        for member in guild.members:
            await update_roles(member)

        await debug_log("Ban check loop completed")

    except Exception as e:
        await log_error("check_bans loop failed", e)


# ========================
# ROLE SYNC SYSTEM
# ========================

async def sync_member_roles(member):
    try:
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

        await debug_log(f"Synced roles for {member}")

    except Exception as e:
        await log_error("sync_member_roles failed", e)


@tasks.loop(seconds=60)
async def sync_roles_task():
    await bot.wait_until_ready()

    try:
        appeal_guild = bot.get_guild(APPEAL_GUILD_ID)

        for member in appeal_guild.members:
            await sync_member_roles(member)

        await debug_log("Role sync loop completed")

    except Exception as e:
        await log_error("sync_roles_task failed", e)


# ========================
# REACTION SYSTEM
# ========================

@bot.event
async def on_message(message):
    try:
        if message.author.bot:
            return

        if message.channel.id in REACTION_CHANNELS:
            try:
                await message.add_reaction("👍")
            except:
                pass

        await bot.process_commands(message)

    except Exception as e:
        await log_error("on_message failed", e)


async def react_to_old_messages():
    await bot.wait_until_ready()

    try:
        for channel_id in REACTION_CHANNELS:
            channel = bot.get_channel(channel_id)

            if channel is None:
                continue

            async for message in channel.history(limit=200):
                try:
                    if not any(str(r.emoji) == "👍" for r in message.reactions):
                        await message.add_reaction("👍")
                except:
                    pass

        await debug_log("Old messages reacted successfully")

    except Exception as e:
        await log_error("react_to_old_messages failed", e)


# ========================
# THREAD SYSTEM
# ========================

@bot.event
async def on_thread_update(before: discord.Thread, after: discord.Thread):
    try:
        if after.archived and not after.locked:
            await after.edit(locked=True)
            await debug_log(f"Locked archived thread: {after.name}")

    except Exception as e:
        await log_error("on_thread_update failed", e)


@tasks.loop(minutes=30)
async def auto_lock_existing_threads():
    await bot.wait_until_ready()

    try:
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

        await debug_log("Auto lock threads completed")

    except Exception as e:
        await log_error("auto_lock_existing_threads failed", e)


@tasks.loop(minutes=10)
async def auto_close_old_threads():
    await bot.wait_until_ready()

    try:
        channel = bot.get_channel(TARGET_THREAD_CHANNEL)

        if channel is None:
            return

        threads = await channel.threads()
        now = datetime.now(UTC)

        for thread in threads:
            if thread.locked:
                continue

            age = (now - thread.created_at).total_seconds()

            if age >= THREAD_LIFETIME:
                try:
                    await thread.edit(archived=True, locked=True)
                    await debug_log(f"Auto closed thread: {thread.name}")
                except:
                    pass

    except Exception as e:
        await log_error("auto_close_old_threads failed", e)
        # ========================
# APPEAL SYSTEM
# ========================

class AppealModal(Modal):
    def __init__(self):
        super().__init__(title="RoomMates Ban Appeal")

        self.username = TextInput(label="What's your username?")
        self.justified = TextInput(label="Was your ban justified?", style=discord.TextStyle.paragraph)
        self.reason = TextInput(label="Why should you be unbanned?", style=discord.TextStyle.paragraph)

        self.add_item(self.username)
        self.add_item(self.justified)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        try:
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
            embed.add_field(name="Justified?", value=self.justified.value, inline=False)
            embed.add_field(name="Why Unban?", value=self.reason.value, inline=False)

            view = StaffReviewView(interaction.user.id)

            await review_channel.send(embed=embed, view=view)

            await interaction.response.send_message("Appeal submitted.", ephemeral=True)

            await debug_log(f"Appeal submitted by {interaction.user}")

        except Exception as e:
            await log_error("AppealModal failed", e)


class StaffReviewView(View):
    def __init__(self, user_id):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: Button):
        try:
            if not interaction.user.guild_permissions.manage_guild:
                return await interaction.response.send_message("No permission.", ephemeral=True)

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

            await debug_log(f"Appeal ACCEPTED for {user}")

        except Exception as e:
            await log_error("Accept appeal failed", e)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, button: Button):
        try:
            if not interaction.user.guild_permissions.manage_guild:
                return await interaction.response.send_message("No permission.", ephemeral=True)

            embed = interaction.message.embeds[0]
            embed.color = discord.Color.red()
            embed.add_field(name="Result", value=f"Denied by {interaction.user.mention}", inline=False)

            await interaction.message.edit(embed=embed, view=None)
            await interaction.response.send_message("Appeal denied.", ephemeral=True)

            await debug_log(f"Appeal DENIED for {self.user_id}")

        except Exception as e:
            await log_error("Deny appeal failed", e)


class AppealPanel(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="DISCORD APPEAL", style=discord.ButtonStyle.success, emoji="🔨", custom_id="appeal_here")
    async def appeal(self, interaction: discord.Interaction, button: Button):
        try:
            banned_role = interaction.guild.get_role(BANNED_ROLE_ID)

            if banned_role not in interaction.user.roles:
                return await interaction.response.send_message(
                    "You are not banned.",
                    ephemeral=True
                )

            await interaction.response.send_modal(AppealModal())

        except Exception as e:
            await log_error("Appeal button failed", e)


# ========================
# SUPPORT SYSTEM
# ========================

class SupportView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Discord Support Ticket", style=discord.ButtonStyle.success)
    async def discord_ticket(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("Not implemented yet.", ephemeral=True)

    @discord.ui.button(label="Create In-game Support Ticket", style=discord.ButtonStyle.secondary)
    async def ingame_ticket(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("Not implemented yet.", ephemeral=True)


# ========================
# MESSAGE PANEL SYSTEM
# ========================

class MessageModal(Modal):
    def __init__(self, channel_id: int):
        super().__init__(title="Send a Message")
        self.channel_id = channel_id

        self.message_input = TextInput(
            label="Message",
            style=discord.TextStyle.paragraph,
            max_length=2000
        )

        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            channel = interaction.client.get_channel(self.channel_id)

            if channel is None:
                return await interaction.response.send_message("Channel not found.", ephemeral=True)

            await channel.send(self.message_input.value)

            await interaction.response.send_message("Message sent.", ephemeral=True)

            await debug_log(f"Message sent by {interaction.user} to {channel.id}")

        except Exception as e:
            await log_error("MessageModal failed", e)


class ChannelSelect(discord.ui.Select):
    def __init__(self, channels, page):
        options = [
            discord.SelectOption(label=ch.name, value=str(ch.id))
            for ch in channels
        ]

        super().__init__(
            placeholder=f"Select channel (Page {page})",
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(MessageModal(int(self.values[0])))


class MessagePanel(View):
    def __init__(self, page=1):
        super().__init__(timeout=None)

        all_channels = [c for c in bot.get_all_channels() if isinstance(c, discord.TextChannel)]

        per_page = 25
        start = (page - 1) * per_page
        end = start + per_page

        self.add_item(ChannelSelect(all_channels[start:end], page))


# ========================
# QOTD SYSTEM
# ========================

class QOTDModal(Modal):
    def __init__(self):
        super().__init__(title="Create QOTD")

        self.day = TextInput(label="Day number")
        self.question = TextInput(label="Question", style=discord.TextStyle.paragraph)

        self.add_item(self.day)
        self.add_item(self.question)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            channel = bot.get_channel(QOTD_CHANNEL_ID)

            content = f"<@&{QOTD_ROLE_ID}>\n**QOTD #{self.day.value}**\n{self.question.value}"

            msg = await channel.send(content)

            await msg.create_thread(name=self.question.value[:100])

            await interaction.response.send_message("QOTD posted.", ephemeral=True)

            await debug_log(f"QOTD created by {interaction.user}")

        except Exception as e:
            await log_error("QOTD failed", e)


class QOTDButton(Button):
    def __init__(self):
        super().__init__(label="Create QOTD", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(QOTDModal())
        # ========================
# PANELS
# ========================

async def send_panel():
    try:
        channel = bot.get_channel(PANEL_CHANNEL_ID) or await bot.fetch_channel(PANEL_CHANNEL_ID)

        embed = discord.Embed(
            title="🏠 RoomMates VC Ban Appeals",
            description="Press the button below to appeal.",
            color=discord.Color.green()
        )

        await channel.send(embed=embed, view=AppealPanel())
        await debug_log("Appeal panel sent")

    except Exception as e:
        await log_error("send_panel failed", e)


async def send_support_panel():
    try:
        channel = bot.get_channel(SUPPORT_CHANNEL_ID) or await bot.fetch_channel(SUPPORT_CHANNEL_ID)

        embed = discord.Embed(
            title="Support",
            description="Click below to create a support ticket.",
            color=discord.Color.purple()
        )

        await channel.send(embed=embed, view=SupportView())
        await debug_log("Support panel sent")

    except Exception as e:
        await log_error("send_support_panel failed", e)


async def send_message_panel():
    try:
        channel = bot.get_channel(MESSAGE_PANEL_CHANNEL_ID) or await bot.fetch_channel(MESSAGE_PANEL_CHANNEL_ID)

        embed = discord.Embed(
            title="📨 Message Sender Panel",
            description="Select a channel to send a message.",
            color=discord.Color.blue()
        )

        view = MessagePanel()
        view.add_item(QOTDButton())

        await channel.send(embed=embed, view=view)

        await debug_log("Message panel sent")

    except Exception as e:
        await log_error("send_message_panel failed", e)


# ========================
# SLASH COMMANDS
# ========================

@bot.tree.command(name="invites")
async def invites(interaction: discord.Interaction, user: discord.Member = None):
    try:
        user = user or interaction.user
        regular, left = get_invites(str(user.id))

        embed = discord.Embed(
            title=user.display_name,
            description=f"Invites: **{regular}**\nLeft: **{left}**",
            color=discord.Color.white()
        )

        await interaction.response.send_message(embed=embed)

    except Exception as e:
        await log_error("invites command failed", e)


@bot.tree.command(name="giveaway")
@app_commands.checks.has_permissions(manage_guild=True)
async def giveaway(interaction: discord.Interaction, title: str, time: str, winners: int):
    try:
        delta = parse_time_string(time)
        end_time = datetime.now(UTC) + delta
        unix = int(end_time.timestamp())

        embed = discord.Embed(title=title, color=discord.Color.white())
        embed.add_field(name="Ends", value=f"<t:{unix}:R>")
        embed.add_field(name="Entries", value="0")

        await interaction.response.defer()

        msg = await interaction.channel.send(embed=embed)

        GIVEAWAYS[msg.id] = {
            "entries": set(),
            "end_time": end_time,
            "winner_count": winners,
            "title": title,
            "host_id": interaction.user.id
        }

        await msg.edit(view=GiveawayView(msg.id))

        bot.loop.create_task(end_giveaway(msg.id, interaction.channel.id))

        await interaction.followup.send("Giveaway created.", ephemeral=True)

        await debug_log(f"Giveaway created: {msg.id}")

    except Exception as e:
        await log_error("giveaway command failed", e)


# ========================
# EVENTS
# ========================

@bot.event
async def on_ready():
    print(f"[DEBUG] Logged in as {bot.user}")
    await debug_log(f"Bot online: {bot.user}")

    for guild in bot.guilds:
        try:
            invites = await guild.invites()
            invite_cache[guild.id] = {i.code: i.uses for i in invites}
        except:
            invite_cache[guild.id] = {}

    bot.add_view(AppealPanel())
    bot.add_view(SupportView())
    bot.add_view(MessagePanel())

    check_bans.start()
    sync_roles_task.start()
    auto_lock_existing_threads.start()
    auto_close_old_threads.start()

    bot.loop.create_task(react_to_old_messages())

    await send_panel()
    await send_support_panel()
    await send_message_panel()

    try:
        await bot.tree.sync()
        await debug_log("Commands synced")
    except Exception as e:
        await log_error("Command sync failed", e)


@bot.event
async def on_member_join(member):
    try:
        await debug_log(f"{member} joined")

        guild = member.guild

        invites = await guild.invites()

        used = None
        for invite in invites:
            old = invite_cache.get(guild.id, {}).get(invite.code, 0)
            if invite.uses > old:
                used = invite
                break

        invite_cache[guild.id] = {i.code: i.uses for i in invites}

        if used:
            add_invite(str(used.inviter.id))

            await debug_log(f"{member} invited by {used.inviter}")

    except Exception as e:
        await log_error("on_member_join failed", e)


@bot.event
async def on_member_remove(member):
    try:
        await debug_log(f"{member} left")

        # ⚠️ still basic (no real tracking yet)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM invites ORDER BY regular DESC LIMIT 1")
        row = cursor.fetchone()

        if row:
            remove_invite(row[0])

    except Exception as e:
        await log_error("on_member_remove failed", e)


# ========================
# START
# ========================

print("[DEBUG] Starting bot...")
bot.run(TOKEN)
