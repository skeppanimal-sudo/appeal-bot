import discord
import os
from discord.ext import commands, tasks
from discord.ui import View, Modal, TextInput, Button

TOKEN = os.getenv("TOKEN")

MAIN_GUILD_ID = 1476717006764900372
APPEAL_GUILD_ID = 1482442062862356573

BANNED_ROLE_ID = 1482444637795782919
NOT_BANNED_ROLE_ID = 1482444680313442345

PANEL_CHANNEL_ID = 1482443249594400993
APPEAL_REVIEW_CHANNEL = 1482443249594400996

ACCEPTED_CHANNEL = 1482442063592161594
ACCEPTED_ROLE = 1482444757178388673

SUPPORT_CHANNEL_ID = 1476717007717142735
STAFF_PING = 1476717006794264601

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

ticket_counter = 0


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


@bot.event
async def on_member_join(member):
    if member.guild.id == APPEAL_GUILD_ID:
        await update_roles(member)


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

        async for message in channel.history(limit=None):
            try:
                if not any(str(r.emoji) == "👍" for r in message.reactions):
                    await message.add_reaction("👍")
            except:
                pass


# ---------------- APPEAL MODAL ---------------- #

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


# ---------------- STAFF REVIEW ---------------- #

class StaffReviewView(View):
    def __init__(self, user_id):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: Button):

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
    async def deny(self, interaction: discord.Interaction, button: Button):

        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()
        embed.add_field(name="Result", value=f"Denied by {interaction.user.mention}", inline=False)

        await interaction.message.edit(embed=embed, view=None)
        await interaction.response.send_message("Appeal denied.", ephemeral=True)


# ---------------- CLOSE SYSTEM ---------------- #

class CloseConfirm(View):
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, button: Button):
        await interaction.channel.edit(locked=True, archived=True)
        await interaction.response.send_message("Ticket closed.", ephemeral=True)

    @discord.ui.button(label="No", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message("Cancelled.", ephemeral=True)


class CloseButton(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, emoji="🔒")
    async def close(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message(
            "Are you sure you want to close this ticket?",
            view=CloseConfirm(),
            ephemeral=True
        )


# ---------------- SUPPORT MODAL ---------------- #

class DiscordSupportModal(Modal):
    def __init__(self):
        super().__init__(title="Discord Issue Support")

        self.q1 = TextInput(label="Is this about another discord user?")
        self.q2 = TextInput(label="Do you have proof?")
        self.q3 = TextInput(label="What happened?", style=discord.TextStyle.paragraph)
        self.q4 = TextInput(label="Did this happen in RoomMates server?")

        self.add_item(self.q1)
        self.add_item(self.q2)
        self.add_item(self.q3)
        self.add_item(self.q4)

    async def on_submit(self, interaction: discord.Interaction):
        global ticket_counter
        ticket_counter += 1

        await interaction.response.defer(ephemeral=True)

        embed = discord.Embed(
            title="Discord Issue Support",
            description="If you said 'no' DO NOT FILE A TICKET HERE",
            color=discord.Color.dark_purple()
        )

        embed.add_field(name="Is the issue about another Discord User?", value=self.q1.value, inline=False)
        embed.add_field(name="What is the issue you are experience?", value=self.q3.value, inline=False)
        embed.add_field(name="Do you have proof?", value=self.q2.value, inline=False)
        embed.add_field(name="Did this issue happen in the AC Discord?", value=self.q4.value, inline=False)

        channel = await bot.fetch_channel(SUPPORT_CHANNEL_ID)

        msg = await channel.send("Creating ticket...")

        thread = await msg.create_thread(
            name=f"ticket-{ticket_counter}",
            type=discord.ChannelType.private_thread
        )

        await thread.add_user(interaction.user)

        await thread.send(
            content=f"<@{STAFF_PING}> {interaction.user.mention}",
            embed=embed,
            view=CloseButton()
        )


# ---------------- SUPPORT PANEL ---------------- #

class SupportView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Create Discord Support Ticket", style=discord.ButtonStyle.success)
    async def ticket(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(DiscordSupportModal())


async def send_support_panel():
    channel = await bot.fetch_channel(SUPPORT_CHANNEL_ID)

    embed = discord.Embed(
        title="Support",
        description="Click below to create a ticket.",
        color=discord.Color.purple()
    )

    await channel.send(embed=embed, view=SupportView())


# ---------------- APPEAL PANEL SEND ---------------- #

class AppealPanel(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Appeal Here", style=discord.ButtonStyle.success)
    async def appeal(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AppealModal())


async def send_panel():
    channel = await bot.fetch_channel(PANEL_CHANNEL_ID)

    embed = discord.Embed(
        title="🏠 RoomMates VC Ban Appeals",
        description="Click below to appeal.",
        color=discord.Color.green()
    )

    await channel.send(embed=embed, view=AppealPanel())


# ---------------- READY ---------------- #

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

    bot.add_view(AppealPanel())
    bot.add_view(SupportView())
    bot.add_view(CloseButton())

    check_bans.start()
    bot.loop.create_task(react_to_old_messages())

    await send_panel()
    await send_support_panel()


bot.run(TOKEN)
