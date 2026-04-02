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

# ──────────────────────────────────────────────
#  CONFIGURATION
# ──────────────────────────────────────────────
TOKEN        = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

DEBUG_CHANNEL_ID = 1487755467949211709

MAIN_GUILD_ID        = 1476717006764900372
APPEAL_GUILD_ID      = 1482442062862356573
BANNED_ROLE_ID       = 1482444637795782919
NOT_BANNED_ROLE_ID   = 1482444680313442345
PANEL_CHANNEL_ID     = 1482443249594400993
APPEAL_REVIEW_CHANNEL= 1482443249594400996
ACCEPTED_CHANNEL     = 1482442063592161594
ACCEPTED_ROLE        = 1482444757178388673
SUPPORT_CHANNEL_ID   = 1476717007717142735
INVITE_CHANNEL       = 1476717008010870813
MAIN_SYNC_ROLE       = 1485407866453102732
APPEAL_SYNC_ROLE     = 1482444572687859773
TARGET_THREAD_CHANNEL= 1486456524187631869
MESSAGE_PANEL_CHANNEL_ID = 1487531080335626380
QOTD_ROLE_ID         = 1480033021326524427
QOTD_CHANNEL_ID      = 1486456524187631869

THREAD_LIFETIME = 24 * 60 * 60  # seconds

REACTION_CHANNELS = [
    1482516620730433625,
    1478798153288384624,
    1482514244997091479,
]

# ──────────────────────────────────────────────
#  LOGGING
# ──────────────────────────────────────────────
def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log(tag: str, message: str):
    """Print a formatted log line and send it to the debug channel."""
    line = f"[{_ts()}] [{tag}] {message}"
    print(line)
    return line  # returned so we can forward to Discord

async def dlog(tag: str, message: str):
    """Log locally and forward to the Discord debug channel."""
    line = log(tag, message)
    try:
        channel = bot.get_channel(DEBUG_CHANNEL_ID)
        if channel:
            await channel.send(f"```\n{line}\n```")
    except Exception:
        pass

# ──────────────────────────────────────────────
#  DATABASE
# ──────────────────────────────────────────────
log("DB", "Connecting to PostgreSQL...")
conn   = psycopg2.connect(DATABASE_URL, sslmode="require")
cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
log("DB", "Connected to PostgreSQL successfully.")

cursor.execute("""
    CREATE TABLE IF NOT EXISTS invites (
        user_id    TEXT PRIMARY KEY,
        regular    INTEGER DEFAULT 0,
        left_count INTEGER DEFAULT 0
    )
""")
conn.commit()

# ── Invite helpers ──────────────────────────────
def get_invites(user_id: str) -> tuple[int, int]:
    cursor.execute(
        "SELECT regular, left_count FROM invites WHERE user_id = %s",
        (user_id,)
    )
    row = cursor.fetchone()
    return (row["regular"], row["left_count"]) if row else (0, 0)

def set_invites(user_id: str, regular: int, left_count: int):
    cursor.execute("""
        INSERT INTO invites (user_id, regular, left_count)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE
            SET regular    = EXCLUDED.regular,
                left_count = EXCLUDED.left_count
    """, (user_id, regular, left_count))
    conn.commit()

def add_invite(user_id: str):
    regular, left_count = get_invites(user_id)
    set_invites(user_id, regular + 1, left_count)

def remove_invite(inviter_id: str):
    """Decrement the real inviter's regular count and increment their left count."""
    regular, left_count = get_invites(inviter_id)
    regular    = max(0, regular - 1)
    left_count = left_count + 1
    set_invites(inviter_id, regular, left_count)

# ──────────────────────────────────────────────
#  BOT SETUP
# ──────────────────────────────────────────────
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# invite_cache[guild_id][invite_code] = (uses, inviter_id)
invite_cache: dict[int, dict[str, tuple[int, int]]] = {}
# member_invite_map[guild_id][member_id] = inviter_id
member_invite_map: dict[int, dict[int, int]] = {}

GIVEAWAYS: dict[int, dict] = {}

# ──────────────────────────────────────────────
#  TIME PARSING
# ──────────────────────────────────────────────
def parse_time_string(time_str: str) -> timedelta:
    pattern = r"(\d+)\s*([dhm])"
    matches = re.findall(pattern, time_str.lower())
    if not matches:
        raise ValueError("Invalid time format. Use e.g. `1d 2h 30m`.")
    total = 0
    for amount, unit in matches:
        amount = int(amount)
        if unit == "d":
            total += amount * 86400
        elif unit == "h":
            total += amount * 3600
        elif unit == "m":
            total += amount * 60
    return timedelta(seconds=total)

# ──────────────────────────────────────────────
#  GIVEAWAY
# ──────────────────────────────────────────────
def _build_giveaway_embed(giveaway: dict, ended: bool = False) -> discord.Embed:
    embed = discord.Embed(title=giveaway["title"], color=discord.Color.from_rgb(255, 255, 255))
    unix  = int(giveaway["end_time"].timestamp())
    embed.add_field(name="Ends" if not ended else "Ended", value=f"<t:{unix}:R>", inline=False)
    embed.add_field(name="Hosted by", value=f"<@{giveaway['host_id']}>",          inline=False)
    embed.add_field(name="Entries",   value=f"**{len(giveaway['entries'])}**",     inline=False)
    return embed

class GiveawayView(View):
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success,
                       emoji="🎉", custom_id="giveaway_join")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        giveaway = GIVEAWAYS.get(self.message_id)
        if not giveaway:
            await interaction.response.send_message("This giveaway has ended.", ephemeral=True)
            return
        if interaction.user.id in giveaway["entries"]:
            await interaction.response.send_message("You already joined.", ephemeral=True)
            return

        giveaway["entries"].add(interaction.user.id)
        await dlog("GIVEAWAY", f"{interaction.user} ({interaction.user.id}) joined giveaway '{giveaway['title']}'")

        try:
            message = await interaction.channel.fetch_message(self.message_id)
            await message.edit(embed=_build_giveaway_embed(giveaway), view=self)
        except Exception as e:
            log("GIVEAWAY", f"Could not update embed: {e}")

        await interaction.response.send_message("You joined the giveaway!", ephemeral=True)

async def end_giveaway(message_id: int, channel_id: int):
    await bot.wait_until_ready()
    giveaway = GIVEAWAYS.get(message_id)
    if not giveaway:
        return

    remaining = (giveaway["end_time"] - datetime.now(UTC)).total_seconds()
    if remaining > 0:
        await asyncio.sleep(remaining)

    giveaway = GIVEAWAYS.pop(message_id, None)
    if not giveaway:
        return

    channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    if not channel:
        log("GIVEAWAY", f"Channel {channel_id} not found – cannot end giveaway.")
        return

    try:
        message = await channel.fetch_message(message_id)
    except Exception as e:
        log("GIVEAWAY", f"Could not fetch giveaway message: {e}")
        return

    entries = list(giveaway["entries"])
    n_winners = min(giveaway["winner_count"], len(entries))
    if entries:
        winners     = random.sample(entries, n_winners)
        winners_txt = ", ".join(f"<@{uid}>" for uid in winners)
    else:
        winners_txt = "No valid entries."

    embed = _build_giveaway_embed(giveaway, ended=True)
    embed.add_field(name="Winners", value=winners_txt, inline=False)
    await message.edit(embed=embed, view=None)

    await dlog("GIVEAWAY", f"Giveaway '{giveaway['title']}' ended. Winners: {winners_txt}")

# ──────────────────────────────────────────────
#  BAN / ROLE SYNC
# ──────────────────────────────────────────────
async def update_roles(member: discord.Member):
    main_guild   = bot.get_guild(MAIN_GUILD_ID)
    appeal_guild = bot.get_guild(APPEAL_GUILD_ID)
    if not main_guild or not appeal_guild:
        return

    banned_role     = appeal_guild.get_role(BANNED_ROLE_ID)
    not_banned_role = appeal_guild.get_role(NOT_BANNED_ROLE_ID)

    try:
        await main_guild.fetch_ban(member)
        banned = True
    except discord.NotFound:
        banned = False
    except Exception as e:
        log("BAN-SYNC", f"Error checking ban for {member}: {e}")
        return

    if banned:
        if banned_role     and banned_role     not in member.roles: await member.add_roles(banned_role)
        if not_banned_role and not_banned_role in  member.roles:    await member.remove_roles(not_banned_role)
    else:
        if not_banned_role and not_banned_role not in member.roles: await member.add_roles(not_banned_role)
        if banned_role     and banned_role     in  member.roles:    await member.remove_roles(banned_role)

@tasks.loop(minutes=10)
async def check_bans():
    guild = bot.get_guild(APPEAL_GUILD_ID)
    if not guild:
        return
    log("TASK", "Running ban-role sync sweep...")
    for member in guild.members:
        await update_roles(member)

async def sync_member_roles(member: discord.Member):
    main_guild   = bot.get_guild(MAIN_GUILD_ID)
    appeal_guild = bot.get_guild(APPEAL_GUILD_ID)
    if not main_guild or not appeal_guild:
        return
    main_member   = main_guild.get_member(member.id)
    appeal_member = appeal_guild.get_member(member.id)
    if not main_member or not appeal_member:
        return
    main_role   = main_guild.get_role(MAIN_SYNC_ROLE)
    appeal_role = appeal_guild.get_role(APPEAL_SYNC_ROLE)
    if not main_role or not appeal_role:
        return

    if main_role in main_member.roles:
        if appeal_role not in appeal_member.roles:
            await appeal_member.add_roles(appeal_role)
    else:
        if appeal_role in appeal_member.roles:
            await appeal_member.remove_roles(appeal_role)

@tasks.loop(seconds=60)
async def sync_roles_task():
    appeal_guild = bot.get_guild(APPEAL_GUILD_ID)
    if not appeal_guild:
        return
    for member in appeal_guild.members:
        await sync_member_roles(member)

# ──────────────────────────────────────────────
#  THREAD MANAGEMENT
# ──────────────────────────────────────────────
@bot.event
async def on_thread_update(before: discord.Thread, after: discord.Thread):
    if after.archived and not after.locked:
        try:
            await after.edit(locked=True)
            await dlog("THREAD", f"Auto-locked archived thread: #{after.name} ({after.id})")
        except Exception as e:
            log("THREAD", f"Failed to lock thread {after.id}: {e}")

@tasks.loop(minutes=30)
async def auto_lock_existing_threads():
    await bot.wait_until_ready()
    for guild in bot.guilds:
        for channel in guild.text_channels:
            try:
                threads = await channel.threads()
            except Exception:
                continue
            for thread in threads:
                if thread.archived and not thread.locked:
                    try:
                        await thread.edit(locked=True)
                    except Exception:
                        pass

@tasks.loop(minutes=10)
async def auto_close_old_threads():
    await bot.wait_until_ready()
    channel = bot.get_channel(TARGET_THREAD_CHANNEL)
    if not channel:
        return
    try:
        threads = await channel.threads()
    except Exception as e:
        log("THREAD", f"Failed to fetch threads: {e}")
        return
    now = datetime.now(UTC)
    for thread in threads:
        if thread.locked:
            continue
        age = (now - thread.created_at).total_seconds()
        if age >= THREAD_LIFETIME:
            try:
                await thread.edit(archived=True, locked=True)
                await dlog("THREAD", f"Auto-closed old thread: #{thread.name} (age {int(age//3600)}h)")
            except Exception as e:
                log("THREAD", f"Failed to close thread {thread.id}: {e}")

# ──────────────────────────────────────────────
#  REACTIONS
# ──────────────────────────────────────────────
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.channel.id in REACTION_CHANNELS:
        try:
            await message.add_reaction("👍")
        except Exception:
            pass
    await bot.process_commands(message)

async def react_to_old_messages():
    await bot.wait_until_ready()
    log("STARTUP", "Back-filling 👍 reactions on old messages...")
    for channel_id in REACTION_CHANNELS:
        channel = bot.get_channel(channel_id)
        if not channel:
            continue
        async for message in channel.history(limit=None):
            try:
                if not any(str(r.emoji) == "👍" for r in message.reactions):
                    await message.add_reaction("👍")
            except Exception:
                pass

# ──────────────────────────────────────────────
#  APPEAL SYSTEM
# ──────────────────────────────────────────────
class AppealModal(Modal):
    def __init__(self):
        super().__init__(title="RoomMates Ban Appeal")
        self.username  = TextInput(label="What's your username?")
        self.justified = TextInput(label="Do you think your ban was justified?",
                                   style=discord.TextStyle.paragraph)
        self.reason    = TextInput(label="Why should you be unbanned?",
                                   style=discord.TextStyle.paragraph)
        self.add_item(self.username)
        self.add_item(self.justified)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        review_channel = bot.get_channel(APPEAL_REVIEW_CHANNEL)
        main_guild     = bot.get_guild(MAIN_GUILD_ID)

        try:
            ban        = await main_guild.fetch_ban(interaction.user)
            ban_reason = ban.reason or "No reason provided"
        except Exception:
            ban_reason = "Unknown"

        embed = discord.Embed(title="New Ban Appeal", color=discord.Color.orange())
        embed.add_field(name="User",               value=f"{interaction.user} ({interaction.user.id})", inline=False)
        embed.add_field(name="Username",           value=self.username.value,   inline=False)
        embed.add_field(name="Ban Reason",         value=ban_reason,            inline=False)
        embed.add_field(name="Was Ban Justified?", value=self.justified.value,  inline=False)
        embed.add_field(name="Why Unban?",         value=self.reason.value,     inline=False)

        view = StaffReviewView(interaction.user.id)
        await review_channel.send(embed=embed, view=view)
        await interaction.response.send_message("Your appeal has been submitted.", ephemeral=True)
        await dlog("APPEAL", f"Appeal submitted by {interaction.user} ({interaction.user.id})")

class StaffReviewView(View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        main_guild       = bot.get_guild(MAIN_GUILD_ID)
        appeal_guild     = bot.get_guild(APPEAL_GUILD_ID)
        accepted_channel = bot.get_channel(ACCEPTED_CHANNEL)
        user             = await bot.fetch_user(self.user_id)

        try:
            await main_guild.unban(user)
            await dlog("APPEAL", f"Appeal ACCEPTED for {user} ({user.id}) by {interaction.user}")
        except discord.NotFound:
            log("APPEAL", f"User {user.id} was not banned – unban skipped.")
        except Exception as e:
            log("APPEAL", f"Unban error for {user.id}: {e}")

        member = appeal_guild.get_member(self.user_id)
        if member:
            role = appeal_guild.get_role(ACCEPTED_ROLE)
            if role:
                await member.add_roles(role)

        if accepted_channel:
            await accepted_channel.send(f"{user.mention} your appeal has been accepted.")

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.green()
        embed.add_field(name="Result", value=f"Accepted by {interaction.user.mention}", inline=False)
        await interaction.message.edit(embed=embed, view=None)
        await interaction.response.send_message("Appeal accepted.", ephemeral=True)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = await bot.fetch_user(self.user_id)
        await dlog("APPEAL", f"Appeal DENIED for {user} ({user.id}) by {interaction.user}")

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()
        embed.add_field(name="Result", value=f"Denied by {interaction.user.mention}", inline=False)
        await interaction.message.edit(embed=embed, view=None)
        await interaction.response.send_message("Appeal denied.", ephemeral=True)

class AppealPanel(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="DISCORD APPEAL", style=discord.ButtonStyle.success,
                       emoji="🔨", custom_id="appeal_here")
    async def appeal(self, interaction: discord.Interaction, button: discord.ui.Button):
        banned_role = interaction.guild.get_role(BANNED_ROLE_ID)
        if banned_role not in interaction.user.roles:
            await interaction.response.send_message(
                "You cannot appeal because you are not banned.", ephemeral=True
            )
            return
        await interaction.response.send_modal(AppealModal())

    @discord.ui.button(label="GAME APPEAL", style=discord.ButtonStyle.primary,
                       emoji="🎮", custom_id="game_appeal")
    async def game_appeal(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Game appeal system coming soon.", ephemeral=True)

    @discord.ui.button(label="Ban Case", style=discord.ButtonStyle.secondary,
                       emoji="📄", custom_id="ban_case")
    async def case(self, interaction: discord.Interaction, button: discord.ui.Button):
        main_guild = bot.get_guild(MAIN_GUILD_ID)
        try:
            ban    = await main_guild.fetch_ban(interaction.user)
            reason = ban.reason or "No reason provided"
            embed  = discord.Embed(
                title       = "Your Ban Case",
                description = f"Reason: {reason}",
                color       = discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.NotFound:
            await interaction.response.send_message(
                "You are not banned in the main server.", ephemeral=True
            )

# ──────────────────────────────────────────────
#  SUPPORT PANEL
# ──────────────────────────────────────────────
class SupportView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Discord Support Ticket", style=discord.ButtonStyle.success,
                       emoji="📩", custom_id="support_discord")
    async def discord_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Support ticket feature not connected yet.", ephemeral=True)

    @discord.ui.button(label="Create In-game Support Ticket", style=discord.ButtonStyle.secondary,
                       emoji="📩", custom_id="support_ingame")
    async def ingame_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("In-game support not connected yet.", ephemeral=True)

# ──────────────────────────────────────────────
#  MESSAGE PANEL
# ──────────────────────────────────────────────
class MessageModal(Modal):
    def __init__(self, channel_id: int):
        super().__init__(title="Send a Message")
        self.channel_id   = channel_id
        self.message_input = TextInput(
            label      = "What do you want to send?",
            style      = discord.TextStyle.paragraph,
            required   = True,
            max_length = 2000,
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.client.get_channel(self.channel_id)
        if not channel:
            await interaction.response.send_message("Channel not found.", ephemeral=True)
            return
        await channel.send(self.message_input.value)
        await interaction.response.send_message(f"Message sent in {channel.mention}.", ephemeral=True)
        await dlog("MSG-PANEL", f"{interaction.user} sent a message in #{channel.name} ({channel.id})")

class ChannelSelect(discord.ui.Select):
    def __init__(self, channels: list, page: int):
        options = [
            discord.SelectOption(label=ch.name[:100], value=str(ch.id))
            for ch in channels
        ]
        super().__init__(
            placeholder = f"Select a channel... (Page {page})",
            min_values  = 1,
            max_values  = 1,
            options     = options,
            custom_id   = f"message_panel_select_page_{page}",
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(MessageModal(int(self.values[0])))

class NextPageButton(discord.ui.Button):
    def __init__(self, page: int):
        super().__init__(label="Next Page ➜", style=discord.ButtonStyle.primary,
                         custom_id=f"next_page_{page}")

    async def callback(self, interaction: discord.Interaction):
        new_page = int(self.custom_id.split("_")[-1]) + 1
        await interaction.response.edit_message(view=MessagePanel(new_page))

class PrevPageButton(discord.ui.Button):
    def __init__(self, page: int):
        super().__init__(label="⬅ Previous Page", style=discord.ButtonStyle.secondary,
                         custom_id=f"prev_page_{page}")

    async def callback(self, interaction: discord.Interaction):
        new_page = int(self.custom_id.split("_")[-1]) - 1
        await interaction.response.edit_message(view=MessagePanel(new_page))

class QOTDModal(Modal):
    def __init__(self):
        super().__init__(title="Create Question of the Day")
        self.day_input = TextInput(
            label      = "What day number is it?",
            style      = discord.TextStyle.short,
            required   = True,
            max_length = 10,
        )
        self.question_input = TextInput(
            label      = "What question would you like to ask?",
            style      = discord.TextStyle.paragraph,
            required   = True,
            max_length = 2000,
        )
        self.add_item(self.day_input)
        self.add_item(self.question_input)

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.client.get_channel(QOTD_CHANNEL_ID)
        if not channel:
            try:
                channel = await interaction.client.fetch_channel(QOTD_CHANNEL_ID)
            except Exception:
                await interaction.response.send_message("QOTD channel not found.", ephemeral=True)
                return

        day_str  = self.day_input.value.strip()
        question = self.question_input.value.strip()
        content  = (
            f"<@&{QOTD_ROLE_ID}>\n"
            f"**Question of the Day #{day_str}:**\n"
            f"{question}"
        )

        try:
            msg = await channel.send(content)
        except Exception:
            await interaction.response.send_message("Failed to send QOTD.", ephemeral=True)
            return

        thread_name = question[:97] + "..." if len(question) > 100 else question
        try:
            await msg.create_thread(name=thread_name)
        except Exception:
            pass

        await interaction.response.send_message("QOTD posted.", ephemeral=True)
        await dlog("QOTD", f"Day #{day_str} posted by {interaction.user}: {question[:80]}")

class QOTDButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Create QOTD", style=discord.ButtonStyle.success,
                         emoji="❓", custom_id="qotd_create_button")

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(QOTDModal())

class MessagePanel(View):
    def __init__(self, page: int = 1):
        super().__init__(timeout=None)
        self.page = page

        all_channels = [
            c for c in bot.get_all_channels()
            if isinstance(c, discord.TextChannel)
        ]
        per_page   = 25
        start      = (page - 1) * per_page
        end        = start + per_page
        page_chans = all_channels[start:end] or all_channels[:per_page]

        self.add_item(ChannelSelect(page_chans, page))
        if start > 0:
            self.add_item(PrevPageButton(page))
        if end < len(all_channels):
            self.add_item(NextPageButton(page))
        self.add_item(QOTDButton())

# ──────────────────────────────────────────────
#  PANEL SENDERS  (idempotent – skip if already sent)
# ──────────────────────────────────────────────
async def send_panel():
    channel = bot.get_channel(PANEL_CHANNEL_ID) or await bot.fetch_channel(PANEL_CHANNEL_ID)
    async for msg in channel.history(limit=20):
        if msg.author == bot.user:
            return
    embed = discord.Embed(
        title       = "🏠 RoomMates VC Ban Appeals",
        description = (
            "Welcome to the **RoomMates VC Ban Appeal System**.\n\n"
            "**How to appeal**\n"
            "Press **🔨 DISCORD APPEAL** and complete the form.\n\n"
            "**What happens next?**\n"
            "• Staff will review your appeal.\n"
            "• If accepted you will be notified.\n"
            "• If declined, after **7 days** you may appeal again.\n\n"
            "You can view your **ban reason** using the Ban Case button."
        ),
        color = discord.Color.green(),
    )
    await channel.send(embed=embed, view=AppealPanel())

async def send_support_panel():
    channel = bot.get_channel(SUPPORT_CHANNEL_ID) or await bot.fetch_channel(SUPPORT_CHANNEL_ID)
    async for msg in channel.history(limit=20):
        if msg.author == bot.user:
            return
    embed = discord.Embed(
        title       = "Support",
        description = (
            "**🎟️ Need Help?**\n\n"
            "If you're experiencing an issue, our support team is here to help.\n\n"
            "**Before opening a ticket, please remember:**\n"
            "• Staff will respond as soon as possible after your ticket is created\n"
            "• You can also use the **in-game support button**\n"
        ),
        color = discord.Color.purple(),
    )
    await channel.send(embed=embed, view=SupportView())

async def send_message_panel():
    channel = bot.get_channel(MESSAGE_PANEL_CHANNEL_ID) or await bot.fetch_channel(MESSAGE_PANEL_CHANNEL_ID)
    async for msg in channel.history(limit=50):
        if (msg.author == bot.user
                and msg.embeds
                and msg.embeds[0].title == "📨 Message Sender Panel"):
            return
    embed = discord.Embed(
        title       = "📨 Message Sender Panel",
        description = (
            "Select a channel from the dropdown below.\n"
            "You will be asked what message you want to send.\n"
            "The bot will send it in the selected channel.\n\n"
            "Use the **Create QOTD** button to post a Question of the Day."
        ),
        color = discord.Color.blue(),
    )
    await channel.send(embed=embed, view=MessagePanel())

# ──────────────────────────────────────────────
#  SLASH COMMANDS
# ──────────────────────────────────────────────
@bot.tree.command(name="slowmode", description="Set slowmode for a channel")
@app_commands.describe(channel="Channel to apply slowmode to",
                       time="Slowmode duration in seconds (0 to disable)")
@app_commands.checks.has_permissions(manage_channels=True)
async def slowmode(interaction: discord.Interaction, channel: discord.TextChannel, time: int):
    if time < 0:
        await interaction.response.send_message("Slowmode time cannot be negative.", ephemeral=True)
        return
    try:
        await channel.edit(slowmode_delay=time)
        msg = f"Slowmode disabled in {channel.mention}." if time == 0 \
              else f"Slowmode set to **{time} seconds** in {channel.mention}."
        await interaction.response.send_message(msg, ephemeral=True)
        await dlog("SLOWMODE", f"{interaction.user} set slowmode to {time}s in #{channel.name}")
    except Exception:
        await interaction.response.send_message("Failed to update slowmode.", ephemeral=True)

@bot.tree.command(name="invites", description="Check how many invites a user has")
@app_commands.describe(user="User to check")
async def invites_cmd(interaction: discord.Interaction, user: discord.Member = None):
    if user is None:
        user = interaction.user
    regular, left_count = get_invites(str(user.id))
    embed = discord.Embed(
        title       = user.display_name,
        description = f"**{regular}** regular invites.",
        color       = discord.Color.from_rgb(255, 255, 255),
    )
    embed.add_field(name="Regular", value=str(regular),    inline=True)
    embed.add_field(name="Left",    value=str(left_count), inline=True)
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

    embed = discord.Embed(title="🏆 Top Inviters", color=discord.Color.from_rgb(255, 255, 255))
    desc  = ""
    for i, row in enumerate(rows, 1):
        uid   = int(row["user_id"])
        user  = interaction.guild.get_member(uid) or await bot.fetch_user(uid)
        name  = user.mention if user else str(uid)
        desc += f"**#{i} — {name}**\nInvites: **{row['regular']}** | Left: **{row['left_count']}**\n\n"
    embed.description = desc
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
    set_invites(str(user.id), regular, left_count)
    await interaction.response.send_message(
        f"Added **{amount} invites** to {user.mention}. They now have **{regular} regular invites**.",
        ephemeral=True,
    )
    await dlog("INVITES", f"{interaction.user} added {amount} invites to {user} → total {regular}")

@bot.tree.command(name="giveaway", description="Create a giveaway")
@app_commands.describe(
    title       = "Title of the giveaway",
    time        = "Duration like '1h 30m', '2h', '45m', '1d 2h'",
    winnercount = "Number of winners",
)
@app_commands.checks.has_permissions(manage_guild=True)
async def giveaway_cmd(interaction: discord.Interaction, title: str, time: str, winnercount: int):
    try:
        delta = parse_time_string(time)
    except ValueError as e:
        await interaction.response.send_message(str(e), ephemeral=True)
        return
    if winnercount < 1:
        await interaction.response.send_message("Winner count must be at least 1.", ephemeral=True)
        return

    end_time = datetime.now(UTC) + delta
    unix     = int(end_time.timestamp())

    await interaction.response.defer()

    giveaway_data = {
        "entries"     : set(),
        "end_time"    : end_time,
        "winner_count": winnercount,
        "title"       : title,
        "host_id"     : interaction.user.id,
        "channel_id"  : interaction.channel.id,
    }

    embed   = _build_giveaway_embed(giveaway_data)
    message = await interaction.channel.send(embed=embed)

    GIVEAWAYS[message.id] = giveaway_data
    view = GiveawayView(message.id)
    await message.edit(view=view)
    bot.loop.create_task(end_giveaway(message.id, interaction.channel.id))

    await interaction.followup.send(
        f"Giveaway created for **{title}** ending <t:{unix}:R>.", ephemeral=True
    )
    await dlog("GIVEAWAY", f"{interaction.user} started giveaway '{title}' ending <t:{unix}:R> with {winnercount} winner(s)")

# ──────────────────────────────────────────────
#  EVENTS
# ──────────────────────────────────────────────
@bot.event
async def on_ready():
    await dlog("STARTUP", f"Bot online: {bot.user}")

    # Cache invites per guild
    for guild in bot.guilds:
        try:
            fetched = await guild.invites()
            invite_cache[guild.id]     = {i.code: (i.uses, i.inviter.id if i.inviter else None) for i in fetched}
            member_invite_map[guild.id] = {}
            await dlog("INVITE-CACHE", f"Cached {len(fetched)} invites for guild '{guild.name}'")
        except Exception as e:
            log("INVITE-CACHE", f"Could not cache invites for '{guild.name}': {e}")
            invite_cache[guild.id]      = {}
            member_invite_map[guild.id] = {}

    # Register persistent views
    bot.add_view(AppealPanel())
    bot.add_view(SupportView())
    bot.add_view(MessagePanel())

    # Start background tasks
    check_bans.start()
    sync_roles_task.start()
    auto_lock_existing_threads.start()
    auto_close_old_threads.start()
    bot.loop.create_task(react_to_old_messages())

    # Send/verify panels
    await send_panel()
    await send_support_panel()
    await send_message_panel()

    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        await dlog("STARTUP", f"Synced {len(synced)} slash command(s)")
    except Exception as e:
        log("STARTUP", f"Failed to sync commands: {e}")

@bot.event
async def on_member_join(member: discord.Member):
    if member.guild.id == APPEAL_GUILD_ID:
        await update_roles(member)
        await sync_member_roles(member)

    guild = member.guild

    # Re-fetch current invites and compare to cache
    try:
        current_invites = await guild.invites()
    except Exception as e:
        log("INVITE", f"Could not fetch invites on join in '{guild.name}': {e}")
        return

    old_cache = invite_cache.get(guild.id, {})
    used      = None

    for invite in current_invites:
        old_uses, _ = old_cache.get(invite.code, (0, None))
        if invite.uses > old_uses and invite.inviter:
            used = invite
            break

    # Update the cache with fresh data
    invite_cache[guild.id] = {
        i.code: (i.uses, i.inviter.id if i.inviter else None)
        for i in current_invites
    }

    if used:
        inviter_id = str(used.inviter.id)
        add_invite(inviter_id)
        regular, _ = get_invites(inviter_id)

        # Remember which invite brought this member (for accurate remove_invite later)
        member_invite_map.setdefault(guild.id, {})[member.id] = used.inviter.id

        channel = bot.get_channel(INVITE_CHANNEL)
        if channel:
            await channel.send(
                f"{member.mention} joined using {used.inviter.mention}'s invite! "
                f"They now have **{regular} invite(s)**."
            )
        await dlog("INVITE", f"{member} joined via {used.inviter}'s invite (code: {used.code}). Inviter total: {regular}")
    else:
        await dlog("INVITE", f"{member} joined '{guild.name}' but invite source could not be determined.")

@bot.event
async def on_member_remove(member: discord.Member):
    guild = member.guild

    # Look up who originally invited this member
    inviter_id = member_invite_map.get(guild.id, {}).pop(member.id, None)

    channel = bot.get_channel(INVITE_CHANNEL)

    if inviter_id:
        remove_invite(str(inviter_id))
        regular, left_count = get_invites(str(inviter_id))
        if channel:
            await channel.send(
                f"**{member.name}** left the server. "
                f"Removed **1 invite** from <@{inviter_id}>. "
                f"They now have **{regular} regular** and **{left_count} left** invites."
            )
        await dlog("INVITE", f"{member} left. Removed invite from <@{inviter_id}>. They now have {regular} regular, {left_count} left.")
    else:
        if channel:
            await channel.send(f"**{member.name}** left the server. No invite record found – no invites deducted.")
        await dlog("INVITE", f"{member} left '{guild.name}' but no invite record found. No invites deducted.")

# ──────────────────────────────────────────────
#  MISC COMMANDS
# ──────────────────────────────────────────────
@bot.command()
async def gamelink(ctx):
    await ctx.send("https://www.roblox.com/share?code=91a1d9f9e2d8234f9d477e1e75736b34&type=ExperienceDetails&stamp=1773867741632")

# ──────────────────────────────────────────────
#  ENTRY POINT
# ──────────────────────────────────────────────
log("STARTUP", "Starting bot...")
bot.run(TOKEN)

