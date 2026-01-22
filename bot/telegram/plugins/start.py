import re
from bot import LOGGER
from bot.config import Telegram
from bot.helper.database import Database
from bot.helper.file_size import get_readable_file_size
from bot.helper.index import get_messages, clean_hebrew_title
from bot.helper.media import is_media
from bot.telegram import StreamBot
from pyrogram import filters, Client
from pyrogram.types import Message
from os.path import splitext
from pyrogram.errors import FloodWait
from pyrogram.enums.parse_mode import ParseMode
from asyncio import sleep
from urllib.parse import quote



db = Database()


@StreamBot.on_message(filters.command('start') & filters.private)
async def start(bot: Client, message: Message):
    if "file_" in message.text:
        try:
            usr_cmd = message.text.split("_")[-1]
            data = usr_cmd.split("-")
            message_id, chat_id = data[0], f"-{data[1]}"
            file = await bot.get_messages(int(chat_id), int(message_id))
            media = is_media(file)
            await message.reply_cached_media(file_id=media.file_id, caption=f'**{media.file_name}**')
        except Exception as e:
            print(f"An error occurred: {e}", flush=True)


@StreamBot.on_message(filters.command('index'))
async def start(bot: Client, message: Message):
    channel_id = message.chat.id
    AUTH_CHANNEL = await db.get_variable('auth_channel')
    if AUTH_CHANNEL is None or AUTH_CHANNEL.strip() == '':
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
                "📁 You don't need to index again unless you make changes to the database."
            )

            await bot.send_message(chat_id=message.chat.id, text=done_message)
        except FloodWait as e:
            LOGGER.info(f"Sleeping for {str(e.value)}s")
            await sleep(e.value)
            await message.reply(text=f"Got Floodwait of {str(e.value)}s",
                                disable_web_page_preview=True, parse_mode=ParseMode.MARKDOWN)
    else:
        await message.reply(text="Channel is not in AUTH_CHANNEL")


@StreamBot.on_message(
    filters.channel
    & (
        filters.document
        | filters.video
    )
)


async def file_receive_handler2(bot: Client, message: Message):
    channel_id = message.chat.id
    AUTH_CHANNEL = await db.get_variable('auth_channel')
    if AUTH_CHANNEL is None or AUTH_CHANNEL.strip() == '':
        AUTH_CHANNEL = Telegram.AUTH_CHANNEL
    else:
        AUTH_CHANNEL = [channel.strip() for channel in AUTH_CHANNEL.split(",")]
    if str(channel_id) in AUTH_CHANNEL:
        try:
            file = message.video or message.document
            #title = file.file_name or message.caption or file.file_id
            #title, _ = splitext(title)
            #title = re.sub(r'[.,|_\',]', ' ', title)
            title = None
            # Priority 1: Caption (often has the best description)
            print(f"Caption:{message.caption}", flush=True)
            if message.caption and message.caption.strip():
                title = message.caption.strip()
                print(f"Title: {title}", flush=True)

            # Priority 2: File name
            print(f"File Name: {file.file_name}", flush=True)
            if not title and file.file_name:
                title = file.file_name
                title = title[:30]
                print(f"Title: {title}", flush=True)

                # Remove file extension
                title, _ = splitext(title)

            # Priority 3: Message text (if any)
            if not title and message.text:
                title = message.text.strip()

            # Priority 4: First line of caption/text if it's multi-line
            if not title and message.caption:
                title = message.caption.split('\n')[0].strip()

            # Priority 5: Use file_id with size as fallback
            if not title or title == "":
                size = get_readable_file_size(file.file_size)
                title = f"Video {msg_id} ({size})"

            # Clean the title - preserve Hebrew and Unicode
            # Only remove problematic characters
            title = re.sub(r'[|_-]', ' ', title)  # Keep dots, commas, quotes - only remove pipes and underscores
            title = re.sub(r'\s+', ' ', title)  # Replace multiple spaces with single space
            title = title.strip()

            # Limit title length to reasonable size (optional)
            if len(title) > 30:
                title = title[:30] 
                
            full_desc = message.caption.strip() if message.caption else "No description available."
            msg_id = message.id
            hash = file.file_unique_id[:6]
            size = get_readable_file_size(file.file_size)
            type = file.mime_type
            print(f"File title before saved from start.py: {title}", flush=True)
            await db.add_tgfiles(str(channel_id), msg_id, str(hash), str(title), str(full_desc), str(size), str(type))
        except FloodWait as e:
            LOGGER.info(f"Sleeping for {str(e.value)}s")
            await sleep(e.value)
            await message.reply(text=f"Got Floodwait of {str(e.value)}s",
                                disable_web_page_preview=True, parse_mode=ParseMode.MARKDOWN)
    else:
        await message.reply(text="Channel is not in AUTH_CHANNEL")


# ... inside file_receive_handler ...

async def file_receive_handler(bot: Client, message: Message):
    channel_id = message.chat.id
    # ... (keep your AUTH_CHANNEL check code) ...
    if str(channel_id):
        try:
            file = message.video or message.document
            msg_id = message.id
            
            # --- IMPROVED TITLE LOGIC ---
            raw_title = message.caption or file.file_name or f"Video {msg_id}"
            raw_fn = file.file_name
            is_default = any(x in raw_fn.lower() for x in ["default_name", "default name", "undefined"])

            if not is_default and raw_fn:
                        # Priority A: Original File Name (if it's not generic)
                title, _ = splitext(raw_fn)
                title = clean_hebrew_title(title)
                if len(title) > 33:
                    title = title[:33]
                    if " " in title:
                        title = title.rsplit(' ', 1)[0]
                print(f"Using File Name: {title}", flush=True)
            elif message.caption:
                # Priority B: Hebrew Caption (limited to 30 chars)
                clean_caption = message.caption.strip().split('\n')[0] # Take first line only
                clean_caption = clean_hebrew_title(clean_caption)
                if len(clean_caption) > 33:
                    clean_caption = clean_caption[:33]
                    if " " in clean_caption:
                        title = clean_caption.rsplit(' ', 1)[0]
                        print(f"Using Caption Snippet: {title}", flush=True)
                        # 2. Last resort fallback if both above failed
            if not title:
                title = f"Video {message.id}"
            # Clean title for DB
            #title = raw_title.split('\n')[0].strip()
            title = re.sub(r'[|_-]', ' ', title)
            title = re.sub(r'\s+', ' ', title).strip()
            title = clean_hebrew_title(title)
            if len(title) > 50: # Increased from 30 to help TMDB find specific shows
                title = title[:50]

            # --- NEW POSTER LOGIC ---
            has_real_thumb = hasattr(file, 'thumbs') and file.thumbs
            
            if has_real_thumb:
                # Use the local proxy URL for real Telegram thumbs
                poster_url = f"/api/thumb/{str(channel_id).replace('-100', '')}?id={msg_id}"
            else:
                # Fallback to Hebrew Placeholder
                clean_t = quote(title)
                poster_url = f"https://placehold.jp/40/1a1a2e/ffffff/600x900.png?text={clean_t}"

            full_desc = message.caption.strip() if message.caption else "No description available."
            hash = file.file_unique_id[:6]
            size = get_readable_file_size(file.file_size)
            mime_type = file.mime_type

            # --- CRITICAL: Update your Database call ---
            # Ensure your db.add_tgfiles function accepts the poster_url!
            # If your database helper doesn't support the extra argument, 
            # you might need to update bot/helper/database.py as well.
            await db.add_tgfiles(
                str(channel_id), 
                msg_id, 
                str(hash), 
                str(title), 
                str(full_desc), 
                str(size), 
                str(mime_type),
                img=poster_url # <--- ADD THIS
            )
            
            print(f"Saved {title} with poster: {poster_url}", flush=True)

        except Exception as e:
            print(f"Error in file_receive_handler: {e}", flush=True)