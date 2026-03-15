import discord
from discord.ext import commands, tasks
from discord.ui import View, Button, Modal, TextInput
from discord import app_commands
import sqlite3
import os
import datetime

TOKEN = os.getenv("TOKEN")

# SERVERS
MAIN_GUILD_ID = 1476717006764900372
APPEAL_GUILD_ID = 1482442062862356573

# BAN ROLES
BANNED_ROLE = 1482444637795782919
NOT_BANNED_ROLE = 1482444680313442345

# APPEAL SYSTEM
PANEL_CHANNEL = 1482443249594400993
APPEAL_REVIEW_CHANNEL = 1482443249594400996
ACCEPT_CHANNEL = 1482442063592161594
ACCEPT_ROLE = 1482444757178388673

# STREAK SYSTEM
STREAK_CHANNEL = 1476717008010870805

STREAK_ROLES = {
1:1482749886699929660,
2:1482749591886762085,
7:1482749640523911448,
14:1482749707917987889,
30:1482749832526303425,
50:1482749832526303425
}

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

# DATABASE
db = sqlite3.connect("database.db")
cursor = db.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS streaks(
user_id INTEGER PRIMARY KEY,
messages INTEGER,
streak INTEGER,
last_time TEXT
)
""")

db.commit()

# ---------------- BAN DM ---------------- #

@bot.event
async def on_member_ban(guild,user):

    if guild.id != MAIN_GUILD_ID:
        return

    try:
        await user.send(
        "You have been banned in the **RoomMates discord server**.\n"
        "Appeal in the Appeal Server:\n"
        "https://discord.gg/vHYxFAZadS"
        )
    except:
        pass

# ---------------- BAN CHECK ---------------- #

async def update_roles(member):

    main = bot.get_guild(MAIN_GUILD_ID)
    appeal = bot.get_guild(APPEAL_GUILD_ID)

    banned_role = appeal.get_role(BANNED_ROLE)
    not_banned_role = appeal.get_role(NOT_BANNED_ROLE)

    try:
        await main.fetch_ban(member)
        banned = True
    except:
        banned = False

    if banned:
        if banned_role not in member.roles:
            await member.add_roles(banned_role)
    else:
        if not_banned_role not in member.roles:
            await member.add_roles(not_banned_role)

@tasks.loop(minutes=10)
async def check_bans():

    guild = bot.get_guild(APPEAL_GUILD_ID)

    for member in guild.members:
        await update_roles(member)

# ---------------- MESSAGE STREAK ---------------- #

@bot.event
async def on_message(message):

    if message.author.bot:
        return

    cursor.execute("SELECT * FROM streaks WHERE user_id=?",(message.author.id,))
    data = cursor.fetchone()

    now = datetime.datetime.utcnow()

    if not data:
        cursor.execute("INSERT INTO streaks VALUES(?,?,?,?)",
        (message.author.id,0,0,None))
        db.commit()
        data = (message.author.id,0,0,None)

    messages = data[1] + 1
    streak = data[2]
    last = data[3]

    if messages >= 2:

        if last:
            last_time = datetime.datetime.fromisoformat(last)

            if (now-last_time).total_seconds() > 86400:
                streak = 0

        messages = 0
        streak += 1

        guild = message.guild
        member = guild.get_member(message.author.id)
        channel = guild.get_channel(STREAK_CHANNEL)

        await channel.send(
        f"{member.mention}, you've accumulated a chat streak!\n"
        f"Come back tomorrow to carry on your streak.\n"
        f"Streak Count: {streak}"
        )

        for day,role_id in STREAK_ROLES.items():

            role = guild.get_role(role_id)

            if streak >= day:
                if role not in member.roles:
                    await member.add_roles(role)

    cursor.execute(
    "UPDATE streaks SET messages=?,streak=?,last_time=? WHERE user_id=?",
    (messages,streak,now.isoformat(),message.author.id)
    )

    db.commit()

    await bot.process_commands(message)

# ---------------- AUTO RESET CHECKER ---------------- #

@tasks.loop(hours=1)
async def streak_reset_checker():

    guild = bot.get_guild(MAIN_GUILD_ID)

    cursor.execute("SELECT * FROM streaks")

    for row in cursor.fetchall():

        user_id = row[0]
        streak = row[2]
        last = row[3]

        if not last:
            continue

        last_time = datetime.datetime.fromisoformat(last)

        if (datetime.datetime.utcnow()-last_time).total_seconds() > 86400:

            member = guild.get_member(user_id)

            if member:

                for role_id in STREAK_ROLES.values():

                    role = guild.get_role(role_id)

                    if role in member.roles:
                        await member.remove_roles(role)

                channel = guild.get_channel(STREAK_CHANNEL)

                await channel.send(
                f"{member.mention}, you've lost your chat streak."
                )

            cursor.execute(
            "UPDATE streaks SET streak=0 WHERE user_id=?",
            (user_id,)
            )

    db.commit()

# ---------------- CHECK STREAK ---------------- #

@bot.tree.command(name="checkstreak")
async def checkstreak(interaction:discord.Interaction):

    cursor.execute("SELECT streak FROM streaks WHERE user_id=?",
    (interaction.user.id,))
    data = cursor.fetchone()

    streak = 0
    if data:
        streak = data[0]

    embed = discord.Embed(
    title=f"{interaction.user.name}'s Chat Streak",
    description=f"Current Streak: **{streak} Days**"
    )

    embed.set_image(
    url="attachment://streak.png"
    )

    file = discord.File("streak.png")

    await interaction.response.send_message(
    embed=embed,
    file=file
    )

# ---------------- APPEAL MODAL ---------------- #

class AppealModal(Modal):

    def __init__(self):
        super().__init__(title="RoomMates Ban Appeal")

        self.username = TextInput(label="What's your username?")
        self.justified = TextInput(
        label="Do you think your ban was justified?",
        style=discord.TextStyle.paragraph)

        self.reason = TextInput(
        label="Why should you be unbanned?",
        style=discord.TextStyle.paragraph)

        self.add_item(self.username)
        self.add_item(self.justified)
        self.add_item(self.reason)

    async def on_submit(self,interaction):

        review = bot.get_channel(APPEAL_REVIEW_CHANNEL)
        main = bot.get_guild(MAIN_GUILD_ID)

        try:
            ban = await main.fetch_ban(interaction.user)
            reason = ban.reason
        except:
            reason = "Unknown"

        embed = discord.Embed(title="New Ban Appeal")

        embed.add_field(name="User",value=str(interaction.user))
        embed.add_field(name="Username",value=self.username.value,inline=False)
        embed.add_field(name="Ban Reason",value=reason,inline=False)
        embed.add_field(name="Justified",value=self.justified.value,inline=False)
        embed.add_field(name="Why Unban",value=self.reason.value,inline=False)

        await review.send(
        embed=embed,
        view=ReviewButtons(interaction.user.id)
        )

        await interaction.response.send_message(
        "Appeal submitted.",
        ephemeral=True
        )

# ---------------- STAFF REVIEW ---------------- #

class ReviewButtons(View):

    def __init__(self,user_id):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="Accept",style=discord.ButtonStyle.green)
    async def accept(self,interaction,button):

        main = bot.get_guild(MAIN_GUILD_ID)
        appeal = bot.get_guild(APPEAL_GUILD_ID)

        user = await bot.fetch_user(self.user_id)

        try:
            await main.unban(user)
        except:
            pass

        member = appeal.get_member(self.user_id)

        if member:
            role = appeal.get_role(ACCEPT_ROLE)
            await member.add_roles(role)

        channel = bot.get_channel(ACCEPT_CHANNEL)

        await channel.send(
        f"{user.mention} your appeal has been accepted."
        )

        embed = interaction.message.embeds[0]

        embed.add_field(
        name="Result",
        value=f"Accepted by {interaction.user.mention}"
        )

        await interaction.message.edit(
        embed=embed,
        view=None
        )

        await interaction.response.send_message(
        "Appeal accepted.",
        ephemeral=True
        )

    @discord.ui.button(label="Deny",style=discord.ButtonStyle.red)
    async def deny(self,interaction,button):

        embed = interaction.message.embeds[0]

        embed.add_field(
        name="Result",
        value=f"Denied by {interaction.user.mention}"
        )

        await interaction.message.edit(
        embed=embed,
        view=None
        )

        await interaction.response.send_message(
        "Appeal denied.",
        ephemeral=True
        )

# ---------------- APPEAL PANEL ---------------- #

class AppealPanel(View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Appeal Here",emoji="🔨",style=discord.ButtonStyle.green)
    async def appeal(self,interaction,button):

        role = interaction.guild.get_role(BANNED_ROLE)

        if role not in interaction.user.roles:

            await interaction.response.send_message(
            "You cannot appeal because you are not banned.",
            ephemeral=True
            )

            return

        await interaction.response.send_modal(AppealModal())

# ---------------- PANEL AUTO SEND ---------------- #

async def send_panel():

    channel = bot.get_channel(PANEL_CHANNEL)

    async for msg in channel.history(limit=20):

        if msg.author == bot.user:
            return

    embed = discord.Embed(
    title="RoomMates VC Ban Appeals",
    description=
    "Click **🔨 Appeal Here** to submit an appeal.\n\n"
    "If accepted you will be notified."
    )

    await channel.send(
    embed=embed,
    view=AppealPanel()
    )

# ---------------- READY ---------------- #

@bot.event
async def on_ready():

    print("Bot Ready")

    check_bans.start()
    streak_reset_checker.start()

    await send_panel()

    await bot.tree.sync()

bot.run(TOKEN)
