import discord
from discord.ext import commands
import os

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="?", intents=intents)

ALLOWED_USER_ID = 1429110753683832985
STAFF_ROLE_ID = None
ticket_counter = 0


# ================= THREAD CREATION =================

async def create_ticket(interaction, title, data):
    global ticket_counter
    ticket_counter += 1

    channel = interaction.channel
    staff_role = interaction.guild.get_role(STAFF_ROLE_ID)

    thread = await channel.create_thread(
        name=f"ticket-{ticket_counter}",
        type=discord.ChannelType.private_thread
    )

    # add user
    await thread.add_user(interaction.user)

    # ping staff + user
    await thread.send(f"{staff_role.mention} {interaction.user.mention}")

    # formatted embed (like your screenshot)
    embed = discord.Embed(title=title, color=discord.Color.blue())

    for name, value in data:
        embed.add_field(
            name=name,
            value=f"```{value}```",
            inline=False
        )

    embed.set_footer(text="Dreamy VR • Support System")

    await thread.send(embed=embed)


# ================= MODALS =================

class GeneralHelpModal(discord.ui.Modal, title="General Help"):
    issue = discord.ui.TextInput(
        label="What do you need help with?",
        placeholder="Explain your issue...",
        style=discord.TextStyle.paragraph
    )

    details = discord.ui.TextInput(
        label="Additional Details",
        placeholder="Add more info...",
        style=discord.TextStyle.paragraph,
        required=False
    )

    async def on_submit(self, interaction: discord.Interaction):
        await create_ticket(
            interaction,
            "💬 General Help Ticket",
            [
                ("User", str(interaction.user)),
                ("Issue", self.issue.value),
                ("Details", self.details.value or "None")
            ]
        )
        await interaction.response.send_message("✅ Ticket created!", ephemeral=True)


class InGameReportModal(discord.ui.Modal, title="Report In-Game Member"):
    username = discord.ui.TextInput(label="Player Username")
    issue = discord.ui.TextInput(label="What happened?", style=discord.TextStyle.paragraph)
    proof = discord.ui.TextInput(label="Proof", placeholder="Yes/No + details")

    async def on_submit(self, interaction: discord.Interaction):
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
        await interaction.response.send_message("✅ Report submitted!", ephemeral=True)


class CommunityReportModal(discord.ui.Modal, title="Report Community Member"):
    discord_user = discord.ui.TextInput(label="Discord User")
    issue = discord.ui.TextInput(label="Issue", style=discord.TextStyle.paragraph)
    proof = discord.ui.TextInput(label="Proof")
    location = discord.ui.TextInput(label="Did this happen in Discord?")

    async def on_submit(self, interaction: discord.Interaction):
        await create_ticket(
            interaction,
            "🚨 Community Report",
            [
                ("Reporter", str(interaction.user)),
                ("Reported User", self.discord_user.value),
                ("Issue", self.issue.value),
                ("Proof", self.proof.value),
                ("Location", self.location.value)
            ]
        )
        await interaction.response.send_message("✅ Report submitted!", ephemeral=True)


# ================= BUTTON VIEW =================

class SupportView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="General Help", style=discord.ButtonStyle.primary, emoji="💬")
    async def general_help(self, interaction, button):
        await interaction.response.send_modal(GeneralHelpModal())

    @discord.ui.button(label="Report In-Game Member", style=discord.ButtonStyle.success, emoji="🎮")
    async def ingame(self, interaction, button):
        await interaction.response.send_modal(InGameReportModal())

    @discord.ui.button(label="Report Community Member", style=discord.ButtonStyle.danger, emoji="🚨")
    async def community(self, interaction, button):
        await interaction.response.send_modal(CommunityReportModal())


# ================= COMMAND =================

@bot.command()
async def heh(ctx, staff_role_id: int):
    if ctx.author.id != ALLOWED_USER_ID:
        return

    global STAFF_ROLE_ID
    STAFF_ROLE_ID = staff_role_id

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
if not token:
    raise ValueError("No TOKEN found")

bot.run(token)
