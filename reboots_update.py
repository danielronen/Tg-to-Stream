import re
import httpx
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne
from dotenv import load_dotenv
import os

load_dotenv("config.env")

# --- CONFIG ---
MONGO_URI = os.getenv("DATABASE_URL")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
DB_NAME = 'surftg'
COLLECTION_NAME = 'files'

SHOWS_CONFIG = {
    "האח הגדול": { 
        "tmdb_id": 86255, 
        "season_map": { 8: 16 } 
    },
}

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

async def get_tmdb_episode(client, tmdb_id, season, episode):
    url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}"
    params = {"api_key": TMDB_API_KEY, "language": "he-IL"}
    try:
        res = await client.get(url, params=params, timeout=10.0)
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    return None

async def run_reboot_sync(dry_run=True):
    client_db = AsyncIOMotorClient(MONGO_URI)
    db = client_db[DB_NAME]
    col = db[COLLECTION_NAME]
    target_titles = list(SHOWS_CONFIG.keys())

    async with httpx.AsyncClient() as http_client:
        cursor = col.find({"title": {"$in": target_titles}})
        bulk_ops = []

        async for doc in cursor:
            title = doc.get("title")
            # --- EXTRACT S/E (Update this if your field names are different) ---
            
            ow = doc.get("ep_ow")
            db_season, db_episode = extract_season_episode(ow)
            
            #db_season = doc.get("season_number") 
            #db_episode = doc.get("episode_number")
            
            if not db_season or not db_episode: continue

            tmdb_s = SHOWS_CONFIG[title]["season_map"].get(db_season, db_season)
            data = await get_tmdb_episode(http_client, SHOWS_CONFIG[title]["tmdb_id"], tmdb_s, db_episode)

            if not data: continue

            # --- SMART TITLE LOGIC ---
            raw_name = data.get("name") or ""
            is_generic = re.fullmatch(r"פרק\s*\d+", raw_name)
            final_name = raw_name if (raw_name and not is_generic) else title

            # --- NO-LOSS OVERVIEW ---
            stremio_tag = f"S{db_season:02d}E{db_episode:02d}"
            current_ow = doc.get("ep_ow") or doc.get("description") or ""
            tmdb_ow = data.get("overview") or ""
            
            # Use TMDB if it's long enough, else keep current
            base_ow = tmdb_ow if len(tmdb_ow) > 10 else current_ow
            final_ow = f"{base_ow}\n\n{stremio_tag}".strip() if stremio_tag not in base_ow else base_ow

            update_fields = {
                "ep_name": final_name,
                "ep_ow": final_ow,
                "thumbnail": f"https://image.tmdb.org/t/p/w1280{data.get('still_path')}" if data.get('still_path') else doc.get("thumbnail"),
                "released": data.get("air_date")
            }

            if dry_run:
                print(f"[DRY RUN] {title} S{db_season}E{db_episode} -> Name: {final_name}")
            else:
                bulk_ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": update_fields}))

        if bulk_ops:
            await col.bulk_write(bulk_ops)
            print(f"Updated {len(bulk_ops)} records.")

if __name__ == "__main__":
    asyncio.run(run_reboot_sync(dry_run=False))