import discord
from discord.ext import commands
import os

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="?", intents=intents)

ALLOWED_USER_ID = 1429110753683832985

# ================= MODALS =================

class GeneralHelpModal(discord.ui.Modal, title="General Help"):
    issue = discord.ui.TextInput(
        label="What do you need help with?",
        placeholder="Explain your issue...",
        style=discord.TextStyle.paragraph,
        required=True
    )

    details = discord.ui.TextInput(
        label="Additional Details",
        placeholder="Add more info if needed...",
        style=discord.TextStyle.paragraph,
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("✅ Help request submitted!", ephemeral=True)


class InGameReportModal(discord.ui.Modal, title="Report In-Game Member"):
    username = discord.ui.TextInput(
        label="Player Username",
        placeholder="Enter their in-game name...",
        required=True
    )

    issue = discord.ui.TextInput(
        label="What happened?",
        placeholder="Explain the situation...",
        style=discord.TextStyle.paragraph,
        required=True
    )

    proof = discord.ui.TextInput(
        label="Do you have proof?",
        placeholder="Yes / No + details",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("✅ In-game report submitted!", ephemeral=True)


class CommunityReportModal(discord.ui.Modal, title="Report Community Member"):
    discord_user = discord.ui.TextInput(
        label="Is this about a Discord user?",
        placeholder="Yes or No",
        required=True
    )

    issue = discord.ui.TextInput(
        label="What is the issue?",
        placeholder="Explain what happened...",
        style=discord.TextStyle.paragraph,
        required=True
    )

    proof = discord.ui.TextInput(
        label="Do you have proof?",
        placeholder="Yes or No",
        required=True
    )

    location = discord.ui.TextInput(
        label="Did this happen in the Discord?",
        placeholder="We only handle Discord issues",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("✅ Community report submitted!", ephemeral=True)


# ================= BUTTON VIEW =================

class SupportView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="General Help", style=discord.ButtonStyle.primary, emoji="💬")
    async def general_help(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(GeneralHelpModal())

    @discord.ui.button(label="Report In-Game Member", style=discord.ButtonStyle.success, emoji="🎮")
    async def ingame(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(InGameReportModal())

    @discord.ui.button(label="Report Community Member", style=discord.ButtonStyle.danger, emoji="🚨")
    async def community(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CommunityReportModal())


# ================= COMMAND =================

@bot.command()
async def heh(ctx):
    if ctx.author.id != ALLOWED_USER_ID:
        return

    header = discord.Embed(
        title="Dreamy VR Support System",
        description="Please use this system to get help safely and efficiently.",
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


# ================= RUN =================

token = os.getenv("TOKEN")
bot.run(token)
