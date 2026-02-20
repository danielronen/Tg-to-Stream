from datetime import datetime
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
import asyncio
from dotenv import load_dotenv

load_dotenv("config.env")
SURF_TG_BASE_URL = os.getenv("BASE_URL", "")
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
_TMDB_CACHE = {}
_IN_FLIGHT = {}

tmdb_client = httpx.AsyncClient(
    timeout=httpx.Timeout(10.0, connect=5.0),
    limits=httpx.Limits(max_keepalive_connections=10, max_connections=20)
)
tmdb_sem = asyncio.Semaphore(20)

db = Database()

def extract_season_episode(description):
    if not description:
        return None, None    
    
    # 1. Hebrew format "עונה X פרק Y" (Improved to allow symbols like , - . |)
    # We use [\s,.\-|]* to allow any combination of spaces and punctuation
    hebrew_pattern = r'עונה\s*(\d+)[\s,.\-|]*פרק\s*(\d+)'
    match = re.search(hebrew_pattern, description)
    if match:
        return int(match.group(1)), int(match.group(2))    
    
    # 2. Hebrew abbreviated "ע2 פ3" or "ע2, פ3"
    hebrew_abbrev_pattern = r'ע(\d+)[\s,.\-|]*פ(\d+)'
    match = re.search(hebrew_abbrev_pattern, description)
    if match:
        return int(match.group(1)), int(match.group(2))    
    
    # 3. English "Season X Episode Y"
    english_pattern = r'[Ss]eason\s*(\d+)[\s,.\-|]*[Ee]pisode\s*(\d+)'
    match = re.search(english_pattern, description)
    if match:
        return int(match.group(1)), int(match.group(2))    
    
    # 4. Short "S01E02"
    short_pattern = r'[Ss](\d+)[Ee](\d+)'
    match = re.search(short_pattern, description)
    if match:
        return int(match.group(1)), int(match.group(2))    
    
    # --- Fallbacks for Episode Only ---
    
    hebrew_episode_only = r'פרק\s*(\d+)'
    match = re.search(hebrew_episode_only, description)
    if match:
        return 1, int(match.group(1))
    
    hebrew_very_short_episode = r'\bפ(\d+)\b' # Added \b to avoid matching middle of words
    match = re.search(hebrew_very_short_episode, description)
    if match:
        return 1, int(match.group(1))
    
    english_episode_only = r'(?:Episode|Ep\.?|EP)\s*(\d+)'
    match = re.search(english_episode_only, description, re.IGNORECASE)
    if match:
        return 1, int(match.group(1))
    
    return None, None

async def get_tmdb_details(title, description, tmdb_api_key, client: httpx.AsyncClient):
    """
    High-performance async TMDB search with context-aware matching and cache stampede protection.
    """
    if not tmdb_api_key:
        return None

    if title in _TMDB_CACHE:
        print(f"✅ Sent from cache: {title}")
        return _TMDB_CACHE[title]

    # --- NEW: CACHE STAMPEDE PROTECTION ---
    if title in _IN_FLIGHT:
        print(f"⏳ Waiting for in-flight request for: {title}")
        # If another task is already fetching this, just wait for it to finish
        await _IN_FLIGHT[title].wait()
        # Once it finishes, the result will be in the cache!
        return _TMDB_CACHE.get(title)

    # If we are the first one, lock this title by creating an Event
    lock_event = asyncio.Event()
    _IN_FLIGHT[title] = lock_event
    # ---------------------------------------

    try:
        search_url = "https://api.themoviedb.org/3/search/multi"
        params = {
            "api_key": tmdb_api_key,
            "query": title,
            "language": "he-IL",
            "include_adult": "true"
        }
        
        response = await client.get(search_url, params=params, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        results = data.get("results", [])

        if not results:
            _TMDB_CACHE[title] = None
            return None

        # --- SMART SCORING LOGIC ---
        best_match = None
        highest_score = -1

        clean_query = title.lower().strip()
        year_match = re.search(r'\b(19|20)\d{2}\b', f"{title} {description}")
        target_year = year_match.group(0) if year_match else None

        for res in results:
            score = 0
            res_title = (res.get("title") or res.get("name") or "").lower()
            res_original = (res.get("original_title") or res.get("original_name") or "").lower()
            
            if clean_query == res_title or clean_query == res_original:
                score += 15
            elif clean_query in res_title:
                score += 5

            is_israeli = ("IL" in res.get("origin_country", []) or res.get("original_language") == "he")
            if is_israeli:
                score += 10

            res_date = res.get("release_date") or res.get("first_air_date") or ""
            if target_year and target_year in res_date:
                score += 10

            res_overview = (res.get("overview") or "").lower()
            if description and len(description) > 5:
                desc_words = [w for w in description.lower().split() if len(w) > 3]
                matches = sum(1 for word in desc_words if word in res_overview)
                score += min(matches, 5) 

            if score > highest_score:
                highest_score = score
                best_match = res

        # Save to Cache
        _TMDB_CACHE[title] = best_match
        return best_match

    except Exception as e:
        print(f"❌ TMDB Error for '{title}': {e}")
        return None
        
    finally:
        # --- NEW: CLEANUP ---
        # No matter what happens (success or error), tell the waiting tasks we are done!
        if title in _IN_FLIGHT:
            _IN_FLIGHT[title].set()  # Unblocks the waiting tasks
            del _IN_FLIGHT[title]    # Remove from in-flight tracker

async def get_tmdb_ep_det(tmdb_id, season, ep, tmdb_api_key, client: httpx.AsyncClient):
    ep_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{ep}"
    params = {"api_key": tmdb_api_key, "language": "he-IL"}
    
    try:
        # Use the passed-in async client
        response = await client.get(ep_url, params=params, timeout=5.0)
        if response.status_code == 200:
            data = response.json()
            raw_name = data.get("name", "")
            episode_name = None
            if raw_name and not any(raw_name.startswith(x) for x in ["פרק", "Episode"]):
                episode_name = raw_name
            # 2. Overview Logic
            overview = data.get("overview") if data.get("overview") else None           
            # 3. Thumbnail (Still Path) Logic
            # Episodes use 'still_path', which is usually a 16:9 image
            still_path = data.get("still_path")
            thumbnail_url = f"https://image.tmdb.org/t/p/w1280{still_path}" if still_path else None        
            air_date = None
            air_date = data.get("air_date")
            
            return episode_name, overview, thumbnail_url, air_date
            
    except Exception as e:
        print(f"⚠️ Error fetching episode details: {e}")
        
    return None, None, None, None

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
    tech_pattern = r"\b(4K|2160p|1080|1080p|720p|480p|DVD|DVDRip|BluRay|BRRip|WEB-?DL|WeDdl|HDTV|WEB|h\s?264|x265|x26?4|HEVC|ENG|HB|DL|Rw|heb|https?|www|com|net|org|mp4|mkv|avi|x264)\b"
    text = re.sub(tech_pattern, "", text, flags=re.IGNORECASE)

    # 4. Dates & Years
    text = re.sub(r"\b\d{2,4}\s\d{2}\s\d{2}\b", "", text) # 26 01 2026
    text = re.sub(r"\b(19|20)\d{2}\b", "", text)         # 2024

    # 5. Group Names (Specific to your examples)
    groups = [
        r"זירה מדיה", r"ז\.מ", r"דב סרטים", r"שלמה סרטים", 
        r"ע י", r"הועלה", r"לולו סרטים", r"שבי גוזלן", 
        r"למבורגיני", r"גוזלן", r"נתי מדיה", r"איכות ערוץ", 
        r"צפייה ישירה", r"איכות", r"ערוץ", r"Yonidan",r"מתורגם", r"תיקון סנכרון", r"תרגום מובנה", r"מדובב", r"מקורי",
        r"Premiumcontentil", r"ISrTeLeG", r"אחרון לעונה",
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

async def get_messages1(chat_id, first_message_id, last_message_id, batch_size=50):
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
    
    # ✨ NEW: Collect all messages first (without TMDB calls)
    raw_messages = []
    
    # Process only missing messages in batches
    while current_idx < len(missing_msg_ids):
        batch_msg_ids = missing_msg_ids[current_idx:current_idx + batch_size]
        
        tasks = [fetch_message(chat_id, msg_id) for msg_id in batch_msg_ids]
        batch_messages = await gather(*tasks)
        
        for message in batch_messages:
            if message:
                if file := message.video or message.document:
                    # Extract basic info (no TMDB calls yet)
                    if message.caption:
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
                            title = clean_hebrew_title(title)
                            if len(title) > 33:
                                title = title[:33].rsplit(" ", 1)[0]
                    
                    if not title:
                        title = f"Video {message.id}"
                    
                    if message.caption:
                        raw_desc = str(message.caption)
                        full_desc = str(message.caption)
                    else:
                        raw_desc = str(file.file_name or "")
                        full_desc = str(file.file_name or "")
                    
                    # Store raw message data (no TMDB yet)
                    raw_messages.append({
                        'message': message,
                        'file': file,
                        'title': title,
                        'raw_desc': raw_desc,
                        'full_desc': full_desc
                    })
                else:
                    non_video_count += 1
        
        current_idx += batch_size
    
    print(f"📦 Collected {len(raw_messages)} videos, now fetching TMDB data in parallel...")
    
    # ✨ NEW: Batch TMDB calls - ALL AT ONCE
    tmdb_tasks = []
    for raw_msg in raw_messages:
        task = get_tmdb_details(
            raw_msg['title'], 
            raw_msg['full_desc'], 
            TMDB_API_KEY, 
            tmdb_client
        )
        tmdb_tasks.append(task)
    
    # Execute ALL TMDB calls in parallel
    tmdb_results = await gather(*tmdb_tasks, return_exceptions=True)
    
    print(f"✅ Fetched {len(tmdb_results)} TMDB results")
    
    # ✨ NEW: Now process TMDB episode details in parallel
    episode_tasks = []
    episode_indices = []
    
    for idx, (raw_msg, tmdb_res) in enumerate(zip(raw_messages, tmdb_results)):
        # Handle errors
        if isinstance(tmdb_res, Exception):
            tmdb_res = None
        
        se, ep = extract_season_episode(raw_msg['full_desc'])
        
        if tmdb_res and (ep or se) and tmdb_res.get("media_type") == "tv":
            tmdb_id = tmdb_res.get("id")
            task = get_tmdb_ep_det(tmdb_id, se, ep, TMDB_API_KEY, tmdb_client)
            episode_tasks.append(task)
            episode_indices.append(idx)
    
    # Execute ALL episode calls in parallel
    if episode_tasks:
        print(f"🎬 Fetching {len(episode_tasks)} episode details in parallel...")
        episode_results = await gather(*episode_tasks, return_exceptions=True)
    else:
        episode_results = []
    
    # ✨ NEW: Build final messages with all data
    print(f"🔨 Building final message objects...")
    
    # Map episode results back to their messages
    episode_data_map = {}
    for idx, ep_idx in enumerate(episode_indices):
        if idx < len(episode_results):
            episode_data_map[ep_idx] = episode_results[idx]
    
    for idx, (raw_msg, tmdb_res) in enumerate(zip(raw_messages, tmdb_results)):
        message = raw_msg['message']
        file = raw_msg['file']
        title = raw_msg['title']
        raw_desc = raw_msg['raw_desc']
        full_desc = raw_msg['full_desc']
        
        # Handle TMDB errors
        if isinstance(tmdb_res, Exception):
            tmdb_res = None
        
        se, ep = extract_season_episode(full_desc)
        episode_name = None
        ep_name = None
        ep_overview = None
        thumb_url = None
        poster_url = None
        background_url = None
        released = None
        
        # Process TMDB main data
        if tmdb_res:
            tmdb_id = tmdb_res.get("id")
            media_type = tmdb_res.get("media_type")
            p_path = tmdb_res.get("poster_path")
            b_path = tmdb_res.get("backdrop_path")
            overv = tmdb_res.get("overview")
            
            if p_path and b_path:
                background_url = f"https://image.tmdb.org/t/p/w1280{b_path}"
                poster_url = f"https://image.tmdb.org/t/p/w500{p_path}"
            elif p_path:
                background_url = f"https://image.tmdb.org/t/p/w1280{p_path}"
                poster_url = f"https://image.tmdb.org/t/p/w500{p_path}"
            
            if overv:
                full_desc = overv
            
            if tmdb_res.get("release_date"):
                released = tmdb_res.get("release_date")
            if tmdb_res.get("first_air_date"):
                released = tmdb_res.get("first_air_date")
            
            # Get episode data if it was fetched
            if idx in episode_data_map:
                ep_result = episode_data_map[idx]
                
                # Handle errors
                if not isinstance(ep_result, Exception) and ep_result:
                    ep_name, ep_ow, ep_thumb, air_date = ep_result
                    
                    if ep_name:
                        episode_name = ep_name
                    if ep_ow:
                        ep_overview = ep_ow + f"\nS{se:02d}E{ep:02d}"
                    if ep_thumb:
                        thumb_url = ep_thumb
                    if air_date:
                        released = air_date
            else:
                # Not a TV episode or no S/E info
                ep_overview = raw_desc
                if not episode_name:
                    episode_name = title
                thumb_url = background_url
        
        # Fallbacks for poster/thumbnail
        if poster_url == None:
            has_real_thumb = hasattr(file, "thumbs") and file.thumbs
            if has_real_thumb:
                poster_url = f"{SURF_TG_BASE_URL}/api/thumb/{chat_id}?id={message.id}"
                background_url = f"{SURF_TG_BASE_URL}/api/thumb/{chat_id}?id={message.id}"
            else:
                clean_t = quote(title)
                poster_url = f"https://placehold.jp/40/1a1a2e/ffffff/600x900.png?text={clean_t}"
                background_url = f"https://placehold.jp/40/1a1a2e/ffffff/600x900.png?text={clean_t}"
        
        if not thumb_url:
            has_real_thumb = hasattr(file, "thumbs") and file.thumbs
            if has_real_thumb:
                thumb_url = f"{SURF_TG_BASE_URL}/api/thumb/{chat_id}?id={message.id}"
        
        if not episode_name:
            episode_name = title
        if not ep_overview:
            ep_overview = raw_desc
        
        if not released:
            released = datetime.now().strftime("%Y-%m-%d")
        
        messages.append({
            "msg_id": message.id,
            "title": title,
            "description": full_desc,
            "hash": file.file_unique_id,
            "size": get_readable_file_size(file.file_size),
            "type": file.mime_type,
            "chat_id": str(chat_id),
            "img": poster_url,
            "background": background_url,
            "ep_ow": ep_overview,
            "ep_name": episode_name,
            "thumbnail": thumb_url,
            "released": released
        })
    
    print(f"✅ Processed {len(messages)} new video files")
    print(f"⏭️  Skipped {len(existing_msg_ids)} already indexed")
    print(f"📝 Skipped {non_video_count} non-video messages")
    
    return messages

async def get_messages(chat_id, first_message_id, last_message_id, batch_size=50):
    messages = []
    
    # 1. Get existing IDs from DB
    existing_msg_ids = set(
        doc['msg_id'] 
        for doc in db.files.find(
            {"chat_id": str(chat_id)}, 
            {"msg_id": 1, "_id": 0}
        )
    )
    print(f"📊 Found {len(existing_msg_ids)} existing messages in database")

    # 2. Calculate missing IDs
    all_msg_ids = list(range(first_message_id, last_message_id + 1))
    missing_msg_ids = [m_id for m_id in all_msg_ids if m_id not in existing_msg_ids]
    
    if not missing_msg_ids:
        print("✅ Everything is already indexed.")
        return []

    print(f"🔍 Need to check {len(missing_msg_ids)} IDs for videos...")

    # 3. BULK FETCH from Telegram (200 at a time - Bot limit)
    # This is much faster than one-by-one because it reduces network trips.
    tg_batch_size = 200 
    raw_messages = []
    non_video_count = 0

    for i in range(0, len(missing_msg_ids), tg_batch_size):
        id_chunk = missing_msg_ids[i:i + tg_batch_size]
        
        # This is the secret: asking for many IDs at once
        chunk_messages = await StreamBot.get_messages(chat_id, id_chunk)
        
        for msg in chunk_messages:
            # Telegram returns None for deleted messages - we just skip them!
            if not msg or msg.empty:
                continue

            if file := (msg.video or msg.document):
                # --- YOUR EXACT TITLE LOGIC ---
                title = ""
                if msg.caption:
                    clean_caption = msg.caption.strip().split("\n")[0]
                    clean_caption = clean_hebrew_title(clean_caption)
                    title = clean_caption[:33].rsplit(" ", 1)[0] if len(clean_caption) > 33 else clean_caption
                else:
                    raw_fn = file.file_name or ""
                    title, _ = splitext(raw_fn)
                    is_default = any(x in title.lower() for x in ["default_name", "default name", "undefined", "index"])
                    if not is_default and title:
                        title = clean_hebrew_title(title)
                        if len(title) > 33:
                            title = title[:33].rsplit(" ", 1)[0]
                
                if not title:
                    title = f"Video {msg.id}"
                
                full_desc = str(msg.caption) if msg.caption else str(file.file_name or "")
                
                raw_messages.append({
                    'message': msg,
                    'file': file,
                    'title': title,
                    'full_desc': full_desc,
                    'raw_desc': full_desc
                })
                
                # If we have enough videos to process a TMDB batch
                if len(raw_messages) >= batch_size:
                    print(f"📦 Processing TMDB batch ({len(raw_messages)} videos)...")
                    batch_results = await process_metadata_batch(raw_messages, chat_id)
                    messages.extend(batch_results)
                    raw_messages = []
            else:
                non_video_count += 1

    # Final leftover batch
    if raw_messages:
        print(f"📦 Processing final TMDB batch ({len(raw_messages)} videos)...")
        batch_results = await process_metadata_batch(raw_messages, chat_id)
        messages.extend(batch_results)

    print(f"✅ Processed {len(messages)} new files. Skipped {non_video_count} non-videos.")
    return messages

async def process_metadata_batch(batch_data, chat_id):
    """Helper that handles the parallel TMDB logic for one batch of messages."""
    processed_list = []
    
    # Wave 1: Parallel TMDB Search
    tmdb_tasks = [get_tmdb_details(m['title'], m['full_desc'], TMDB_API_KEY, tmdb_client) for m in batch_data]
    tmdb_results = await asyncio.gather(*tmdb_tasks, return_exceptions=True)

    # Wave 2: Parallel Episode Details
    episode_tasks = []
    episode_indices = []
    for idx, (raw_msg, tmdb_res) in enumerate(zip(batch_data, tmdb_results)):
        if isinstance(tmdb_res, Exception) or not tmdb_res:
            continue
        se, ep = extract_season_episode(raw_msg['full_desc'])
        if (ep or se) and tmdb_res.get("media_type") == "tv":
            episode_tasks.append(get_tmdb_ep_det(tmdb_res.get("id"), se, ep, TMDB_API_KEY, tmdb_client))
            episode_indices.append(idx)

    episode_results = await asyncio.gather(*episode_tasks, return_exceptions=True) if episode_tasks else []
    ep_data_map = {episode_indices[i]: res for i, res in enumerate(episode_results) if not isinstance(res, Exception)}

    # Wave 3: Final Object Construction (Exact same original logic)
    for idx, (raw_msg, tmdb_res) in enumerate(zip(batch_data, tmdb_results)):
        message, file, title = raw_msg['message'], raw_msg['file'], raw_msg['title']
        full_desc, raw_desc = raw_msg['full_desc'], raw_msg['raw_desc']
        
        if isinstance(tmdb_res, Exception): tmdb_res = None
        
        se, ep = extract_season_episode(full_desc)
        episode_name, ep_name, ep_overview, thumb_url = None, None, None, None
        poster_url, background_url, released = None, None, None

        if tmdb_res:
            tmdb_id = tmdb_res.get("id")
            media_type = tmdb_res.get("media_type")
            p_path, b_path = tmdb_res.get("poster_path"), tmdb_res.get("backdrop_path")
            overv = tmdb_res.get("overview")
            
            if p_path:
                poster_url = f"https://image.tmdb.org/t/p/w500{p_path}"
                background_url = f"https://image.tmdb.org/t/p/w1280{b_path if b_path else p_path}"
            
            if overv: full_desc = overv
            
            # --- YOUR ORIGINAL DATE LOGIC ---
            if tmdb_res.get("release_date"): released = tmdb_res.get("release_date")
            if tmdb_res.get("first_air_date"): released = tmdb_res.get("first_air_date")

            if idx in ep_data_map and ep_data_map[idx]:
                ep_res = ep_data_map[idx]
                if ep_res:
                    ep_name, ep_ow, ep_thumb, air_date = ep_res
                    if ep_name: episode_name = ep_name
                    if ep_ow: ep_overview = ep_ow + f"\nS{se:02d}E{ep:02d}"
                    if ep_thumb: thumb_url = ep_thumb
                    if air_date: released = air_date
            else:
                if not episode_name: episode_name = title
                ep_overview, thumb_url = raw_desc, background_url

        # --- FALLBACKS ---
        if poster_url == None:
            if hasattr(file, "thumbs") and file.thumbs:
                poster_url = background_url = f"{SURF_TG_BASE_URL}/api/thumb/{chat_id}?id={message.id}"
            else:
                clean_t = quote(title)
                poster_url = background_url = f"https://placehold.jp/40/1a1a2e/ffffff/600x900.png?text={clean_t}"
        
        if not thumb_url and hasattr(file, "thumbs") and file.thumbs:
            thumb_url = f"{SURF_TG_BASE_URL}/api/thumb/{chat_id}?id={message.id}"
        
        if not episode_name: episode_name = title
        if not ep_overview: ep_overview = raw_desc
        
        # --- YOUR ORIGINAL DEFAULT DATE LOGIC ---
        if not released:
            released = datetime.now().strftime("%Y-%m-%d")
        
        processed_list.append({
            "msg_id": message.id,
            "title": title,
            "description": full_desc,
            "hash": file.file_unique_id,
            "size": get_readable_file_size(file.file_size),
            "type": file.mime_type,
            "chat_id": str(chat_id),
            "img": poster_url,
            "background": background_url,
            "ep_ow": ep_overview,
            "ep_name": episode_name,
            "thumbnail": thumb_url,
            "released": released
        })
    return processed_list

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
