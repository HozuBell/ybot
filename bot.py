import discord
from discord.ext import commands
from discord import app_commands
from gtts import gTTS
import os
import functools
from collections import deque
from dotenv import load_dotenv

# --- Load .env ---
load_dotenv()
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise ValueError("❌ Không tìm thấy TOKEN trong .env")

# Prefix h! và k! cho bot
intents = discord.Intents.default()
intents.voice_states = True
intents.message_content = True
bot = commands.Bot(command_prefix=["h!", "k!"], intents=intents)

# Hàng chờ TTS cho từng guild
tts_queues = {}

# --- Hàm phát TTS ---
async def tts_play(interaction_or_ctx, text: str, is_slash=False):
    guild = interaction_or_ctx.guild
    author = interaction_or_ctx.user if is_slash else interaction_or_ctx.author
    channel_send = interaction_or_ctx.channel

    if not author.voice or not author.voice.channel:
        msg = "❌ Bạn cần vào voice channel trước."
        if is_slash:
            await interaction_or_ctx.followup.send(msg, ephemeral=True)
        else:
            await channel_send.send(msg)
        return

    # Tạo queue cho guild nếu chưa có
    if guild.id not in tts_queues:
        tts_queues[guild.id] = deque()

    vc = guild.voice_client
    if vc is None:
        vc = await author.voice.channel.connect()

    # Thêm yêu cầu vào hàng chờ
    tts_queues[guild.id].append((author.display_name, text, channel_send, is_slash, interaction_or_ctx))

    # Nếu bot chưa đọc thì bắt đầu phát
    if not vc.is_playing():
        await play_next_in_queue(guild)

# --- Hàm xử lý phát hàng chờ ---
async def play_next_in_queue(guild):
    if guild.id not in tts_queues or not tts_queues[guild.id]:
        return

    vc = guild.voice_client
    if vc is None:
        return

    username, text, channel_send, is_slash, interaction_or_ctx = tts_queues[guild.id].popleft()
    filename = f"tts_{guild.id}.mp3"

    try:
        tts = gTTS(text=text, lang="vi")
        tts.save(filename)

        def after_play(error):
            if os.path.exists(filename):
                os.remove(filename)
            bot.loop.create_task(play_next_in_queue(guild))  # Phát tiếp theo trong hàng chờ

        source = discord.FFmpegPCMAudio(filename)
        vc.play(source, after=after_play)

        msg = f"🗣 {username} đang dùng bot và nói: **{text}**"
        if is_slash:
            await interaction_or_ctx.followup.send(msg)
        else:
            await channel_send.send(msg)

    except Exception as e:
        if is_slash:
            await interaction_or_ctx.followup.send(f"❌ Lỗi khi chuyển văn bản thành giọng nói: {e}")
        else:
            await channel_send.send(f"❌ Lỗi khi chuyển văn bản thành giọng nói: {e}")

# --- Slash command /noichuyen ---
@bot.tree.command(name="noichuyen", description="Chuyển văn bản thành giọng nói (tiếng Việt)")
async def noichuyen(interaction: discord.Interaction, text: str):
    await interaction.response.defer()
    await tts_play(interaction, text, is_slash=True)

# --- Prefix command h!say ---
@bot.command(name="say")
async def h_say(ctx: commands.Context, *, text: str):
    await tts_play(ctx, text)

# --- Prefix command h!sad ---
@bot.command(name="sad")
async def h_sad(ctx: commands.Context):
    await tts_play(ctx, "Phong ngáo")

# --- Prefix command k!mmblp ---
@bot.command(name="mmblp")
async def k_mmblp(ctx: commands.Context):
    await tts_play(ctx, "Phong ơi, sửa mic đi")

# --- Prefix command h!leave ---
@bot.command(name="leave")
async def h_leave(ctx: commands.Context):
    vc = ctx.guild.voice_client
    if vc:
        await vc.disconnect()
        tts_queues.pop(ctx.guild.id, None)  # Xoá hàng chờ khi thoát
        await ctx.send("👋 Bot đã rời khỏi voice channel")
    else:
        await ctx.send("⚠️ Bot hiện không ở trong voice channel.")

# --- Auto leave khi kênh trống ---
@bot.event
async def on_voice_state_update(member, before, after):
    for vc in bot.voice_clients:
        if len(vc.channel.members) == 1 and vc.channel.members[0] == bot.user:
            await vc.disconnect()
            tts_queues.pop(vc.guild.id, None)  # Xoá hàng chờ khi thoát
            await vc.channel.send("👋 Mọi người đã rời kênh, bot sẽ thoát.")

# --- Bot ready ---
@bot.event
async def on_ready():
    print(f"✅ Bot đã đăng nhập: {bot.user}")
    try:
        await bot.tree.sync()
        print("🔗 Commands global sync complete")
    except Exception as e:
        print(f"⚠️ Lỗi global sync: {e}")

# --- Run bot ---
bot.run(TOKEN)
