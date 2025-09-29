import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
from gtts import gTTS
import os
import asyncio
import functools
from dotenv import load_dotenv

# --- Load .env ---
load_dotenv()
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise ValueError("❌ Không tìm thấy TOKEN trong .env")

# Prefix h! cho bot
intents = discord.Intents.default()
intents.voice_states = True
intents.message_content = True
bot = commands.Bot(command_prefix="h!", intents=intents)

# --- YT-DLP config ---
ytdlp_opts = {
    'format': 'bestaudio/best',
    'quiet': True,
    'extract_flat': False,
    'noplaylist': False
}

queues = {}
titles = {}
play_channels = {}
now_playing_messages = {}

# --- Audio extraction (loại bỏ video vi phạm bản quyền / unavailable) ---
def get_audio_source(url: str, limit=25):
    with yt_dlp.YoutubeDL(ytdlp_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        urls, names = [], []
        if 'entries' in info:
            for entry in info['entries']:
                if not entry:
                    continue
                # Skip video private / deleted / unplayable
                if entry.get('is_private') or entry.get('age_limit') or entry.get('availability') in ['private', 'deleted', 'unplayable']:
                    continue
                urls.append(entry['url'])
                names.append(entry.get('title', 'Không rõ'))
                if len(urls) >= limit:
                    break
        else:
            urls.append(info['url'])
            names.append(info.get('title', 'Không rõ'))
        return urls, names

# --- Music Controls ---
class MusicControls(discord.ui.View):
    def __init__(self, vc: discord.VoiceClient, guild_id: int):
        super().__init__(timeout=None)
        self.vc = vc
        self.guild_id = guild_id
        self.paused = False
        self.update_buttons_label()

    def update_buttons_label(self):
        for child in self.children:
            if getattr(child, "custom_id", "") == "pause_resume":
                child.label = "▶️ Tiếp tục" if self.paused else "⏸️ Tạm dừng"

    @discord.ui.button(label="⏸️ Tạm dừng", style=discord.ButtonStyle.secondary, custom_id="pause_resume")
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.vc.is_playing():
            self.vc.pause()
            self.paused = True
        elif self.vc.is_paused():
            self.vc.resume()
            self.paused = False
        self.update_buttons_label()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="⏭️ Skip", style=discord.ButtonStyle.primary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.vc.is_playing() or self.vc.is_paused():
            self.vc.stop()
            await interaction.response.send_message("⏭️ Bỏ qua bài hiện tại", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ Không có gì để skip", ephemeral=True)

    @discord.ui.button(label="⏹️ Stop Bot", style=discord.ButtonStyle.danger)
    async def stop_bot(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc:
            queues[self.guild_id] = []
            titles[self.guild_id] = []
            await vc.disconnect()
            await interaction.response.send_message("⏹️ Bot đã dừng nhạc và thoát.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ Bot không ở voice channel.", ephemeral=True)

# --- Embed Queue ---
def format_queue_embed(guild_id):
    queue_list = titles.get(guild_id, [])
    description = "\n".join(f"{i+1}. {t}" for i, t in enumerate(queue_list)) or "📭 Hàng chờ trống."
    embed = discord.Embed(
        title="🎶 Hàng chờ nhạc",
        description=description,
        color=discord.Color.blurple()
    )
    return embed

# --- Play next song (tự động bỏ video không phát được) ---
def play_next(guild_id):
    while queues.get(guild_id):
        url = queues[guild_id].pop(0)
        title = titles[guild_id].pop(0)
        vc = discord.utils.get(bot.voice_clients, guild__id=guild_id)
        if not vc:
            continue
        try:
            source = discord.FFmpegPCMAudio(
                url,
                before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                options="-vn"
            )
            def after_play(error):
                if error:
                    print(f"Lỗi khi phát nhạc: {error}")
                asyncio.run_coroutine_threadsafe(update_now_playing(guild_id), bot.loop)
                play_next(guild_id)
            vc.play(source, after=after_play)
            asyncio.run_coroutine_threadsafe(update_now_playing(guild_id, title), bot.loop)
            break  # Nếu phát thành công, thoát vòng lặp
        except Exception as e:
            print(f"⚠️ Bỏ bài {title} vì không phát được: {e}")
            continue  # Thử bài tiếp theo
    else:
        # Queue trống hoặc không phát được bài nào
        vc = discord.utils.get(bot.voice_clients, guild__id=guild_id)
        if vc and not vc.is_playing():
            asyncio.run_coroutine_threadsafe(vc.disconnect(), bot.loop)
            if guild_id in play_channels:
                asyncio.run_coroutine_threadsafe(
                    play_channels[guild_id].send("👋 Bot đã rời kênh"), bot.loop
                )

# --- Update embed ---
async def update_now_playing(guild_id, title=None):
    channel = play_channels.get(guild_id)
    if not channel:
        return
    message = now_playing_messages.get(guild_id)
    embed = format_queue_embed(guild_id)
    if title:
        embed.title = f"🎶 Đang phát: {title}"
    if message:
        try:
            await message.edit(embed=embed)
        except discord.NotFound:
            msg = await channel.send(embed=embed, view=MusicControls(discord.utils.get(bot.voice_clients, guild__id=guild_id), guild_id))
            now_playing_messages[guild_id] = msg
    else:
        msg = await channel.send(embed=embed, view=MusicControls(discord.utils.get(bot.voice_clients, guild__id=guild_id), guild_id))
        now_playing_messages[guild_id] = msg

# --- Bot ready ---
@bot.event
async def on_ready():
    print(f"✅ Bot đã đăng nhập: {bot.user}")
    for guild in bot.guilds:
        try:
            await bot.tree.sync(guild=discord.Object(id=guild.id))
            print(f"🔗 Commands synced cho guild: {guild.name}")
        except Exception as e:
            print(f"⚠️ Lỗi sync cho {guild.name}: {e}")
    try:
        await bot.tree.sync()
        print("🔗 Commands global sync complete")
    except Exception as e:
        print(f"⚠️ Lỗi global sync: {e}")

# --- Play music command ---
@bot.tree.command(name="nhac", description="Phát nhạc hoặc playlist từ YouTube")
@app_commands.describe(url="Link YouTube (video hoặc playlist)")
async def nhac(interaction: discord.Interaction, url: str):
    await interaction.response.defer(ephemeral=True)
    if not interaction.user.voice:
        await interaction.followup.send("❌ Bạn phải ở trong voice channel trước.", ephemeral=True)
        return
    vc = interaction.guild.voice_client
    if vc is None:
        vc = await interaction.user.voice.channel.connect()
    try:
        play_channels[interaction.guild.id] = interaction.channel
        urls, names = get_audio_source(url)
        urls = urls[:25]
        names = names[:25]
        queues.setdefault(interaction.guild.id, []).extend(urls)
        titles.setdefault(interaction.guild.id, []).extend(names)
        if not vc.is_playing():
            play_next(interaction.guild.id)
        await interaction.followup.send(f"🎶 Đã thêm {len(urls)} bài vào hàng chờ.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Lỗi khi phát nhạc: {e}", ephemeral=True)

# --- TTS chung ---
async def tts_play(interaction_or_ctx, text: str, is_slash=False):
    guild = interaction_or_ctx.guild
    author = interaction_or_ctx.user if is_slash else interaction_or_ctx.author
    channel_send = interaction_or_ctx.channel
    if not author.voice or not author.voice.channel:
        msg = "❌ Bạn cần vào voice channel trước."
        if is_slash:
            await interaction_or_ctx.followup.send(msg, ephemeral=True)
        else:
            await channel_send.send(msg, delete_after=10)
        return

    vc = guild.voice_client
    if vc is None:
        vc = await author.voice.channel.connect()
    
    try:
        filename = f"tts_{guild.id}.mp3"
        tts = gTTS(text=text, lang="vi")
        tts.save(filename)

        if vc.is_playing():
            vc.stop()

        def after_play(error, file=filename):
            if os.path.exists(file):
                try:
                    os.remove(file)
                except Exception as e:
                    print(f"Lỗi xóa file {file}: {e}")

        source = discord.FFmpegPCMAudio(filename)
        vc.play(source, after=functools.partial(after_play))

        msg = f"🗣 Bot đang nói: **{text}**"
        if is_slash:
            await interaction_or_ctx.followup.send(msg)
        else:
            await channel_send.send(msg, delete_after=15)

    except Exception as e:
        msg = f"❌ Lỗi khi chuyển văn bản thành giọng nói: {e}"
        if is_slash:
            await interaction_or_ctx.followup.send(msg)
        else:
            await channel_send.send(msg, delete_after=10)

# --- Slash command /noichuyen ---
@bot.tree.command(name="noichuyen", description="Chuyển văn bản thành giọng nói (tiếng Việt)")
async def noichuyen(interaction: discord.Interaction, text: str):
    await interaction.response.defer()
    await tts_play(interaction, text, is_slash=True)

# --- Prefix command h!say ---
@bot.command(name="say")
async def h_say(ctx: commands.Context, *, text: str):
    await tts_play(ctx, text)

# --- Prefix command h!leave ---
@bot.command(name="leave")
async def h_leave(ctx: commands.Context):
    vc = ctx.guild.voice_client
    if vc:
        queues[ctx.guild.id] = []
        titles[ctx.guild.id] = []
        await vc.disconnect()
        await ctx.send("👋 Bot đã rời khỏi voice channel", delete_after=10)
    else:
        await ctx.send("⚠️ Bot hiện không ở trong voice channel.", delete_after=10)

# --- Auto leave ---
@bot.event
async def on_voice_state_update(member, before, after):
    for vc in bot.voice_clients:
        if len(vc.channel.members) == 1 and vc.channel.members[0] == bot.user:
            await vc.disconnect()
            if vc.guild.id in play_channels:
                await play_channels[vc.guild.id].send("👋 Mọi người đã rời kênh, bot sẽ thoát.")

# --- Run bot ---
bot.run(TOKEN)
