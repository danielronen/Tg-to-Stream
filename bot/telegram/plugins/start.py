from datetime import datetime
import re
import os
from bot import LOGGER
from bot.config import Telegram
from bot.helper.database import Database
from bot.helper.file_size import get_readable_file_size
from bot.helper.index import get_messages, clean_hebrew_title, get_tmdb_details, extract_season_episode, get_tmdb_ep_det
from bot.helper.media import is_media
from bot.telegram import StreamBot, UserBot
from pyrogram import filters, Client
from pyrogram.types import Message
from os.path import splitext
from pyrogram.errors import FloodWait
from pyrogram.enums.parse_mode import ParseMode
from asyncio import sleep
from urllib.parse import quote
from dotenv import load_dotenv
import httpx


tmdb_client = httpx.AsyncClient(
    timeout=httpx.Timeout(10.0, connect=5.0),
    limits=httpx.Limits(max_keepalive_connections=10, max_connections=20)
)

load_dotenv("config.env")
SURF_TG_BASE_URL = os.getenv("BASE_URL", "")
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
SOURCE_CHANNELS_STR = os.getenv("GROUPS_AND_CHANNELS", "")
SOURCE_CHANNELS = [
    int(ch.strip()) for ch in SOURCE_CHANNELS_STR.split(",") if ch.strip()
]

db = Database()


@StreamBot.on_message(filters.command("start") & filters.private)
async def start(bot: Client, message: Message):
    print(f"🟢 START command received from {message.from_user.id}")
    if "file_" in message.text:
        try:
            usr_cmd = message.text.split("_")[-1]
            data = usr_cmd.split("-")
            message_id, chat_id = data[0], f"-{data[1]}"
            file = await bot.get_messages(int(chat_id), int(message_id))
            media = is_media(file)
            await message.reply_cached_media(
                file_id=media.file_id, caption=f"**{media.file_name}**"
            )
        except Exception as e:
            print(f"An error occurred: {e}", flush=True)


@StreamBot.on_message(filters.command("index"))
async def start(bot: Client, message: Message):
    channel_id = message.chat.id
    AUTH_CHANNEL = await db.get_variable("auth_channel")
    if AUTH_CHANNEL is None or AUTH_CHANNEL.strip() == "":
        AUTH_CHANNEL = Telegram.AUTH_CHANNEL
    else:
        AUTH_CHANNEL = [channel.strip() for channel in AUTH_CHANNEL.split(",")]
    if str(channel_id) in AUTH_CHANNEL:
        try:
            last_id = message.id
            start_message = (
                "🔄 Please perform this action only once at the beginning of Surf-Tg usage.\n\n"
                "📋 File listing is currently in progress.\n\n"
                "🚫 Please refrain from sending any additional files or indexing other channels until this process completes.\n\n"
                "⏳ Please be patient and wait a few moments."
            )

            wait_msg = await message.reply(text=start_message)
            files = await get_messages(message.chat.id, 1, last_id)
            await db.add_btgfiles(files)
            await wait_msg.delete()
            done_message = (
                "✅ All your files have been successfully stored in the database. You're all set!\n\n"
                "📁 You don't need to index again unless you make changes to the database.\n\n"
                f"📁 Added: {len(files)} new files"
            )

            await bot.send_message(chat_id=message.chat.id, text=done_message)
        except FloodWait as e:
            LOGGER.info(f"Sleeping for {str(e.value)}s")
            await sleep(e.value)
            await message.reply(
                text=f"Got Floodwait of {str(e.value)}s",
                disable_web_page_preview=True,
                parse_mode=ParseMode.MARKDOWN,
            )
    else:
        await message.reply(text="Channel is not in AUTH_CHANNEL")



@StreamBot.on_message(filters.channel & (filters.document | filters.video))
async def file_receive_handler(bot: Client, message: Message):
    channel_id = message.chat.id
    if str(channel_id):
        try:
            title = None
            file = message.video or message.document
            msg_id = message.id
            # --- IMPROVED TITLE LOGIC ---
            
            if message.caption:
                # Priority B: Hebrew Caption (limited to 30 chars)
                clean_caption = message.caption.strip().split("\n")[0]  # Take first line only
                clean_caption = clean_hebrew_title(clean_caption)
                if len(clean_caption) > 33:
                    clean_caption = clean_caption[:33]
                    if " " in clean_caption:
                        title = clean_caption.rsplit(" ", 1)[0]
                    else:
                        title = clean_caption
                        # 2. Last resort fallback if both above failed
                else:
                    title = clean_caption
            else:
                raw_fn = file.file_name or ""
                title, _ = splitext(raw_fn)
                is_default = any(
                    x in title.lower()
                    for x in ["default_name", "default name", "undefined"]
                )
                if not is_default and title:
                    # Priority A: Original File Name (if it's not generic)
                    title = clean_hebrew_title(title)
                    if len(title) > 33:
                        title = title[:33]
                        if " " in title:
                            title = title.rsplit(" ", 1)[0]
                            
                            
            # --- Description ---
            full_desc = (
                message.caption.strip() if message.caption else file.file_name.strip()
            )
            # --------------------- NEW POSTER LOGIC -----------------------
            
            tmdb_res = await get_tmdb_details(title, full_desc,TMDB_API_KEY,tmdb_client)
            raw_desc = full_desc
            se,ep = extract_season_episode(full_desc)
            
            poster_url = None
            ep_name = None
            thumb_url = None
            ep_overview = None
            background_url = None
            released = None
            
            if tmdb_res:
                tmdb_id = tmdb_res.get("id")
                media_type = tmdb_res.get("media_type")
                p_path = tmdb_res.get("poster_path")
                b_path = tmdb_res.get("backdrop_path")
                overview = tmdb_res.get("overview")
                if p_path and b_path:
                    poster_url = f"https://image.tmdb.org/t/p/w500{p_path}"
                    background_url = f"https://image.tmdb.org/t/p/w1280{b_path}"
                elif p_path:
                    poster_url = f"https://image.tmdb.org/t/p/w500{p_path}"
                    background_url = f"https://image.tmdb.org/t/p/w1280{p_path}"
                if overview:
                    full_desc = overview
                if tmdb_res.get("release_date"):
                    released = tmdb_res.get("release_date")
                if tmdb_res.get("first_air_date"):
                    released = tmdb_res.get("first_air_date")
                    
                if ep or se and media_type == "tv":
                    ep_name, ep_ow, ep_thumb, air_date = await get_tmdb_ep_det(tmdb_id,se,ep,TMDB_API_KEY,tmdb_client)
                    if ep_name:
                        episode_name = ep_name
                    if ep_ow:
                        ep_overview = ep_ow + f"\nS{se:02d}E{ep:02d}"
                    if ep_thumb:
                        thumb_url = ep_thumb                     
                    if air_date:
                        released = air_date
                else:
                    ep_overview = raw_desc
                    episode_name = title
                    thumb_url = background_url                            
                     
            #poster_url, background_url = get_tmdb_poster(title,TMDB_API_KEY)
            if poster_url == None:
                has_real_thumb = hasattr(file, "thumbs") and file.thumbs
                if has_real_thumb:
                    poster_url = f"{SURF_TG_BASE_URL}/api/thumb/{channel_id}?id={msg_id}"
                    background_url = f"{SURF_TG_BASE_URL}/api/thumb/{channel_id}?id={msg_id}"
                else:# Fallback to Hebrew Placeholder
                    clean_t = quote(title)
                    poster_url = f"https://placehold.jp/40/1a1a2e/ffffff/600x900.png?text={clean_t}"
                    background_url = f"https://placehold.jp/40/1a1a2e/ffffff/600x900.png?text={clean_t}"

            if not thumb_url:
                has_real_thumb = hasattr(file, "thumbs") and file.thumbs
                if has_real_thumb:
                    thumb_url = f"{SURF_TG_BASE_URL}/api/thumb/{channel_id}?id={msg_id}"
                    
            if not ep_overview or not episode_name:
                ep_overview = raw_desc
                episode_name = title
                
            if not released:
                released = datetime.now().strftime("%Y-%m-%d")
            hash = file.file_unique_id
            size = get_readable_file_size(file.file_size)
            mime_type = file.mime_type

            await db.add_tgfiles(
                str(channel_id),
                msg_id,
                str(hash),
                str(title),
                str(full_desc),
                str(size),
                str(mime_type),
                img=poster_url,
                background=background_url,
                ep_overview=ep_overview,
                ep_name=episode_name,
                thumb_url=thumb_url,
                released=released
                
            )

        except Exception as e:
            print(f"Error in file_receive_handler: {e}", flush=True)


@UserBot.on_message(filters.chat(SOURCE_CHANNELS))
async def forward_videos_to_target(bot: Client, message: Message):
    try:
        # Get target channel
        AUTH_CHANNEL = await db.get_variable("auth_channel")
        if AUTH_CHANNEL is None or AUTH_CHANNEL.strip() == "":
            AUTH_CHANNEL = Telegram.AUTH_CHANNEL

        target = (
            AUTH_CHANNEL.split(",")[0].strip()
            if isinstance(AUTH_CHANNEL, str)
            else str(AUTH_CHANNEL[0])
        )
        target_id = int(target)
        # Check if it's a video or video document
        should_forward = False
        if message.video:
            should_forward = True
        elif message.document:
            mime_type = message.document.mime_type or ""
            file_name = message.document.file_name or ""
            if mime_type.startswith("video/") or file_name.endswith(
                (".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv")
            ):
                should_forward = True
                
        if should_forward:
            await message.forward(target_id)

    except Exception as e:
        print(f"   ❌ Error: {e}")
        import traceback

        traceback.print_exc()
