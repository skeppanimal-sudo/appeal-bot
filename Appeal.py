import discord
from discord.ext import commands
import os

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="?", intents=intents)

ALLOWED_USER_ID = 1429110753683832985

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")

@bot.command()
async def heh(ctx):
    if ctx.author.id != ALLOWED_USER_ID:
        return

    # 🔹 Header Embed
    header = discord.Embed(
        title="Dreamy VR Support System",
        description="Please use this system to get help safely and efficiently. Staff will assist you as soon as possible.",
        color=discord.Color.blue()
    )

    header.add_field(
        name="\u200b",
        value="────────────────────────────",
        inline=False
    )

    # 🔹 Support Rules Embed
    support = discord.Embed(color=discord.Color.blue())

    support.add_field(
        name="1 • Open a Ticket",
        value="> Create a private support channel using the ticket system.",
        inline=True
    )

    support.add_field(
        name="2 • Explain Issue",
        value="> Clearly describe your problem so staff can help faster.",
        inline=True
    )

    support.add_field(
        name="3 • Stay Respectful",
        value="> Respect staff and others at all times.",
        inline=True
    )

    support.add_field(
        name="4 • No Spam",
        value="> Do not open multiple tickets for the same issue.",
        inline=True
    )

    support.add_field(
        name="5 • Follow Staff",
        value="> Listen to staff instructions during support.",
        inline=True
    )

    support.add_field(
        name="6 • No Troll Tickets",
        value="> Fake or joke tickets will result in punishment.",
        inline=True
    )

    support.add_field(
        name="\u200b",
        value="────────────────────────────",
        inline=False
    )

    support.set_footer(text="Dreamy VR • Support System")

    # 🔹 Image Embed (padded to match size)
    image_embed = discord.Embed(color=discord.Color.blue())

    # top divider
    image_embed.add_field(
        name="\u200b",
        value="────────────────────────────",
        inline=False
    )

    # padding fields (adjust if needed)
    for _ in range(6):
        image_embed.add_field(name="\u200b", value="\u200b", inline=True)

    # image at bottom
    image_embed.set_image(
        url="https://cdn.discordapp.com/attachments/1443984687436398698/1495500126582603838/image.png"
    )

    # 🔥 Send embeds in order
    await ctx.send(embed=header)
    await ctx.send(embed=support)
    await ctx.send(embed=image_embed)


# 🔐 Railway token
token = os.getenv("TOKEN")
if not token:
    raise ValueError("No TOKEN found in environment variables")

bot.run(token)
