from os.path import splitext
import re
import os
import httpx
from urllib.parse import quote
from bot.config import Telegram
from bot.helper.database import Database
from bot.telegram import StreamBot, UserBot
from bot.helper.file_size import get_readable_file_size
from bot.helper.cache import get_cache, save_cache
from asyncio import gather
from dotenv import load_dotenv

load_dotenv("config.env")
SURF_TG_BASE_URL = os.getenv("BASE_URL", "")
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
_TMDB_CACHE = {}


db = Database()


def get_tmdb_poster(title, tmdb_api_key):
    if not tmdb_api_key:
        return None
    if title in _TMDB_CACHE:
        return _TMDB_CACHE[title]
    try:
        search_url = "https://api.themoviedb.org/3/search/multi"
        params = {
            "api_key": tmdb_api_key,
            "query": title,
            "language": "he-IL",
            "include_adult": "false"
        }
        response = httpx.get(search_url, params=params, timeout=10.0)
        response.raise_for_status()
        data = response.json()

        if data.get("results") and len(data["results"]) > 0:
            result = data["results"][0]
            poster_path = result.get("poster_path")
            if poster_path:
                # TMDB poster base URL (w500 is a good size for Stremio)
                poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}"
                # Cache the result
                _TMDB_CACHE[title] = poster_url
                return poster_url
            else:
                _TMDB_CACHE[title] = None
                return None
        else:
            _TMDB_CACHE[title] = None
            return None
            
    except httpx.TimeoutException:
        print(f"⏱️ TMDB request timeout for '{title}'")
        return None
    except httpx.HTTPError as e:
        print(f"❌ TMDB HTTP error for '{title}': {e}")
        return None
    except Exception as e:
        print(f"❌ Error fetching TMDB poster for '{title}': {e}")
        return None

def check_thumbnail_exists(thumbnail_url, timeout=2.0):
    """
    Quickly check if a Telegram thumbnail URL is accessible.
    Returns True if accessible, False otherwise.
    """
    print(f"checks tumbnail url: {thumbnail_url}")
    try:
        # Make a HEAD request (faster than GET, just checks if URL exists)
        response = httpx.head(thumbnail_url, timeout=timeout, follow_redirects=True)
        # Check if successful and not an error page
        return response.status_code == 200
    except:
        return False

async def fetch_message(chat_id, message_id):
    try:
        message = await StreamBot.get_messages(chat_id, message_id)
        return message
    except Exception as e:
        return None

def clean_hebrew_title(text):
    if not text:
        return ""
    # 1. Remove all non-Hebrew/non-Alphanumeric symbols (dots, underscores, brackets)
    # This leaves Hebrew characters, English letters, and numbers
    text = re.sub(r"[._\-\[\]\(\)]", " ", text)
    text = re.sub(r"[^א-תa-zA-Z0-9\s]", " ", text)
    # 2. Remove common English technical tags (Removed \b to be more aggressive)
    text = re.sub(
        r"(4K|2160p|1080p|720p|480p|430p|BluRay|BRRip|WEB-?DL|HDTV|WEB|h\s?264|x265|x26?4|h26?4|ENG|HB|HEVC|DL|Rw|heb|ח\s?\d+)",
        "",
        text,
        flags=re.IGNORECASE,
    )
    # 3. Remove Dates (Handles 23 01 26 or 2023 01 26)
    # This looks for sequences of 2-4 digits separated by spaces
    text = re.sub(r"\b\d{2,4}\s\d{2}\s\d{2}\b", "", text)
    text = re.sub(r"\b(19|20)\d{2}\b", "", text)
    # 3. Remove Hebrew Uploader/Group tags
    # Added common variations like brackets
    groups = [
        r"זירה מדיה",
        r"ז\.מ",
        r"דב סרטים",
        r"שלמה סרטים",
        r"ע י",
        r"ת מ",
        r"הועלה",
        r"לולו סרטים",
        r"שבי גוזלן",
        r"למבורגיני",
        r"גוזלן",
        r"נתי מדיה",
        r"איכות ערוץ",
        r"צפייה ישירה",
        r"איכות"
    
    ]
    for group in groups:
        text = re.sub(group, "", text)
    # 4. Remove Season/Episode info
    text = re.sub(r"\bע(ונה)?\s?\d*\b", "", text)
    text = re.sub(r"\bפ(רק)?\s?\d*\b", "", text)
    text = re.sub(r"s\d{1,2}e\d{1,2}", "", text, flags=re.IGNORECASE)
    text = re.sub(r"ע\d+", " ", text)
    text = re.sub(r"פ\d+", " ", text)
    # 5. Clean up quotes and extra spaces
    text = text.replace('"', "").replace("'", "").strip()
    text = re.sub(r"\s+", " ", text)
    
    return text


async def get_messages(chat_id, first_message_id, last_message_id, batch_size=50):
    messages = []
    current_message_id = first_message_id
    while current_message_id <= last_message_id:
        batch_message_ids = list(
            range(
                current_message_id,
                min(current_message_id + batch_size, last_message_id + 1),
            )
        )
        tasks = [fetch_message(chat_id, message_id) for message_id in batch_message_ids]
        batch_messages = await gather(*tasks)
        for message in batch_messages:
            if message:
                title = None
                if file := message.video or message.document:
                    raw_fn = file.file_name or ""
                    title, _ = splitext(raw_fn)
                    is_default = any(
                        x in title.lower()
                        for x in ["default_name", "default name", "undefined", "index"]
                    )
                    if not is_default and title:
                        # Priority A: Original File Name (if it's not generic)
                        title = clean_hebrew_title(title)
                        if len(title) > 33:
                            title = title[:33]
                            if " " in title:
                                title = title.rsplit(" ", 1)[0]
                        title = clean_hebrew_title(title)
                    elif message.caption:
                        # Priority B: Hebrew Caption (limited to 30 chars)
                        clean_caption = message.caption.strip().split("\n")[
                            0
                        ]  # Take first line only
                        clean_caption = clean_hebrew_title(clean_caption)
                        if len(clean_caption) > 33:
                            clean_caption = clean_caption[:33]
                            if " " in clean_caption:
                                title = clean_caption.rsplit(" ", 1)[0]
                                # 2. Last resort fallback if both above failed
                            else:
                                title = clean_caption
                        else: 
                            title = clean_caption
                    if not title:
                        title = f"Video {message.id}"
                    if message.caption:
                        full_desc = str(message.caption)
                    else:
                        full_desc = str(raw_fn)
                        
                    # ------------------ POSTER LOGIC -------------------------------
                    
                    if message.video:
                        has_real_thumb = hasattr(file, "thumbs") and file.thumbs
                        if has_real_thumb:
                            poster_url = f"{SURF_TG_BASE_URL}/api/thumb/{chat_id}?id={message.id}"
                        else:
                            poster_url = get_tmdb_poster(title,TMDB_API_KEY)
                    elif message.document:
                        poster_url = get_tmdb_poster(title,TMDB_API_KEY)
                    if not poster_url:
                        # Fallback to Hebrew Placeholder
                        clean_t = quote(title)
                        poster_url = f"https://placehold.jp/40/1a1a2e/ffffff/600x900.png?text={clean_t}"

                    messages.append(
                        {
                            "msg_id": message.id,
                            "title": title,
                            "description": full_desc,
                            "hash": file.file_unique_id[:6],
                            "size": get_readable_file_size(file.file_size),
                            "type": file.mime_type,
                            "chat_id": str(chat_id),
                            "img": poster_url,
                        }
                    )

        current_message_id += batch_size
    return messages


async def get_files(chat_id, page=1):
    if Telegram.SESSION_STRING == "":
        return await db.list_tgfiles(id=chat_id, page=page)
    if cache := get_cache(chat_id, int(page)):
        return cache
    posts = []
    async for post in UserBot.get_chat_history(
        chat_id=int(chat_id), limit=50, offset=(int(page) - 1) * 50
    ):
        file = post.video or post.document
        if not file:
            continue
        raw_fn = file.file_name or ""
        is_default = any(
            x in raw_fn.lower() for x in ["default_name", "default name", "undefined"]
        )
        title = None
        if not is_default and raw_fn:
            # Priority A: Original File Name (if it's not generic)
            title, _ = splitext(raw_fn)
            title = clean_hebrew_title(title)
            if len(title) > 33:
                title = title[:33]
                if " " in title:
                    title = title.rsplit(" ", 1)[0]
        elif post.caption:
            # Priority B: Hebrew Caption (limited to 30 chars)
            clean_caption = post.caption.strip().split("\n")[0]  # Take first line only
            if len(clean_caption) > 33:
                clean_caption = clean_hebrew_title(clean_caption)
                title = clean_caption[:33].rsplit(" ", 1)[0]
            else:
                clean_caption = clean_hebrew_title(clean_caption)
                title = clean_caption
        # 2. Last resort fallback if both above failed
        if not title:
            title = f"Video {post.id}"
        # 4. Clean symbols but preserve Hebrew characters
        title = re.sub(r"[|_*`]", " ", title)
        title = re.sub(r"\s+", " ", title).strip()
        title = clean_hebrew_title(title)
        full_desc = str(post.caption) if post.caption else ""
        has_real_thumb = hasattr(post, "thumbs") and post.thumbs
        if has_real_thumb:
            # Use the local proxy URL for real Telegram thumbs
            poster_url = f"/api/thumb/{str(chat_id).replace('-100', '')}?id={post.id}"
        else:
            # Fallback to Hebrew Placeholder
            clean_t = quote(title)
            poster_url = (
                f"https://placehold.jp/40/1a1a2e/ffffff/600x900.png?text={clean_t}"
            )
        posts.append(
            {
                "msg_id": post.id,
                "chat_id": str(chat_id),
                "title": title,
                "description": full_desc,
                "hash": file.file_unique_id[:6],
                "size": get_readable_file_size(file.file_size),
                "type": file.mime_type,
                "img": poster_url,
            }
        )
    save_cache(chat_id, {"posts": posts}, page)
    return posts


async def posts_file(posts, chat_id):
    phtml = """
            <div class="col">
                
                    <div class="card text-white bg-primary mb-3">
                        <input type="checkbox" class="admin-only form-check-input position-absolute top-0 end-0 m-2"
                            onchange="checkSendButton()" id="selectCheckbox"
                            data-id="{id}|{hash}|{title}|{size}|{type}|{img}">
                        <img src="https://cdn.jsdelivr.net/gh/weebzone/weebzone/data/Surf-TG/src/loading.gif" class="lzy_img card-img-top rounded-top"
                            data-src="{img}" alt="{title}">
                        <a href="/watch/{chat_id}?id={id}&hash={hash}">
                        <div class="card-body p-1">
                            <h6 class="card-title">{title}</h6>
                            <span class="badge bg-warning">{type}</span>
                            <span class="badge bg-info">{size}</span>
                        </div>
                        </a>
                    </div>
                
            </div>
        """

    return "".join(
        phtml.format(
            chat_id=str(chat_id).replace("-100", ""),
            id=post["msg_id"],
            img=f"/api/thumb/{chat_id}?id={post['msg_id']}",
            title=post["title"],
            hash=post["hash"],
            size=post["size"],
            type=post["type"],
        )
        for post in posts
    )
