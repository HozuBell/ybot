# bot.py
import os
import asyncio
from collections import deque
from uuid import uuid4

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from gtts import gTTS

import wavelink

# --- Load env ---
load_dotenv()
TOKEN = os.getenv("TOKEN") or os.getenv("DISCORD_TOKEN")
LAVALINK_URI = os.getenv("LAVALINK_URI") or os.getenv("LAVALINK_URL") or "http://localhost:2333"
LAVALINK_PASSWORD = os.getenv("LAVALINK_PASSWORD") or os.getenv("LAVALINK_PASS") or "youshallnotpass"

if not TOKEN:
    raise RuntimeError("❌ TOKEN not set. Set TOKEN or DISCORD_TOKEN environment variable.")

# --- Bot setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix=["h!", "k!", "p!"], intents=intents)

# ========== TTS SYSTEM ==========
tts_queues: dict[int, deque] = {}  # guild_id -> deque of (author_name, text, channel, is_slash, ctx_or_inter)

async def tts_play_enqueue(ctx_or_inter, text: str, is_slash=False):
    """Queue a TTS request for the guild and start playback if idle."""
    guild = ctx_or_inter.guild
    author = ctx_or_inter.user if is_slash else ctx_or_inter.author
    send_channel = ctx_or_inter.channel

    if not author.voice or not author.voice.channel:
        msg = "❌ Bạn cần vào voice channel trước."
        if is_slash:
            await ctx_or_inter.followup.send(msg, ephemeral=True)
        else:
            await send_channel.send(msg)
        return

    if guild.id not in tts_queues:
        tts_queues[guild.id] = deque()

    # connect if not connected
    vc = guild.voice_client
    if vc is None:
        vc = await author.voice.channel.connect()

    # enqueue: store ctx_or_inter so we can reply after
    tts_queues[guild.id].append((author.display_name, text, send_channel, is_slash, ctx_or_inter))

    # if not playing, start processing queue
    if not vc.is_playing():
        await _tts_play_next(guild)

async def _tts_play_next(guild: discord.Guild):
    """Internal: play next TTS in queue for this guild."""
    if guild.id not in tts_queues or not tts_queues[guild.id]:
        return

    vc = guild.voice_client
    if vc is None:
        return

    author_name, text, send_channel, is_slash, ctx_or_inter = tts_queues[guild.id].popleft()

    # unique filename to avoid race
    filename = f"tts_{guild.id}_{uuid4().hex}.mp3"
    try:
        tts = gTTS(text=text, lang="vi")
        tts.save(filename)
    except Exception as e:
        err = f"❌ Lỗi khi tạo TTS: {e}"
        if is_slash:
            await ctx_or_inter.followup.send(err)
        else:
            await send_channel.send(err)
        # try next item
        bot.loop.create_task(_tts_play_next(guild))
        return

    def _after_play(err):
        # remove file, then schedule next
        try:
            if os.path.exists(filename):
                os.remove(filename)
        except Exception:
            pass
        # schedule next
        bot.loop.create_task(_tts_play_next(guild))

    # play
    try:
        source = discord.FFmpegPCMAudio(filename)
        vc.play(source, after=_after_play)
    except Exception as e:
        if is_slash:
            bot.loop.create_task(ctx_or_inter.followup.send(f"❌ Lỗi phát TTS: {e}"))
        else:
            bot.loop.create_task(send_channel.send(f"❌ Lỗi phát TTS: {e}"))
        # cleanup and continue
        try:
            if os.path.exists(filename):
                os.remove(filename)
        except Exception:
            pass
        bot.loop.create_task(_tts_play_next(guild))
        return

    # notify
    info_msg = f"🗣️ **{author_name}** nói: {text}"
    if is_slash:
        await ctx_or_inter.followup.send(info_msg)
    else:
        await send_channel.send(info_msg)


# --- TTS Commands ---
@bot.tree.command(name="noichuyen", description="Chuyển văn bản thành giọng nói (tiếng Việt)")
async def noichuyen(interaction: discord.Interaction, text: str):
    await interaction.response.defer()
    await tts_play_enqueue(interaction, text, is_slash=True)

@bot.command(name="say")
async def cmd_say(ctx: commands.Context, *, text: str):
    await tts_play_enqueue(ctx, text, is_slash=False)

@bot.command(name="sad")
async def cmd_sad(ctx: commands.Context):
    await tts_play_enqueue(ctx, "Phong ngáo", is_slash=False)

@bot.command(name="mmblp")
async def cmd_mmblp(ctx: commands.Context):
    await tts_play_enqueue(ctx, "Phong ơi, sửa mic đi", is_slash=False)

@bot.command(name="leave")
async def cmd_leave(ctx: commands.Context):
    vc = ctx.guild.voice_client
    if vc:
        await vc.disconnect()
        tts_queues.pop(ctx.guild.id, None)
        await ctx.send("👋 Bot đã rời khỏi voice channel")
    else:
        await ctx.send("⚠️ Bot hiện không ở trong voice channel.")


# auto-disconnect when alone (works for both TTS and music)
@bot.event
async def on_voice_state_update(member: discord.Member, before, after):
    # iterate bot voice clients
    for vc in bot.voice_clients:
        # check channel exists and only bot remains
        if vc.channel and len(vc.channel.members) == 1 and vc.channel.members[0].id == bot.user.id:
            try:
                await vc.disconnect()
            except Exception:
                pass
            # clear TTS queue for that guild
            tts_queues.pop(vc.guild.id, None)


# ========== MUSIC SYSTEM (Wavelink v3 compatible) ==========
# simple queue per guild (list of wavelink.Track)
music_queues: dict[int, list] = {}
message_players: dict[int, discord.Message] = {}  # guild_id -> message (embed with controls)

print("Wavelink module version:", getattr(wavelink, "__version__", "unknown"))

async def connect_lavalink_node():
    await bot.wait_until_ready()
    node = wavelink.Node(uri=LAVALINK_URI, password=LAVALINK_PASSWORD)
    # try modern Pool.connect then fallback to NodePool.connect (some installs vary)
    try:
        # preferred in many v3+ examples
        await wavelink.Pool.connect(client=bot, nodes=[node])
        print("✅ Connected Lavalink via wavelink.Pool.connect")
        return
    except AttributeError:
        pass
    except Exception as e:
        print("⚠️ Pool.connect failed:", e)

    try:
        await wavelink.NodePool.connect(client=bot, nodes=[node])
        print("✅ Connected Lavalink via wavelink.NodePool.connect")
        return
    except AttributeError:
        pass
    except Exception as e:
        print("⚠️ NodePool.connect failed:", e)

    # final fallback: try Node.connect if available (signature may differ)
    try:
        if hasattr(wavelink.Node, "connect"):
            # some forks expose Node.connect(client=..., uri=..., password=...)
            try:
                await wavelink.Node.connect(client=bot, uri=LAVALINK_URI, password=LAVALINK_PASSWORD)
                print("✅ Connected Lavalink via wavelink.Node.connect")
                return
            except TypeError:
                # maybe signature is different; try passing node object
                await wavelink.Node.connect(node)
                print("✅ Connected Lavalink via wavelink.Node.connect(node)")
                return
    except Exception as e:
        print("❌ All Lavalink connect attempts failed:", e)

    print("❌ Could not connect to Lavalink — music commands will raise errors until fixed.")


class PlayerControls(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    @discord.ui.button(label="⏯", style=discord.ButtonStyle.secondary)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc:
            return await interaction.response.send_message("❌ Bot chưa kết nối kênh thoại.", ephemeral=True)
        if vc.is_paused():
            await vc.resume()
            await interaction.response.send_message("▶️ Resumed", ephemeral=True)
        else:
            await vc.pause()
            await interaction.response.send_message("⏸ Paused", ephemeral=True)

    @discord.ui.button(label="⏭", style=discord.ButtonStyle.primary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if not vc or not vc.is_playing():
            return await interaction.response.send_message("❌ Không có bài để skip.", ephemeral=True)
        await vc.stop()
        await interaction.response.send_message("⏭ Skipped", ephemeral=True)

    @discord.ui.button(label="⏹", style=discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc:
            await vc.disconnect()
        music_queues.pop(interaction.guild.id, None)
        await interaction.response.send_message("⏹ Stopped and cleared queue", ephemeral=True)


def make_music_embed(guild_id: int, current=None):
    q = music_queues.get(guild_id, [])
    desc = f"🎶 Đang phát: **{current if current else 'Không có'}**\n\n📜 Queue:\n"
    if q:
        for i, t in enumerate(q, start=1):
            desc += f"{i}. {t.title}\n"
    else:
        desc += "_(Trống)_"
    embed = discord.Embed(title="Music Player", description=desc, color=0x2ecc71)
    return embed


async def _play_next_from_queue(guild: discord.Guild):
    q = music_queues.get(guild.id, [])
    if not q:
        # nothing next
        msg = message_players.get(guild.id)
        if msg:
            try:
                await msg.edit(embed=make_music_embed(guild.id, None), view=None)
            except Exception:
                pass
        return

    vc = guild.voice_client
    if vc is None:
        return
    next_track = q.pop(0)
    try:
        await vc.play(next_track)
    except Exception as e:
        # older/newer API differences: try vc.play(next_track) or vc.play(await next_track)
        try:
            await vc.play(next_track)
        except Exception as ee:
            print("❌ Error playing track:", ee)
    # update embed
    msg = message_players.get(guild.id)
    if msg:
        try:
            await msg.edit(embed=make_music_embed(guild.id, next_track.title), view=PlayerControls(guild.id))
        except Exception:
            pass


@bot.event
async def on_wavelink_track_end(player: wavelink.Player, track, reason):
    # triggered when a track ends; schedule next from queue
    guild = player.guild
    await _play_next_from_queue(guild)


async def start_music(ctx_or_inter, query: str, is_slash=False):
    """Search and enqueue/play. Accepts YouTube link, playlist link, or query."""
    if isinstance(ctx_or_inter, commands.Context):
        author = ctx_or_inter.author
        channel = ctx_or_inter.channel
        send = ctx_or_inter.send
        guild = ctx_or_inter.guild
    else:
        author = ctx_or_inter.user
        channel = ctx_or_inter.channel
        send = ctx_or_inter.response.send_message
        guild = ctx_or_inter.guild

    if not author.voice or not author.voice.channel:
        if is_slash:
            await ctx_or_inter.response.send_message("❌ Bạn cần vào voice channel trước.", ephemeral=True)
        else:
            await channel.send("❌ Bạn cần vào voice channel trước.")
        return

    # connect if necessary
    vc = guild.voice_client
    if vc is None:
        try:
            vc = await author.voice.channel.connect(cls=wavelink.Player)
        except Exception as e:
            # fallback: connect without cls (some API differences)
            vc = await author.voice.channel.connect()

    # detect playlist link crudely
    try:
        if "list=" in query or "playlist" in query:
            # attempt playlist search
            try:
                playlist = await wavelink.YouTubePlaylist.search(query)
            except Exception:
                playlist = None
            if playlist and getattr(playlist, "tracks", None):
                tracks = playlist.tracks
                # if not currently playing, play first, enqueue rest
                if not vc.is_playing():
                    first = tracks.pop(0)
                    await vc.play(first)
                    music_queues[guild.id] = tracks.copy()
                    embed = make_music_embed(guild.id, first.title)
                    msg = await channel.send(f"▶️ Đang phát playlist: **{getattr(playlist, 'name', 'Playlist')}**", embed=embed, view=PlayerControls(guild.id))
                    message_players[guild.id] = msg
                else:
                    music_queues.setdefault(guild.id, []).extend(tracks)
                    await channel.send(f"➕ Đã thêm playlist vào queue ({len(tracks)} bài).")
                return
    except Exception:
        # ignore playlist path errors and fall through to single-track search
        pass

    # single track search
    try:
        track = await wavelink.YouTubeTrack.search(query, return_first=True)
    except Exception as e:
        track = None
    if not track:
        if is_slash:
            await ctx_or_inter.response.send_message("❌ Không tìm thấy nhạc.", ephemeral=True)
        else:
            await channel.send("❌ Không tìm thấy nhạc.")
        return

    # if playing -> enqueue, else play now
    if vc.is_playing():
        music_queues.setdefault(guild.id, []).append(track)
        if is_slash:
            await ctx_or_inter.response.send_message(f"➕ Đã thêm vào queue: **{track.title}**")
        else:
            await channel.send(f"➕ Đã thêm vào queue: **{track.title}**")
    else:
        await vc.play(track)
        music_queues[guild.id] = []  # reset queue
        embed = make_music_embed(guild.id, track.title)
        msg = await channel.send(embed=embed, view=PlayerControls(guild.id))
        message_players[guild.id] = msg
        if is_slash:
            await ctx_or_inter.response.send_message(f"▶️ Đang phát: **{track.title}**")

# --- Prefix music commands ---
@bot.command(name="play")
async def cmd_play(ctx: commands.Context, *, query: str):
    await start_music(ctx, query, is_slash=False)

@bot.command(name="skip")
async def cmd_skip(ctx: commands.Context):
    vc = ctx.guild.voice_client
    if vc and vc.is_playing():
        await vc.stop()
        await ctx.send("⏭ Đã skip bài.")
    else:
        await ctx.send("❌ Không có bài để skip.")

@bot.command(name="stop")
async def cmd_stop(ctx: commands.Context):
    vc = ctx.guild.voice_client
    if vc:
        await vc.disconnect()
    music_queues.pop(ctx.guild.id, None)
    await ctx.send("⏹ Đã dừng và xóa queue.")

@bot.command(name="queue")
async def cmd_queue(ctx: commands.Context):
    q = music_queues.get(ctx.guild.id, [])
    if not q:
        await ctx.send("📭 Queue trống.")
        return
    msg = "\n".join([f"{i+1}. {t.title}" for i, t in enumerate(q)])
    await ctx.send(f"🎶 Queue:\n{msg}")

# --- Slash music commands ---
@bot.tree.command(name="nhac", description="Phát nhạc từ link hoặc tên bài")
async def slash_nhac(interaction: discord.Interaction, search: str):
    await interaction.response.defer()
    await start_music(interaction, search, is_slash=True)

@bot.tree.command(name="queue", description="Xem hàng chờ")
async def slash_queue(interaction: discord.Interaction):
    q = music_queues.get(interaction.guild.id, [])
    if not q:
        await interaction.response.send_message("📭 Queue trống.")
        return
    msg = "\n".join([f"{i+1}. {t.title}" for i, t in enumerate(q)])
    await interaction.response.send_message(f"🎶 Queue:\n{msg}")

@bot.tree.command(name="thoat", description="Thoát khỏi voice channel")
async def slash_thoat(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc:
        await vc.disconnect()
        music_queues.pop(interaction.guild.id, None)
        await interaction.response.send_message("👋 Bot đã rời voice channel.")
    else:
        await interaction.response.send_message("❌ Bot không ở voice channel.")

# ========== BOT LIFECYCLE ==========
@bot.event
async def on_ready():
    print(f"✅ Bot ready: {bot.user} (id: {bot.user.id})")
    # connect lavalink node in background (try multiple connect methods)
    bot.loop.create_task(connect_lavalink_node())
    try:
        await bot.tree.sync()
        print("🔗 Slash commands synced")
    except Exception as e:
        print("⚠️ Slash sync failed:", e)

# run
if __name__ == "__main__":
    bot.run(TOKEN)
