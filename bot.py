import nextcord
from nextcord.ext import commands
import yt_dlp
import os
from dotenv import load_dotenv
import asyncio

# --- Load .env ---
load_dotenv()
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise ValueError("❌ Không tìm thấy TOKEN trong .env")

intents = nextcord.Intents.default()
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents)

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
now_playing_messages = {}  # Lưu message embed để auto update


# --- Audio extraction ---
def get_audio_source(url: str):
    with yt_dlp.YoutubeDL(ytdlp_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if 'entries' in info:
            urls = [entry['url'] for entry in info['entries']]
            names = [entry.get('title', 'Không rõ') for entry in info['entries']]
            return urls, names
        else:
            return [info['url']], [info.get('title', 'Không rõ')]


# --- Music Controls ---
class MusicControls(nextcord.ui.View):
    def __init__(self, vc: nextcord.VoiceClient, guild_id: int):
        super().__init__(timeout=None)
        self.vc = vc
        self.guild_id = guild_id
        self.paused = False
        self.update_buttons_label()

    def update_buttons_label(self):
        for child in self.children:
            if getattr(child, "custom_id", "") == "pause_resume":
                child.label = "▶️ Tiếp tục" if self.paused else "⏸️ Tạm dừng"

    @nextcord.ui.button(label="⏸️ Tạm dừng", style=nextcord.ButtonStyle.secondary, custom_id="pause_resume")
    async def pause_resume(self, interaction: nextcord.Interaction, button: nextcord.ui.Button):
        if self.vc.is_playing():
            self.vc.pause()
            self.paused = True
        elif self.vc.is_paused():
            self.vc.resume()
            self.paused = False
        self.update_buttons_label()
        await interaction.response.edit_message(view=self)

    @nextcord.ui.button(label="⏭️ Skip", style=nextcord.ButtonStyle.primary)
    async def skip(self, interaction: nextcord.Interaction, button: nextcord.ui.Button):
        if self.vc.is_playing() or self.vc.is_paused():
            self.vc.stop()
            await interaction.response.send_message("⏭️ Bỏ qua bài hiện tại", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ Không có gì để skip", ephemeral=True)

    @nextcord.ui.button(label="⏹️ Stop Bot", style=nextcord.ButtonStyle.danger)
    async def stop_bot(self, interaction: nextcord.Interaction, button: nextcord.ui.Button):
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
    embed = nextcord.Embed(
        title="🎶 Hàng chờ nhạc",
        description=description,
        color=nextcord.Color.blurple()
    )
    return embed


# --- Play next song ---
def play_next(guild_id):
    if queues.get(guild_id):
        url = queues[guild_id].pop(0)
        title = titles[guild_id].pop(0)
        vc = nextcord.utils.get(bot.voice_clients, guild__id=guild_id)
        if vc:
            source = nextcord.FFmpegPCMAudio(
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
    else:
        vc = nextcord.utils.get(bot.voice_clients, guild__id=guild_id)
        if vc and not vc.is_playing():
            asyncio.run_coroutine_threadsafe(vc.disconnect(), bot.loop)
            if guild_id in play_channels:
                asyncio.run_coroutine_threadsafe(
                    play_channels[guild_id].send("👋 Hết nhạc, bot sẽ rời khỏi kênh."), bot.loop
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
        except nextcord.NotFound:
            msg = await channel.send(embed=embed, view=MusicControls(nextcord.utils.get(bot.voice_clients, guild__id=guild_id), guild_id))
            now_playing_messages[guild_id] = msg
    else:
        msg = await channel.send(embed=embed, view=MusicControls(nextcord.utils.get(bot.voice_clients, guild__id=guild_id), guild_id))
        now_playing_messages[guild_id] = msg


# --- Bot ready ---
@bot.event
async def on_ready():
    print(f"✅ Bot đã đăng nhập: {bot.user}")


# --- Slash command nhạc ---
@bot.slash_command(name="nhac", description="Phát nhạc hoặc playlist từ YouTube")
async def nhac(interaction: nextcord.Interaction, url: str):
    await interaction.response.defer(ephemeral=True)  # tránh lỗi Unknown interaction

    if not interaction.user.voice:
        await interaction.followup.send("❌ Bạn phải ở trong voice channel trước.", ephemeral=True)
        return

    vc = interaction.guild.voice_client
    if vc is None:
        channel = interaction.user.voice.channel
        vc = await channel.connect()

    try:
        play_channels[interaction.guild.id] = interaction.channel
        urls, names = get_audio_source(url)
        queues.setdefault(interaction.guild.id, []).extend(urls)
        titles.setdefault(interaction.guild.id, []).extend(names)
        if not vc.is_playing():
            play_next(interaction.guild.id)
        await interaction.followup.send(f"🎶 Đã thêm {len(urls)} bài vào hàng chờ.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Lỗi khi phát nhạc: {e}", ephemeral=True)


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
