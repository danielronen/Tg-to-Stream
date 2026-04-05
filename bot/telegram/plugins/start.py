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
import math
import time


tmdb_client = httpx.AsyncClient(
    timeout=httpx.Timeout(10.0, connect=5.0),
    limits=httpx.Limits(max_keepalive_connections=10, max_connections=20)
)

load_dotenv("config.env")
SURF_TG_BASE_URL = os.getenv("BASE_URL", "")
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
AUTH_CHANNEL = os.getenv("AUTH_CHANNEL", "")
SOURCE_CHANNELS_STR = os.getenv("GROUPS_AND_CHANNELS", "")
SOURCE_CHANNELS = [
    int(ch.strip()) for ch in SOURCE_CHANNELS_STR.split(",") if ch.strip()
]

db = Database()

BLACKLIST_CACHE = []
cached_blacklist = []

async def reload_blacklist():
    global BLACKLIST_CACHE
    # This calls your DB to get all phrases
    BLACKLIST_CACHE = await db.get_all_blacklist_phrases() 
    print(f"✅ Blacklist updated: {len(BLACKLIST_CACHE)} phrases loaded.")
    #cached_blacklist = [p['phrase'] for p in BLACKLIST_CACHE]

def is_blacklisted(message: Message) -> bool:
    """Returns True if any blacklisted phrase is found in caption or filename."""
    # Combine caption and filename into one searchable text
    caption = message.caption or ""
    # Check video filename or document filename
    filename = ""
    if message.video:
        filename = message.video.file_name or ""
    elif message.document:
        filename = message.document.file_name or ""
        
    combined_text = f"{caption} {filename}"

    # Check if any phrase exists in the text
    for phrase in BLACKLIST_CACHE:
        if phrase in combined_text:
            return True
    return False

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
            full_desc = re.sub(r'https?://\S+','', full_desc)
            # --------------------- NEW POSTER LOGIC -----------------------
            
            tmdb_res = await get_tmdb_details(title, full_desc,TMDB_API_KEY,tmdb_client)
            raw_desc = full_desc
            se,ep = extract_season_episode(full_desc)
            if title == "האח הגדול" and se:
                se += 8
            
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
                        if title == "האח הגדול":
                            se -= 8
                        ep_overview = ep_ow + f"\nS{se:02d}E{ep:02d}"
                    if ep_thumb:
                        thumb_url = ep_thumb                     
                    
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
        if is_blacklisted(message):
            print(f"   🚫 Blocked by blacklist: {message.id}")
            return # Exit immediately
        
        # Get target channel
        AUTH_CHANNEL = await db.get_variable("auth_channel")
        if AUTH_CHANNEL is None or AUTH_CHANNEL.strip() == "":
            AUTH_CHANNEL = Telegram.AUTH_CHANNEL

        target = (
            AUTH_CHANNEL.split(",")[0].strip()
            if isinstance(AUTH_CHANNEL, str)
            else str(AUTH_CHANNEL[0])
        )
        #target_id = int(target)
        raw_id = target.strip()
        if not raw_id.startswith("-"):
            # If the ID doesn't start with '-', it's a Channel/Group ID 
            # missing its prefix. We add -100 for Pyrogram resolution.
            target_id = int(f"-100{raw_id}")
        else:
            target_id = int(raw_id)
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


@UserBot.on_message(filters.command("add_block"))
async def add_to_blacklist(bot, message):
    phrase = message.text.split(None, 1)[1]
    await db.add_phrase_to_db(phrase) # Save to DB
    await reload_blacklist()         # Update the RAM cache
    await message.reply(f"🚫 Added '{phrase}' to blacklist.")
    
    
# --- COMMAND: DELETE FROM BLACKLIST ---
@UserBot.on_message(filters.command("del_block"))
async def cmd_del_block(bot, message):
    if len(message.command) < 2:
        return await message.reply("❌ Usage: `/del_block phrase`")
    
    phrase = message.text.split(None, 1)[1]
    await db.remove_phrase_from_db(phrase)
    await reload_blacklist() # Update RAM immediately
    await message.reply(f"🗑️ Removed **'{phrase}'** from the blacklist.")

# --- COMMAND: SHOW BLACKLIST ---
@UserBot.on_message(filters.command("show_block"))
async def cmd_show_block(bot, message):
    if not BLACKLIST_CACHE:
        return await message.reply("📭 The blacklist is currently empty.")
    
    list_text = "🚫 **Current Blacklist:**\n\n"
    for i, phrase in enumerate(BLACKLIST_CACHE, 1):
        list_text += f"{i}. `{phrase}`\n"
    
    await message.reply(list_text)
    
    
# --- Helper: Progress Bar Function ---
async def progress_bar(current, total, status_msg, action_name, start_time):
    """
    Generates a visual progress bar: [■■■■□□□□] 50.0%
    Updates every 5 seconds to avoid Telegram rate limits.
    """
    now = time.time()
    # Only update every 5 seconds or when finished
    if not hasattr(progress_bar, "last_update"):
        progress_bar.last_update = 0
        
    if now - progress_bar.last_update < 5 and current != total:
        return

    progress_bar.last_update = now
    
    percentage = current * 100 / total
    finished_blocks = int(percentage / 10)
    remaining_blocks = 10 - finished_blocks
    
    bar = "■" * finished_blocks + "□" * remaining_blocks
    
    # Calculate speed
    elapsed_time = now - start_time
    speed = current / elapsed_time if elapsed_time > 0 else 0
    speed_kb = speed / 1024
    
    text = (
        f"**{action_name}**\n"
        f"<code>[{bar}] {percentage:.1f}%</code>\n"
        f"📊 {current / (1024*1024):.1f}MB / {total / (1024*1024):.1f}MB\n"
        f"⚡ Speed: {speed_kb:.1f} KB/s"
    )
    
    try:
        await status_msg.edit(text)
    except Exception:
        pass

@UserBot.on_message(filters.command("grab") & filters.me)
async def grab_batch_restricted(bot: Client, message: Message):
    # Handle multiple links (split by whitespace/newline)
    links = message.text.split()[1:]
    
    if not links:
        return await message.reply("❌ Usage: `/grab link1 link2 link3`")
    
    main_status = await message.reply(f"🚀 **Batch Started:** Processing {len(links)} files...")

    for index, link in enumerate(links, 1):
        try:
            # 1. Parse Link
            parts = link.rstrip("/").split("/")
            msg_id = int(parts[-1])
            
            if "t.me/c/" in link:
                c_index = parts.index("c")
                source_chat_id = int(f"-100{parts[c_index + 1]}")
            else:
                tme_index = next(i for i, p in enumerate(parts) if "t.me" in p)
                source_chat_id = parts[tme_index + 1]

            await main_status.edit(f"📂 **[{index}/{len(links)}]** Fetching message metadata...")
            target_msg = await bot.get_messages(source_chat_id, msg_id)

            if not target_msg or not target_msg.media:
                await message.reply(f"⚠️ Skip: Link {index} has no media.")
                continue

            # 2. Download with Progress Bar
            start_t = time.time()
            file_path = await target_msg.download(
                progress=progress_bar,
                progress_args=(main_status, f"📥 Downloading File {index}/{len(links)}", start_t)
            )

            # 3. Resolve Target Auth Channel
            AUTH_CHANNEL = await db.get_variable("auth_channel")
            if not AUTH_CHANNEL:
                AUTH_CHANNEL = Telegram.AUTH_CHANNEL
            target = AUTH_CHANNEL.split(",")[0].strip() if isinstance(AUTH_CHANNEL, str) else str(AUTH_CHANNEL[0])
            target_id = int(f"-100{target.strip()}") if not target.strip().startswith("-") else int(target.strip())

            # 4. Upload with Progress Bar
            start_t = time.time()
            caption = target_msg.caption or ""
            
            if target_msg.video:
                await bot.send_video(
                    target_id, video=file_path, caption=caption,
                    progress=progress_bar,
                    progress_args=(main_status, f"📤 Uploading File {index}/{len(links)}", start_t)
                )
            else:
                await bot.send_document(
                    target_id, document=file_path, caption=caption,
                    progress=progress_bar,
                    progress_args=(main_status, f"📤 Uploading File {index}/{len(links)}", start_t)
                )

            # 5. Cleanup
            if os.path.exists(file_path):
                os.remove(file_path)

        except Exception as e:
            await message.reply(f"❌ Error on link {index}: {e}")
            continue

    await main_status.edit(f"✅ **Batch Complete!** Processed {len(links)} files.")
    

@UserBot.on_message(filters.command("grab_range") & filters.me)
async def grab_range_restricted(bot: Client, message: Message):
    """
    Usage: /grab_range [start_link] [end_link]
    Example: /grab_range https://t.me/c/123/10 https://t.me/c/123/20
    """
    args = message.text.split()
    if len(args) < 3:
        return await message.reply("❌ Usage: `/grab_range [start_link] [end_link]`")

    try:
        # 1. Parse IDs from both links
        start_parts = args[1].rstrip("/").split("/")
        end_parts = args[2].rstrip("/").split("/")
        
        start_id = int(start_parts[-1])
        end_id = int(end_parts[-1])
        
        # Determine Source Channel
        if "t.me/c/" in args[1]:
            c_index = start_parts.index("c")
            source_chat_id = int(f"-100{start_parts[c_index + 1]}")
        else:
            tme_index = next(i for i, p in enumerate(start_parts) if "t.me" in p)
            source_chat_id = start_parts[tme_index + 1]

        if start_id > end_id:
            return await message.reply("❌ Start ID must be smaller than End ID.")

        total_to_check = (end_id - start_id) + 1
        main_status = await message.reply(f"🔍 Checking range: `{start_id}` to `{end_id}` ({total_to_check} IDs)...")

        success_count = 0
        
        # 2. Loop through the numerical range
        for current_id in range(start_id, end_id + 1):
            try:
                # We update the status so you know the bot hasn't frozen
                await main_status.edit(f"🛰️ Scanning ID: `{current_id}`\n✅ Found: {success_count} videos so far.")
                
                target_msg = await bot.get_messages(source_chat_id, current_id)
                
                # --- THE "GAP" FILTER ---
                # Skip if message is empty, deleted, or has no video/document
                if not target_msg or target_msg.empty or not target_msg.media:
                    continue 
                
                # Check if it's actually a video or video-document
                is_video = target_msg.video or (
                    target_msg.document and 
                    (target_msg.document.mime_type or "").startswith("video/")
                )
                
                if not is_video:
                    continue

                # 3. If we pass the filters, start the Bridge Process
                start_t = time.time()
                file_path = await target_msg.download(
                    progress=progress_bar,
                    progress_args=(main_status, f"📥 Downloading ID {current_id}", start_t)
                )

                # Target Resolution
                AUTH_CHANNEL = await db.get_variable("auth_channel") or Telegram.AUTH_CHANNEL
                target = AUTH_CHANNEL.split(",")[0].strip() if isinstance(AUTH_CHANNEL, str) else str(AUTH_CHANNEL[0])
                target_id = int(f"-100{target.strip()}") if not target.strip().startswith("-") else int(target.strip())

                start_t = time.time()
                caption = target_msg.caption or ""
                
                await bot.send_video(
                    target_id, video=file_path, caption=caption,
                    progress=progress_bar,
                    progress_args=(main_status, f"📤 Uploading ID {current_id}", start_t)
                )

                if os.path.exists(file_path):
                    os.remove(file_path)
                
                success_count += 1
                # Small sleep to avoid aggressive flood waits
                await asyncio.sleep(1)

            except Exception as e:
                print(f"⚠️ Error on ID {current_id}: {e}")
                continue

        await main_status.edit(f"✅ **Range Complete!**\nScanned: {total_to_check} IDs\nSuccessfully Bridged: {success_count} videos.")

    except Exception as e:
        await message.reply(f"❌ Range Error: {e}")