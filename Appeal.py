import discord
from discord.ext import commands
import os
import asyncpg
from datetime import datetime

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="?", intents=intents)

ALLOWED_USER_ID = 1429110753683832985

db = None


# ================= DATABASE =================

async def init_db():
    global db
    db = await asyncpg.create_pool(os.getenv("DATABASE_URL"))

    async with db.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS config (
            guild_id BIGINT PRIMARY KEY,
            staff_role_id BIGINT,
            ticket_counter INT DEFAULT 0
        )
        """)


async def get_config(guild_id):
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM config WHERE guild_id=$1", guild_id
        )
        return row


async def set_staff_role(guild_id, role_id):
    async with db.acquire() as conn:
        await conn.execute("""
        INSERT INTO config (guild_id, staff_role_id, ticket_counter)
        VALUES ($1, $2, 0)
        ON CONFLICT (guild_id)
        DO UPDATE SET staff_role_id=$2
        """, guild_id, role_id)


async def get_next_ticket(guild_id):
    async with db.acquire() as conn:
        row = await conn.fetchrow("""
        UPDATE config
        SET ticket_counter = ticket_counter + 1
        WHERE guild_id=$1
        RETURNING ticket_counter
        """, guild_id)
        return row["ticket_counter"]


# ================= CLOSE BUTTON =================

class CloseView(discord.ui.View):
    def __init__(self, ticket_number):
        super().__init__(timeout=None)
        self.ticket_number = ticket_number

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, emoji="🔒")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):

        thread = interaction.channel

        # rename thread
        await thread.edit(name=f"close-{self.ticket_number}")

        # lock thread
        await thread.edit(locked=True, archived=False)

        # restrict visibility (remove everyone except staff + opener)
        config = await get_config(interaction.guild.id)
        staff_role = interaction.guild.get_role(config["staff_role_id"])

        for member in thread.members:
            if member != interaction.user and staff_role not in member.roles:
                try:
                    await thread.remove_user(member)
                except:
                    pass

        await interaction.response.send_message(
            "🔒 Ticket has been closed and made private.",
            ephemeral=True
        )


# ================= THREAD CREATION =================

async def create_ticket(interaction, title, fields):
    config = await get_config(interaction.guild.id)

    if not config or not config["staff_role_id"]:
        await interaction.response.send_message(
            "❌ Staff role not set. Use ?heh <role_id>",
            ephemeral=True
        )
        return

    staff_role = interaction.guild.get_role(config["staff_role_id"])
    ticket_number = await get_next_ticket(interaction.guild.id)

    thread = await interaction.channel.create_thread(
        name=f"ticket-{ticket_number}",
        type=discord.ChannelType.private_thread
    )

    await thread.add_user(interaction.user)

    # ping
    await thread.send(f"{staff_role.mention} {interaction.user.mention}")

    # embed
    embed = discord.Embed(
        title=title,
        color=discord.Color.blue(),
        timestamp=datetime.utcnow()
    )

    embed.add_field(name="Ticket Number", value=f"**#{ticket_number}**", inline=False)
    embed.add_field(name="User ID", value=str(interaction.user.id), inline=False)
    embed.add_field(name="Opened By", value=str(interaction.user), inline=False)
    embed.add_field(name="Opened At", value=f"<t:{int(datetime.utcnow().timestamp())}:F>", inline=False)

    for name, value in fields:
        embed.add_field(
            name=name,
            value=f"```{value}```",
            inline=False
        )

    embed.set_footer(text="Dreamy VR • Support System")

    await thread.send(embed=embed, view=CloseView(ticket_number))

    await interaction.response.send_message("✅ Ticket created!", ephemeral=True)


# ================= MODALS =================

class GeneralHelpModal(discord.ui.Modal, title="General Help"):
    issue = discord.ui.TextInput(label="What do you need help with?", style=discord.TextStyle.paragraph)
    details = discord.ui.TextInput(label="Additional Details", required=False, style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction):
        await create_ticket(
            interaction,
            "💬 General Help Ticket",
            [
                ("User", str(interaction.user)),
                ("Issue", self.issue.value),
                ("Details", self.details.value or "None")
            ]
        )


class InGameModal(discord.ui.Modal, title="Report In-Game Member"):
    username = discord.ui.TextInput(label="Player Username")
    issue = discord.ui.TextInput(label="What happened?", style=discord.TextStyle.paragraph)
    proof = discord.ui.TextInput(label="Proof")

    async def on_submit(self, interaction):
        await create_ticket(
            interaction,
            "🎮 In-Game Report",
            [
                ("Reporter", str(interaction.user)),
                ("Player", self.username.value),
                ("Issue", self.issue.value),
                ("Proof", self.proof.value)
            ]
        )


class CommunityModal(discord.ui.Modal, title="Report Community Member"):
    user = discord.ui.TextInput(label="Discord User")
    issue = discord.ui.TextInput(label="Issue", style=discord.TextStyle.paragraph)
    proof = discord.ui.TextInput(label="Proof")
    location = discord.ui.TextInput(label="Did this happen in Discord?")

    async def on_submit(self, interaction):
        await create_ticket(
            interaction,
            "🚨 Community Report",
            [
                ("Reporter", str(interaction.user)),
                ("Reported User", self.user.value),
                ("Issue", self.issue.value),
                ("Proof", self.proof.value),
                ("Location", self.location.value)
            ]
        )


# ================= BUTTON VIEW =================

class SupportView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="General Help", style=discord.ButtonStyle.primary, emoji="💬", custom_id="help")
    async def help_btn(self, interaction, button):
        await interaction.response.send_modal(GeneralHelpModal())

    @discord.ui.button(label="Report In-Game Member", style=discord.ButtonStyle.success, emoji="🎮", custom_id="ingame")
    async def ingame_btn(self, interaction, button):
        await interaction.response.send_modal(InGameModal())

    @discord.ui.button(label="Report Community Member", style=discord.ButtonStyle.danger, emoji="🚨", custom_id="community")
    async def community_btn(self, interaction, button):
        await interaction.response.send_modal(CommunityModal())


# ================= COMMAND =================

@bot.command()
async def heh(ctx, staff_role_id: int):
    if ctx.author.id != ALLOWED_USER_ID:
        return

    await set_staff_role(ctx.guild.id, staff_role_id)

    header = discord.Embed(
        title="Dreamy VR Support System",
        description="Please use this system to get help safely and efficiently. Staff will assist you as soon as possible.",
        color=discord.Color.blue()
    )

    support = discord.Embed(color=discord.Color.blue())

    support.add_field(name="1 • Open a Ticket", value="> Use buttons below", inline=True)
    support.add_field(name="2 • Explain Issue", value="> Be detailed", inline=True)
    support.add_field(name="3 • Stay Respectful", value="> Respect staff", inline=True)
    support.add_field(name="4 • No Spam", value="> Don't spam tickets", inline=True)
    support.add_field(name="5 • Follow Staff", value="> Follow instructions", inline=True)
    support.add_field(name="6 • No Troll Tickets", value="> No fake reports", inline=True)

    support.set_image(
        url="https://cdn.discordapp.com/attachments/1443984687436398698/1495500126582603838/image.png"
    )

    support.set_footer(text="Dreamy VR • Support System")

    await ctx.send(embed=header)
    await ctx.send(embed=support, view=SupportView())


# ================= READY =================

@bot.event
async def on_ready():
    await init_db()
    bot.add_view(SupportView())
    print(f"Logged in as {bot.user}")


# ================= RUN =================

bot.run(os.getenv("TOKEN"))
