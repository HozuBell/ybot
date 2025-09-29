import discord
from discord.ext import commands
from discord import app_commands
import wavelink
import asyncio
from gtts import gTTS
from collections import deque
import os
from dotenv import load_dotenv

# --- Load env ---
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# --- BOT ---
intents = discord.Intents.default()
intents.voice_states = True
intents.message_content = True
bot = commands.Bot(command_prefix=["h!", "k!", "p!"], intents=intents)

# ============================
# TTS SYSTEM
# ============================

tts_queues = {}

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

    if guild.id not in tts_queues:
        tts_queues[guild.id] = deque()

    vc = guild.voice_client
    if vc is None:
        vc = await author.voice.channel.connect()

    tts_queues[guild.id].append((author.display_name, text, channel_send, is_slash, interaction_or_ctx))

    if not vc.is_playing():
        await play_next_in_queue(guild)


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
            bot.loop.create_task(play_next_in_queue(guild))

        source = discord.FFmpegPCMAudio(filename)
        vc.play(source, after=after_play)

        msg = f"🗣 {username} nói: **{text}**"
        if is_slash:
            await interaction_or_ctx.followup.send(msg)
        else:
            await channel_send.send(msg)

    except Exception as e:
        err = f"❌ Lỗi TTS: {e}"
        if is_slash:
            await interaction_or_ctx.followup.send(err)
        else:
            await channel_send.send(err)


@bot.tree.command(name="noichuyen", description="Chuyển văn bản thành giọng nói (tiếng Việt)")
async def noichuyen(interaction: discord.Interaction, text: str):
    await interaction.response.defer()
    await tts_play(interaction, text, is_slash=True)


@bot.command(name="say")
async def h_say(ctx, *, text: str):
    await tts_play(ctx, text)


@bot.command(name="sad")
async def h_sad(ctx):
    await tts_play(ctx, "Phong ngáo")


@bot.command(name="mmblp")
async def k_mmblp(ctx):
    await tts_play(ctx, "Phong ơi, sửa mic đi")


@bot.command(name="leave")
async def h_leave(ctx):
    vc = ctx.guild.voice_client
    if vc:
        await vc.disconnect()
        tts_queues.pop(ctx.guild.id, None)
        await ctx.send("👋 Bot đã rời khỏi voice channel")
    else:
        await ctx.send("⚠️ Bot hiện không ở trong voice channel.")


# ============================
# MUSIC SYSTEM (Wavelink v3)
# ============================

class MusicPlayer(wavelink.Player):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.queue = asyncio.Queue()
        self.next_event = asyncio.Event()

    async def do_next(self):
        try:
            track = await self.queue.get()
        except Exception:
            return
        await self.play(track)
        self.next_event.clear()

    async def add_to_queue(self, track):
        await self.queue.put(track)
        if not self.is_playing():
            await self.do_next()


class PlayerControls(discord.ui.View):
    def __init__(self, player: MusicPlayer):
        super().__init__(timeout=None)
        self.player = player

    @discord.ui.button(label="⏯", style=discord.ButtonStyle.blurple)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.player.is_paused():
            await self.player.resume()
            await interaction.response.send_message("▶️ Tiếp tục", ephemeral=True)
        else:
            await self.player.pause()
            await interaction.response.send_message("⏸ Tạm dừng", ephemeral=True)

    @discord.ui.button(label="⏭", style=discord.ButtonStyle.green)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.player.stop()
        await interaction.response.send_message("⏭ Bỏ qua bài hát", ephemeral=True)

    @discord.ui.button(label="⏹", style=discord.ButtonStyle.red)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.player.disconnect()
        await interaction.response.send_message("⏹ Đã dừng phát nhạc", ephemeral=True)


@bot.command(name="play")
async def play(ctx, *, search: str):
    if not ctx.author.voice:
        return await ctx.send("❌ Bạn phải vào voice channel trước.")

    vc: MusicPlayer = ctx.voice_client
    if not vc:
        vc: MusicPlayer = await ctx.author.voice.channel.connect(cls=MusicPlayer)

    track = await wavelink.YouTubeTrack.search(search, return_first=True)
    if not track:
        return await ctx.send("❌ Không tìm thấy nhạc.")

    await vc.add_to_queue(track)
    await ctx.send(f"▶️ Đã thêm vào queue: `{track.title}`", view=PlayerControls(vc))


@bot.command(name="skip")
async def skip(ctx):
    if ctx.voice_client:
        await ctx.voice_client.stop()
        await ctx.send("⏭ Đã bỏ qua bài hát.")


@bot.command(name="stop")
async def stop(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("⏹ Đã dừng phát nhạc.")


@bot.command(name="queue")
async def queue(ctx):
    vc: MusicPlayer = ctx.voice_client
    if not vc or vc.queue.empty():
        return await ctx.send("📭 Queue trống.")

    upcoming = list(vc.queue._queue)
    desc = "\n".join([f"{i+1}. {t.title}" for i, t in enumerate(upcoming)])
    await ctx.send(f"🎶 Queue:\n{desc}")


@bot.tree.command(name="nhac", description="Phát nhạc từ link hoặc tên bài hát")
async def nhac(interaction: discord.Interaction, search: str):
    if not interaction.user.voice:
        return await interaction.response.send_message("❌ Bạn phải vào voice trước.")

    vc: MusicPlayer = interaction.guild.voice_client
    if not vc:
        vc: MusicPlayer = await interaction.user.voice.channel.connect(cls=MusicPlayer)

    track = await wavelink.YouTubeTrack.search(search, return_first=True)
    if not track:
        return await interaction.response.send_message("❌ Không tìm thấy nhạc.")

    await vc.add_to_queue(track)
    await interaction.response.send_message(f"▶️ Đang phát: `{track.title}`", view=PlayerControls(vc))


@bot.tree.command(name="thoat", description="Thoát khỏi voice channel")
async def thoat(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("⏹ Bot đã thoát voice channel.")


# ============================
# BOT EVENTS
# ============================

@bot.event
async def on_ready():
    print(f"✅ Bot đã đăng nhập: {bot.user}")
    await connect_nodes()
    try:
        await bot.tree.sync()
        print("🔗 Slash commands đã sync")
    except Exception as e:
        print(f"⚠️ Lỗi sync: {e}")


async def connect_nodes():
    await bot.wait_until_ready()
    node = wavelink.Node(uri="http://lavalink:2333", password="youshallnotpass")
    await wavelink.NodePool.connect(client=bot, nodes=[node])
    print("🎶 Đã kết nối Lavalink Node (Wavelink v3)")


bot.run(TOKEN)
