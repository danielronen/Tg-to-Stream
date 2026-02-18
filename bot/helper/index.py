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
        return None, None
    if title in _TMDB_CACHE:
        return _TMDB_CACHE[title]
    try:
        search_url = "https://api.themoviedb.org/3/search/multi"
        params = {
            "api_key": tmdb_api_key,
            "query": title,
            "language": "he-IL",
            "include_adult": "true"
        }
        response = httpx.get(search_url, params=params, timeout=10.0)
        response.raise_for_status()
        data = response.json()

        if data.get("results") and len(data["results"]) > 0:
            result = data["results"][0]
            poster_path = result.get("poster_path")
            backdrop_path = result.get("backdrop_path")
            if poster_path:
                # TMDB poster base URL (w500 is a good size for Stremio)
                poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}"
                if backdrop_path:
                    background_url = f"https://image.tmdb.org/t/p/w1280{backdrop_path}"
                # Cache the result
                    _TMDB_CACHE[title] = poster_url, background_url
                    return poster_url, background_url
                else:# In case there is only a poster, use poster as a background
                    background_url = f"https://image.tmdb.org/t/p/w1280{poster_path}"
                    _TMDB_CACHE[title] = poster_url, background_url
                    return poster_url, background_url
            else:
                _TMDB_CACHE[title] = None, None
                return None, None
        else:
            _TMDB_CACHE[title] = None, None
            return None, None
            
    except httpx.TimeoutException:
        print(f"⏱️ TMDB request timeout for '{title}'")
        return None, None
    except httpx.HTTPError as e:
        print(f"❌ TMDB HTTP error for '{title}': {e}")
        return None, None
    except Exception as e:
        print(f"❌ Error fetching TMDB poster for '{title}': {e}")
        return None, None

def get_tmdb_details(title, tmdb_api_key):
    if not tmdb_api_key:
        print("No TMDB api key")
        return None
    if title in _TMDB_CACHE:
        print("sent from cache")
        return _TMDB_CACHE[title]
    try:
        search_url = "https://api.themoviedb.org/3/search/multi"
        params = {
            "api_key": tmdb_api_key,
            "query": title,
            "language": "he-IL",
            "include_adult": "true"
        }
        response = httpx.get(search_url, params=params, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        if data.get("results") and len(data["results"]) > 0:
            for res in data["results"]:
                if res.get("origin_country"):
                    if res.get("origin_country")[0] == "IL":
                        result = res
                        _TMDB_CACHE[title] = result
                        return result
            result = data["results"][0]
            _TMDB_CACHE[title] = result
            return result
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
    
async def fetch_message(chat_id, message_id):
    try:
        message = await StreamBot.get_messages(chat_id, message_id)
        return message
    except Exception as e:
        return None

def clean_hebrew_title(text):
    if not text:
        return ""

    # 0. High Priority Split (Isolate title from description/details)
    # Added ':' for Hebrew captions that use 'Title: Description'
    text = re.split(r"[|•,/\[\(:/]", text)[0].strip()

    # 1. Clean Delimiters & Telegram emojis
    text = re.sub(r"[._\-\[\]\(\)↙️👥]", " ", text)
    
    # 2. Preserve only Hebrew, English, and Numbers
    text = re.sub(r"[^א-תa-zA-Z0-9\s]", " ", text)

    # 3. Technical Tags & File Extensions
    tech_pattern = r"\b(4K|2160p|1080p|720p|480p|DVD|DVDRip|BluRay|BRRip|WEB-?DL|HDTV|WEB|h\s?264|x265|x26?4|HEVC|ENG|HB|DL|Rw|heb|https?|www|com|net|org|mp4|mkv|avi|x264)\b"
    text = re.sub(tech_pattern, "", text, flags=re.IGNORECASE)

    # 4. Dates & Years
    text = re.sub(r"\b\d{2,4}\s\d{2}\s\d{2}\b", "", text) # 26 01 2026
    text = re.sub(r"\b(19|20)\d{2}\b", "", text)         # 2024

    # 5. Group Names (Specific to your examples)
    groups = [
        r"זירה מדיה", r"ז\.מ", r"דב סרטים", r"שלמה סרטים", 
        r"ע י", r"הועלה", r"לולו סרטים", r"שבי גוזלן", 
        r"למבורגיני", r"גוזלן", r"נתי מדיה", r"איכות ערוץ", 
        r"צפייה ישירה", r"איכות", r"ערוץ", r"Yonidan",r"מתורגם", 
        r"Premiumcontentil", r"ISrTeLeG",
    ]
    for group in groups:
        text = re.sub(group, "", text, flags=re.IGNORECASE)

    # 6. Season/Episode (Handles 'ע1', 'עונה 1', 'S01E01')
    text = re.sub(r"\bע(ונה)?\s?\d*\b", " ", text)
    text = re.sub(r"\bפ(רק)?\s?\d*\b", " ", text)
    text = re.sub(r"s\d{1,2}e\d{1,2}", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b[עפ]\d+\b", " ", text)

    # 7. Final Polish
    text = re.sub(r"\b[a-zA-Z]\b", " ", text) # Remove single English letters
    text = re.sub(r"\s+", " ", text).strip()
    
    return text

async def get_messages(chat_id, first_message_id, last_message_id, batch_size=50):
    messages = []
    
    # Get all existing msg_ids for this chat in ONE query
    existing_msg_ids = set(
        doc['msg_id'] 
        for doc in db.files.find(
            {"chat_id": str(chat_id)}, 
            {"msg_id": 1, "_id": 0}
        )
    )
    print(f"📊 Found {len(existing_msg_ids)} existing messages in database")
    
    # Calculate which message IDs we need to fetch
    all_msg_ids = set(range(first_message_id, last_message_id + 1))
    missing_msg_ids = sorted(all_msg_ids - existing_msg_ids)
    
    print(f"🔍 Need to fetch {len(missing_msg_ids)} missing message IDs (skipping {len(existing_msg_ids)} duplicates)")
    
    non_video_count = 0
    current_idx = 0
    
    # Process only missing messages in batches
    while current_idx < len(missing_msg_ids):
        batch_msg_ids = missing_msg_ids[current_idx:current_idx + batch_size]
        
        tasks = [fetch_message(chat_id, msg_id) for msg_id in batch_msg_ids]
        batch_messages = await gather(*tasks)
        
        for message in batch_messages:
            if message:
                if file := message.video or message.document:
                    if message.caption:
                        # Priority A: Hebrew Caption
                        clean_caption = message.caption.strip().split("\n")[0]
                        clean_caption = clean_hebrew_title(clean_caption)
                        if len(clean_caption) > 33:
                            title = clean_caption[:33].rsplit(" ", 1)[0]
                        else: 
                            title = clean_caption
                    else:
                        
                        raw_fn = file.file_name or ""
                        title, _ = splitext(raw_fn)
                        is_default = any(
                            x in title.lower()
                            for x in ["default_name", "default name", "undefined", "index"]
                        )
                        
                        if not is_default and title:
                            # Priority B: Original File Name (if it's not generic)
                            title = clean_hebrew_title(title)
                            if len(title) > 33:
                                title = title[:33].rsplit(" ", 1)[0]
                    if not title:
                        title = f"Video {message.id}"
                        
                        
                    if message.caption:
                        full_desc = str(message.caption)
                    else:
                        full_desc = str(raw_fn)
                    
                    # POSTER LOGIC
                    tmdb_id = None
                    tmdb_res = get_tmdb_details(title, TMDB_API_KEY)
                    if tmdb_res:
                        poster_path = tmdb_res.get("poster_path")
                        background_path = tmdb_res.get("backdrop_path")
                        tmdb_id = tmdb_res.get("id")
                        full_desc = tmdb_res.get("overview")
                        poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}"
                        background_url = f"https://image.tmdb.org/t/p/w1280{background_path}"
                                                
                    #poster_url, background_url = get_tmdb_poster(title, TMDB_API_KEY)
                    if poster_url == None:
                        has_real_thumb = hasattr(file, "thumbs") and file.thumbs
                        if has_real_thumb:
                            poster_url = f"{SURF_TG_BASE_URL}/api/thumb/{chat_id}?id={message.id}"
                            background_url = f"{SURF_TG_BASE_URL}/api/thumb/{chat_id}?id={message.id}"
                        else:
                            clean_t = quote(title)
                            poster_url = f"https://placehold.jp/40/1a1a2e/ffffff/600x900.png?text={clean_t}"
                            background_url = f"https://placehold.jp/40/1a1a2e/ffffff/600x900.png?text={clean_t}"

                    messages.append(
                        {
                            "msg_id": message.id,
                            "title": title,
                            "description": full_desc,
                            "hash": file.file_unique_id,
                            "size": get_readable_file_size(file.file_size),
                            "type": file.mime_type,
                            "chat_id": str(chat_id),
                            "img": poster_url,
                            "background": background_url
                        }
                    )
                else:
                    non_video_count += 1
        
        current_idx += batch_size
    
    print(f"✅ Processed {len(messages)} new video files")
    print(f"⏭️  Skipped {len(existing_msg_ids)} already indexed")
    print(f"📝 Skipped {non_video_count} non-video messages")
    
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
                "hash": file.file_unique_id,
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
            hash=post["hash"][:6],
            size=post["size"],
            type=post["type"],
        )
        for post in posts
    )
