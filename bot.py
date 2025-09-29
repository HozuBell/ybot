import discord
from discord.ext import commands
from discord import app_commands
from gtts import gTTS
import os
import asyncio
from collections import deque
from dotenv import load_dotenv
import wavelink

# --- Load .env ---
load_dotenv()
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise ValueError("❌ Không tìm thấy TOKEN trong .env")

# Prefix h!, k!, p!
intents = discord.Intents.default()
intents.voice_states = True
intents.message_content = True
bot = commands.Bot(command_prefix=["h!", "k!", "p!"], intents=intents)

# ==========================
# 🎤 Hệ thống TTS
# ==========================
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

        msg = f"🗣 {username} đang dùng bot và nói: **{text}**"
        if is_slash:
            await interaction_or_ctx.followup.send(msg)
        else:
            await channel_send.send(msg)

    except Exception as e:
        if is_slash:
            await interaction_or_ctx.followup.send(f"❌ Lỗi TTS: {e}")
        else:
            await channel_send.send(f"❌ Lỗi TTS: {e}")

# Slash command TTS
@bot.tree.command(name="noichuyen", description="Chuyển văn bản thành giọng nói (tiếng Việt)")
async def noichuyen(interaction: discord.Interaction, text: str):
    await interaction.response.defer()
    await tts_play(interaction, text, is_slash=True)

# Prefix commands TTS
@bot.command(name="say")
async def h_say(ctx: commands.Context, *, text: str):
    await tts_play(ctx, text)

@bot.command(name="sad")
async def h_sad(ctx: commands.Context):
    await tts_play(ctx, "Phong ngáo")

@bot.command(name="mmblp")
async def k_mmblp(ctx: commands.Context):
    await tts_play(ctx, "Phong ơi, sửa mic đi")

@bot.command(name="leave")
async def h_leave(ctx: commands.Context):
    vc = ctx.guild.voice_client
    if vc:
        await vc.disconnect()
        tts_queues.pop(ctx.guild.id, None)
        await ctx.send("👋 Bot đã rời khỏi voice channel")
    else:
        await ctx.send("⚠️ Bot không ở trong voice channel.")

@bot.event
async def on_voice_state_update(member, before, after):
    for vc in bot.voice_clients:
        if len(vc.channel.members) == 1 and vc.channel.members[0] == bot.user:
            await vc.disconnect()
            tts_queues.pop(vc.guild.id, None)
            await vc.channel.send("👋 Mọi người đã rời kênh, bot sẽ thoát.")

# ==========================
# 🎶 Hệ thống Music (Wavelink)
# ==========================
queues = {}
messages = {}
DJ_ROLE_NAME = "DJ"

def is_dj(member: discord.Member):
    return member.guild_permissions.administrator or any(role.name == DJ_ROLE_NAME for role in member.roles)

def music_embed(guild_id, current=None):
    queue_list = queues.get(guild_id, [])
    desc = f"🎶 Đang phát: **{current if current else 'Không có'}**\n\n📜 Queue:\n"
    if queue_list:
        desc += "\n".join([f"{i+1}. {track.title}" for i, track in enumerate(queue_list)])
    else:
        desc += "_(Trống)_"
    embed = discord.Embed(title="🎵 Music Player", description=desc, color=0x2ecc71)
    return embed

class MusicControl(discord.ui.View):
    def __init__(self, player: wavelink.Player, guild_id: int):
        super().__init__(timeout=None)
        self.player = player
        self.guild_id = guild_id

    @discord.ui.button(label="⏸ Pause", style=discord.ButtonStyle.secondary)
    async def pause(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_dj(interaction.user):
            await interaction.response.send_message("❌ Cần role DJ/Admin.", ephemeral=True)
            return
        if self.player.is_playing():
            await self.player.pause()
            await interaction.response.send_message("⏸ Paused!", ephemeral=True)

    @discord.ui.button(label="▶ Resume", style=discord.ButtonStyle.success)
    async def resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_dj(interaction.user):
            await interaction.response.send_message("❌ Cần role DJ/Admin.", ephemeral=True)
            return
        if self.player.is_paused():
            await self.player.resume()
            await interaction.response.send_message("▶ Resumed!", ephemeral=True)

    @discord.ui.button(label="⏭ Skip", style=discord.ButtonStyle.primary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_dj(interaction.user):
            await interaction.response.send_message("❌ Cần role DJ/Admin.", ephemeral=True)
            return
        if self.player.is_playing():
            await self.player.stop()
            await interaction.response.send_message("⏭ Skipped!", ephemeral=True)

    @discord.ui.button(label="⏹ Stop", style=discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_dj(interaction.user):
            await interaction.response.send_message("❌ Cần role DJ/Admin.", ephemeral=True)
            return
        queues[self.guild_id] = []
        await self.player.stop()
        await interaction.response.send_message("⏹ Stopped & cleared queue!", ephemeral=True)

@bot.event
async def on_ready():
    print(f"✅ Bot đã đăng nhập: {bot.user}")
    try:
        await bot.tree.sync()
        print("🔗 Slash commands synced")
    except Exception as e:
        print(f"⚠️ Lỗi sync: {e}")
    bot.loop.create_task(connect_nodes())

async def connect_nodes():
    await bot.wait_until_ready()
    await wavelink.NodePool.create_node(
        bot=bot,
        host="127.0.0.1",
        port=2333,
        password="youshallnotpass"
    )

@bot.event
async def on_wavelink_track_end(player: wavelink.Player, track, reason):
    gid = player.guild.id
    if queues.get(gid):
        next_track = queues[gid].pop(0)
        await player.play(next_track)
        if gid in messages:
            embed = music_embed(gid, next_track.title)
            view = MusicControl(player, gid)
            await messages[gid].edit(embed=embed, view=view)
    else:
        if gid in messages:
            embed = music_embed(gid, None)
            await messages[gid].edit(embed=embed, view=None)

async def start_play(ctx_or_inter, url, is_slash=False):
    if isinstance(ctx_or_inter, commands.Context):
        author = ctx_or_inter.author
        channel = ctx_or_inter.channel
    else:
        author = ctx_or_inter.user
        channel = ctx_or_inter.channel

    if not author.voice:
        msg = "❌ Bạn phải vào voice channel trước!"
        if is_slash:
            await ctx_or_inter.response.send_message(msg, ephemeral=True)
        else:
            await ctx_or_inter.send(msg)
        return

    vc = author.voice.channel
    if not author.guild.voice_client:
        player: wavelink.Player = await vc.connect(cls=wavelink.Player)
    else:
        player: wavelink.Player = author.guild.voice_client

    gid = author.guild.id

    if "list=" in url:
        playlist = await wavelink.YouTubePlaylist.search(url)
        if not playlist:
            msg = "❌ Không tìm thấy playlist."
            if is_slash:
                await ctx_or_inter.response.send_message(msg, ephemeral=True)
            else:
                await ctx_or_inter.send(msg)
            return
        queues.setdefault(gid, [])
        if not player.is_playing():
            first = playlist.tracks.pop(0)
            await player.play(first)
            queues[gid].extend(playlist.tracks)
            embed = music_embed(gid, first.title)
            view = MusicControl(player, gid)
            if is_slash:
                await ctx_or_inter.response.send_message(f"▶ Phát playlist: **{playlist.name}**", embed=embed, view=view)
                messages[gid] = await ctx_or_inter.original_response()
            else:
                msg = await channel.send(f"▶ Phát playlist: **{playlist.name}**", embed=embed, view=view)
                messages[gid] = msg
        else:
            queues[gid].extend(playlist.tracks)
            msg = f"➕ Thêm playlist **{playlist.name}** ({len(playlist.tracks)} bài)."
            if is_slash:
                await ctx_or_inter.response.send_message(msg)
            else:
                await ctx_or_inter.send(msg)
    else:
        track = await wavelink.YouTubeTrack.search(url, return_first=True)
        if not track:
            msg = "❌ Không tìm thấy bài hát."
            if is_slash:
                await ctx_or_inter.response.send_message(msg, ephemeral=True)
            else:
                await ctx_or_inter.send(msg)
            return
        if player.is_playing():
            queues.setdefault(gid, []).append(track)
            msg = f"➕ Thêm: **{track.title}**"
            if is_slash:
                await ctx_or_inter.response.send_message(msg)
            else:
                await ctx_or_inter.send(msg)
        else:
            await player.play(track)
            queues[gid] = []
            embed = music_embed(gid, track.title)
            view = MusicControl(player, gid)
            if is_slash:
                await ctx_or_inter.response.send_message(embed=embed, view=view)
                messages[gid] = await ctx_or_inter.original_response()
            else:
                msg = await channel.send(embed=embed, view=view)
                messages[gid] = msg

# Slash commands Music
@bot.tree.command(name="nhac", description="Phát nhạc từ YouTube (link)")
async def nhac(interaction: discord.Interaction, url: str):
    await start_play(interaction, url, is_slash=True)

@bot.tree.command(name="search", description="Tìm và phát nhạc từ YouTube")
async def search(interaction: discord.Interaction, *, keyword: str):
    track = await wavelink.YouTubeTrack.search(keyword, return_first=True)
    if track:
        await start_play(interaction, track.uri, is_slash=True)
    else:
        await interaction.response.send_message("❌ Không tìm thấy kết quả.")

# Prefix commands Music
@bot.command(name="play")
async def play(ctx, *, url: str):
    await start_play(ctx, url, is_slash=False)

@bot.command(name="search")
async def search_cmd(ctx, *, keyword: str):
    track = await wavelink.YouTubeTrack.search(keyword, return_first=True)
    if track:
        await start_play(ctx, track.uri, is_slash=False)
    else:
        await ctx.send("❌ Không tìm thấy kết quả.")

# --- Run bot ---
bot.run(TOKEN)
