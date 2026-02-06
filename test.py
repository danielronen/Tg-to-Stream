from os import getenv
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from pymongo import MongoClient
from urllib.parse import quote, unquote
from collections import defaultdict
import os
import re
import hashlib

# ========== CONFIGURATION ==========
load_dotenv("config.env")
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is required")

MONGODB_URI = f"{DATABASE_URL}/surftg"
SURF_TG_BASE_URL = os.getenv("BASE_URL", "http://localhost:8080")
ADDON_NAME = os.getenv("ADDON_NAME", "My Hebrew Videos")
AUTH_CHANNEL = os.getenv("AUTH_CHANNEL", "")

# ========== CHANNEL CONFIGURATION ==========
# Define your TV channels with keywords to match in filenames
TV_CHANNELS = {
    "כאן 11": ["כאן 11", "כאן11", "kan 11", "kan11"],
    "קשת 12": ["קשת 12", "קשת12", "keshet12", "keshet 12", "keshet"],
    "רשת 13": ["רשת 13", "רשת13", "reshet 13", "reshet13", "reshet 13"],
    "עכשיו 14": ["עכשיו 14", "עכשיו14", "channel 14"],
    "i24News": ["I24NEWS", "i24News", "i24news"],
    "Sport 5": ["Sport 5", "Sport5", "ספורט5", "ספורט 5"],
    "Yes": ["Yes", "Yes+", "יס"],
    "Hot": ["Hot", "HOT", "הוט"],
    # Add more channels here as needed
}

# Connect to MongoDB
print(f"Connecting to MongoDB...")
client = MongoClient(MONGODB_URI)
db = client.get_default_database()
print(f"Connected to database: {db.name}")

# Cache for collection name
_VIDEO_COLLECTION = None

# ========== STREMIO MANIFEST ==========

def build_manifest():
    """Build manifest with three separate catalogs"""
    genres = list(TV_CHANNELS.keys())
    
    return {
        "id": "community.surftg.series",  
        "version": "3.2.0",
        "name": ADDON_NAME,
        "description": "Stream your Telegram videos as Series, Movies and TV Catchup", 
        "resources": ["catalog", "stream", "meta"],
        "types": ["series", "movie"],  
        "catalogs": [
            # 1. TV Series - Only series with seasons/episodes
            {
                "type": "series",
                "id": "surftg_series",
                "name": "TV Series",
                "extra": [
                    {"name": "search", "isRequired": False}
                ],
            },
            # 2. Movies - Only standalone content WITHOUT channel association
            {
                "type": "movie",
                "id": "surftg_movies",
                "name": "Movies & Other",
                "extra": [
                    {"name": "search", "isRequired": False}
                ],
            },
            # 3. TV Catchup - All channel-related content with REQUIRED genre filter
            {
                "type": "movie",
                "id": "surftg_tv_catchup",
                "name": "📺 TV Catchup",
                "extra": [
                    {"name": "search", "isRequired": False},
                    {
                        "name": "genre",
                        "isRequired": False,  # Can't force it, but we'll return empty if not set
                        "options": genres,
                        "optionsLimit": 1
                    }
                ],
            }
        ],
        "idPrefixes": ["surftg_"],
        "behaviorHints": {"configurable": False, "configurationRequired": False},
    }

MANIFEST = build_manifest()

# ========== HELPER FUNCTIONS ==========

def find_video_collection():
    """Auto-detect which collection has video files"""
    global _VIDEO_COLLECTION
    if _VIDEO_COLLECTION:
        return _VIDEO_COLLECTION

    possible_names = [
        "files", "media", "messages", "telegram_files", "tg_files",
        "channel_files", "Videos", "videos", "file", "playlist",
    ]

    collections = db.list_collection_names()

    for name in possible_names:
        if name in collections:
            count = db[name].count_documents({})
            if count > 0:
                print(f"Found collection '{name}' with {count} documents")
                _VIDEO_COLLECTION = name
                return name

    for coll in collections:
        if not coll.startswith("system."):
            count = db[coll].count_documents({})
            if count > 0:
                print(f"Using collection '{coll}' with {count} documents")
                _VIDEO_COLLECTION = coll
                return coll

    return None

def detect_channel(title, description=""):
    """
    Detect which TV channel a video belongs to.
    Returns channel name if found, None otherwise.
    """
    search_text = f"{title} {description}".lower()
    
    for channel_name, keywords in TV_CHANNELS.items():
        for keyword in keywords:
            if keyword.lower() in search_text:
                return channel_name
    
    return None

def extract_season_episode2(description):
    """
    Extract season and episode numbers from Hebrew or English descriptions.
    Returns: (season_number, episode_number) or (None, None) if not found
    """
    if not description:
        return None, None    
    
    # Hebrew format "עונה X פרק Y"
    hebrew_pattern = r'עונה\s*(\d+)\s*[|\s]*פרק\s*(\d+)'
    match = re.search(hebrew_pattern, description)
    if match:
        return int(match.group(1)), int(match.group(2))    
    
    # Hebrew abbreviated "ע2 פ3"
    hebrew_abbrev_pattern = r'ע(\d+)\s*פ(\d+)'
    match = re.search(hebrew_abbrev_pattern, description)
    if match:
        return int(match.group(1)), int(match.group(2))    
    
    # English "Season X Episode Y"
    english_pattern = r'[Ss]eason\s*(\d+)\s*[Ee]pisode\s*(\d+)'
    match = re.search(english_pattern, description)
    if match:
        return int(match.group(1)), int(match.group(2))    
    
    # Short "S01E02"
    short_pattern = r'[Ss](\d+)[Ee](\d+)'
    match = re.search(short_pattern, description)
    if match:
        return int(match.group(1)), int(match.group(2))    
    
    # Hebrew episode only "פרק 5" (assumes Season 1)
    hebrew_episode_only = r'פרק\s*(\d+)'
    match = re.search(hebrew_episode_only, description)
    if match:
        return 1, int(match.group(1))
    
    # Very short Hebrew "פ5"
    hebrew_very_short_episode = r'פ(\d+)'
    match = re.search(hebrew_very_short_episode, description)
    if match:
        return 1, int(match.group(1))
    
    # English episode only "Episode 5"
    english_episode_only = r'(?:Episode|Ep\.?|EP)\s*(\d+)'
    match = re.search(english_episode_only, description, re.IGNORECASE)
    if match:
        return 1, int(match.group(1))
    
    # Very short "E05"
    short_episode_only = r'\bE(\d+)\b'
    match = re.search(short_episode_only, description, re.IGNORECASE)
    if match:
        return 1, int(match.group(1))
    
    return None, None

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

def get_series_id(series_name):
    """Generate safe ID from series name"""
    hash_object = hashlib.md5(series_name.encode('utf-8'))
    hash_hex = hash_object.hexdigest()[:12]
    return f"surftg_series_{hash_hex}"

def normalize_series_name(title):
    """Normalize series name by removing episode-specific info"""
    if not title:
        return "Unknown Series"
    normalized = title.strip()
    normalized = re.sub(r'\s*-\s*.*$', '', normalized)
    return normalized.strip()

def classify_video(video_doc):
    """
    Classify a video into one of three categories:
    
    1. 'series' - Has season/episode info
    2. 'tv_channel' - Has channel keyword but no S/E info (TV catchup content)
    3. 'movie' - No S/E info and no channel (standalone movie/other)
    
    Returns: (category, series_name_or_none, season_or_none, episode_or_none)
    """
    description = video_doc.get("description", "") or ""
    title = video_doc.get("title", "")
    
    # Check for season/episode
    season, episode = extract_season_episode(description)
    
    if season and episode:
        # Has S/E → It's a series
        series_name = normalize_series_name(title)
        return 'series', series_name, season, episode
    
    # No S/E info - check if it has channel association
    channel = detect_channel(title, description)
    
    if channel:
        # Has channel but no S/E → TV catchup content
        return 'tv_channel', None, None, None
    else:
        # No channel, no S/E → Standalone movie/other
        return 'movie', None, None, None

def get_all_series(channel_filter=None):
    """Get all series with their episodes, optionally filtered by channel"""
    collection_name = find_video_collection()
    if not collection_name:
        return {}
    
    collection = db[collection_name]
    all_videos = list(collection.find({"msg_id": {"$exists": True}}, {"_id": 0}))
    
    series_dict = defaultdict(list)
    
    for video in all_videos:
        # Filter by channel if specified
        if channel_filter:
            title = video.get("title", "")
            description = video.get("description", "")
            video_channel = detect_channel(title, description)
            
            if video_channel != channel_filter:
                continue
        
        vid_type, series_name, season, episode = classify_video(video)
        
        if vid_type == 'series':
            series_dict[series_name].append({
                'video': video,
                'season': season,
                'episode': episode
            })
    
    return dict(series_dict)

def get_series_meta(series_name, episodes_list):
    """Create series metadata object for Stremio"""
    sorted_eps = sorted(episodes_list, key=lambda x: (x['season'], x['episode']))
    latest = sorted_eps[0]['video'] if sorted_eps else None
    
    seasons = set(ep['season'] for ep in episodes_list)
    total_episodes = len(episodes_list)
    
    poster_url = latest.get('img') if latest else None
    background_url = latest.get('background') if latest else None
    
    # Detect channel for series description
    channel = None
    if latest:
        channel = detect_channel(latest.get('title', ''), latest.get('description', ''))
    
    # Build description
    description_parts = [f"📺 {len(seasons)} Season(s) • {total_episodes} Episodes"]
    if channel:
        description_parts.insert(0, f"📡 {channel}")
    description = "\n".join(description_parts)
    
    series_hash = abs(hash(series_name))
    series_id = f"surftg_series_{series_hash:x}"
    
    return {
        "id": series_id,
        "type": "series",
        "name": series_name,
        "poster": poster_url,
        "description": description,
        "background": background_url
    }

def generate_stream_url(video_doc):
    """Generate Surf-TG streaming URL for Stremio"""
    msg_id = video_doc.get("msg_id")
    chat_id = video_doc.get("chat_id", AUTH_CHANNEL)
    hash_value = video_doc.get("hash", "")
    title = video_doc.get("title", "video")
    hash_short = hash_value[:6] if len(hash_value) > 6 else hash_value
    chat_id_clean = str(chat_id).replace("-100", "")
    filename = title.replace(" ", "_")
    filename = re.sub(r"[^\w\-_\.]", "_", filename)
    
    if not any(filename.lower().endswith(ext) for ext in [".mkv", ".mp4", ".avi", ".mov", ".webm"]):
        video_type = video_doc.get("type", "video/x-matroska")
        if "matroska" in video_type or "mkv" in video_type:
            filename += ".mkv"
        elif "mp4" in video_type:
            filename += ".mp4"
        else:
            filename += ".mkv"
    
    filename_encoded = quote(filename, safe="")
    stream_url = f"{SURF_TG_BASE_URL}/{chat_id_clean}/{filename_encoded}?id={msg_id}&hash={hash_short}"
    
    return stream_url

def video_to_movie_meta(video_doc):
    """Convert video document to movie metadata"""
    msg_id = str(video_doc.get("msg_id", ""))
    stremio_id = f"surftg_movie_{msg_id}"
    title = video_doc.get("title", f"Video {msg_id}")
    description = video_doc.get("description") or ""
    size = video_doc.get("size", "Unknown")
    poster_url = video_doc.get("img")
    background_url = video_doc.get("background")
    
    # Add channel info to description
    channel = detect_channel(title, description)
    desc_parts = []
    if channel:
        desc_parts.append(f"📡 {channel}")
    if description:
        desc_parts.append(description)
    desc_parts.append(f"💾 Size: {size}")
    
    return {
        "id": stremio_id,
        "type": "movie",
        "name": title,
        "poster": poster_url,
        "description": "\n".join(desc_parts),
        "background": background_url
    }

# ========== STREMIO ROUTES ==========

async def manifest(request):
    return JSONResponse(
        MANIFEST,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*",
            "ngrok-skip-browser-warning": "true",
        },
    )

async def catalog(request):
    """
    Three catalog types with strict separation:
    
    1. surftg_series - Only videos classified as 'series' (has S/E info)
    2. surftg_movies - Only videos classified as 'movie' (no channel, no S/E)
    3. surftg_tv_catchup - Only videos classified as 'tv_channel' (has channel, no S/E)
                          - REQUIRES genre filter, returns empty if none selected
    """
    try:
        catalog_type = request.path_params.get("type", "series")
        catalog_id = request.path_params.get("id", "")
        extra_str = request.path_params.get("extra", "")
        
        # Extract search query and genre filter
        search_query = None
        genre_filter = None
        
        if extra_str:
            params = extra_str.split("&")
            for param in params:
                if "search=" in param:
                    search_query = unquote(param.replace("search=", ""))
                elif "genre=" in param:
                    genre_filter = unquote(param.replace("genre=", ""))
        
        skip = int(request.query_params.get("skip", 0))
        limit = 100
        
        collection_name = find_video_collection()
        collection = db[collection_name]
        
        metas = []
        
        # ========== TV CATCHUP CATALOG ==========
        if catalog_id == "surftg_tv_catchup":
            # CRITICAL: Return empty if no genre selected
            if not genre_filter:
                print("TV Catchup: No genre selected, returning empty catalog")
                return JSONResponse(
                    {"metas": []},
                    headers={
                        "Access-Control-Allow-Origin": "*",
                        "ngrok-skip-browser-warning": "true",
                    },
                )
            
            # Build query
            query = {"msg_id": {"$exists": True}}
            
            if search_query:
                query["$or"] = [
                    {"title": {"$regex": search_query, "$options": "i"}},
                    {"description": {"$regex": search_query, "$options": "i"}},
                ]
            
            # Get all videos
            videos = list(collection.find(query, {"_id": 0}).sort("msg_id", -1))
            
            # Filter: Only 'tv_channel' type videos matching the selected channel
            for video in videos:
                vid_type, _, _, _ = classify_video(video)
                
                # Only include videos classified as 'tv_channel'
                if vid_type == 'tv_channel':
                    title = video.get("title", "")
                    description = video.get("description", "")
                    video_channel = detect_channel(title, description)
                    
                    if video_channel == genre_filter:
                        metas.append(video_to_movie_meta(video))
                        if len(metas) >= limit:
                            break
            
            print(f"TV Catchup: Found {len(metas)} videos for {genre_filter}")
        
        # ========== SERIES CATALOG ==========
        elif catalog_type == "series" or catalog_id == "surftg_series":
            series_dict = get_all_series(channel_filter=None)
            
            if search_query:
                series_dict = {
                    name: eps for name, eps in series_dict.items()
                    if search_query.lower() in name.lower()
                }
            
            series_list = []
            for series_name, episodes in series_dict.items():
                series_list.append(get_series_meta(series_name, episodes))
            
            series_list.sort(key=lambda x: x['name'])
            metas = series_list[skip:skip+limit]
        
        # ========== MOVIES CATALOG ==========
        else:
            query = {"msg_id": {"$exists": True}}
            
            if search_query:
                query["$or"] = [
                    {"title": {"$regex": search_query, "$options": "i"}},
                    {"description": {"$regex": search_query, "$options": "i"}},
                ]
            
            videos = list(collection.find(query, {"_id": 0}).sort("msg_id", -1))
            
            # CRITICAL: Only include videos classified as 'movie' (no channel, no S/E)
            for video in videos:
                vid_type, _, _, _ = classify_video(video)
                
                if vid_type == 'movie':
                    metas.append(video_to_movie_meta(video))
                    if len(metas) >= limit:
                        break
            
            print(f"Movies: Found {len(metas)} standalone movies")
        
        return JSONResponse(
            {"metas": metas},
            headers={
                "Access-Control-Allow-Origin": "*",
                "ngrok-skip-browser-warning": "true",
            },
        )
    
    except Exception as e:
        print(f"❌ Catalog Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"metas": []})

async def meta(request):
    try:
        meta_type = request.path_params["type"]
        meta_id = request.path_params["id"]        
        
        # SERIES METADATA
        if meta_id.startswith("surftg_series_"):
            series_dict = get_all_series()
            
            matching_name = None            
            for series_name, episodes in series_dict.items():
                series_meta = get_series_meta(series_name, episodes)
                if series_meta["id"] == meta_id:
                    matching_name = series_name
                    break
            
            if not matching_name:
                return JSONResponse({"meta": {}})
            
            episodes = series_dict[matching_name]
            
            # Group episodes by season
            videos_by_season = defaultdict(list)
            for ep_data in episodes:
                videos_by_season[ep_data['season']].append(ep_data)
            
            # Build episode list
            videos = []
            for season in sorted(videos_by_season.keys()):
                for ep_data in sorted(videos_by_season[season], key=lambda x: x['episode']):
                    video = ep_data['video']
                    episode_id = f"surftg_{video.get('msg_id')}"
                    
                    episode_obj = {
                        "id": episode_id,
                        "title": f"S{ep_data['season']:02d}E{ep_data['episode']:02d}",
                        "season": ep_data['season'],
                        "episode": ep_data['episode'],
                    }
                    
                    desc = video.get('description', '').strip()
                    if desc:
                        episode_obj["overview"] = desc
                    
                    thumb = video.get('background', '').strip()
                    if thumb and not thumb.startswith('https://placehold.jp'):
                        episode_obj["thumbnail"] = thumb
                    
                    videos.append(episode_obj)
            
            series_meta = get_series_meta(matching_name, episodes)
            
            final_meta = {
                "id": series_meta["id"],
                "type": "series",
                "name": series_meta["name"],
                "videos": videos
            }
            
            poster = series_meta.get("poster", "").strip()
            if poster and not poster.startswith('https://placehold.jp'):
                final_meta["poster"] = poster
            
            background = series_meta.get("background", "").strip()
            if background and not background.startswith('https://placehold.jp'):
                final_meta["background"] = background
            
            desc = series_meta.get("description", "").strip()
            if desc:
                final_meta["description"] = desc

            return JSONResponse(
                {"meta": final_meta},
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Content-Type": "application/json; charset=utf-8"
                }
            )
        
        # MOVIE METADATA
        elif meta_id.startswith("surftg_movie_"):
            msg_id = meta_id.replace("surftg_movie_", "")
            collection = db[find_video_collection()]
            
            video_doc = collection.find_one({"msg_id": msg_id}) or collection.find_one(
                {"msg_id": int(msg_id) if msg_id.isdigit() else None}
            )
            if not video_doc:
                return JSONResponse({"meta": {}})
            
            return JSONResponse(
                {"meta": video_to_movie_meta(video_doc)},
                headers={"Access-Control-Allow-Origin": "*"}
            )
        
        return JSONResponse({"meta": {}})
    
    except Exception as e:
        print(f"❌ Meta Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"meta": {}})

async def stream(request):
    try:
        video_id = request.path_params["id"]
        msg_id_str = video_id.replace("surftg_", "").replace("movie_", "").replace("series_", "")
        collection = db[find_video_collection()]
        
        video_doc = collection.find_one({"msg_id": msg_id_str}, {"_id": 0})
        if not video_doc:
            try:
                msg_id_int = int(msg_id_str)
                video_doc = collection.find_one({"msg_id": msg_id_int}, {"_id": 0})
            except ValueError:
                pass
        
        if not video_doc:
            return JSONResponse({"streams": []})
        
        stream_url = generate_stream_url(video_doc)
        title = video_doc.get("title", "Unknown")
        size = video_doc.get("size", "")
        description = video_doc.get("description", "")
        
        # Add channel info to stream title
        channel = detect_channel(title, description)
        season, episode = extract_season_episode(description)
        
        title_parts = []
        if channel:
            title_parts.append(f"📡 {channel}")
        
        if season and episode:
            title_parts.append(f"{title} | S{season:02d}E{episode:02d}")
        else:
            title_parts.append(title)
        
        title_parts.append(f"💾 {size}")
        stream_title = "\n".join(title_parts)
        
        return JSONResponse(
            {
                "streams": [
                    {
                        "url": stream_url,
                        "title": stream_title,
                        "name": title,
                    }
                ]
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            },
        )
    
    except Exception as e:
        print(f"❌ Stream Error: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"streams": []})

# ========== APP SETUP ==========

routes = [
    Route("/manifest.json", manifest),
    Route("/catalog/{type}/{id}.json", catalog),
    Route("/catalog/{type}/{id}/{extra}.json", catalog),
    Route("/meta/{type}/{id}.json", meta),
    Route("/detail/{type}/{id}.json", meta),
    Route("/stream/{type}/{id}.json", stream),
]

middleware = [
    Middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "HEAD", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["*"],
    )
]

app = Starlette(routes=routes, middleware=middleware)

if __name__ == "__main__":
    import uvicorn
    
    print("\n" + "=" * 60)
    print("ISRAEL TV ADDON - THREE SEPARATE CATALOGS")
    print("=" * 60)
    
    print("\n📺 Configured TV Channels:")
    for channel, keywords in TV_CHANNELS.items():
        print(f"   {channel}: {', '.join(keywords[:3])}")
    
    print("\nTesting database connection...")
    collection_name = find_video_collection()
    if collection_name:
        collection = db[collection_name]
        all_videos = list(collection.find({"msg_id": {"$exists": True}}, {"_id": 0}).limit(200))
        
        # Classify all videos
        series_count = 0
        movie_count = 0
        tv_catchup_count = 0
        channel_dist = defaultdict(int)
        
        for video in all_videos:
            vid_type, _, _, _ = classify_video(video)
            if vid_type == 'series':
                series_count += 1
            elif vid_type == 'movie':
                movie_count += 1
            elif vid_type == 'tv_channel':
                tv_catchup_count += 1
                # Count per channel
                title = video.get("title", "")
                description = video.get("description", "")
                channel = detect_channel(title, description)
                if channel:
                    channel_dist[channel] += 1
        
        print(f"\n📊 Content Distribution (from {len(all_videos)} videos):")
        print(f"   📺 TV Series: {series_count} episodes")
        print(f"   🎬 Movies & Other: {movie_count} files")
        print(f"   📡 TV Catchup: {tv_catchup_count} files")
        
        if channel_dist:
            print(f"\n📡 TV Catchup by Channel:")
            for ch, count in sorted(channel_dist.items()):
                print(f"   {ch}: {count} videos")
    
    print("=" * 60 + "\n")
    
    port = 7000
    ssl_cert = "cert.pem"
    ssl_key = "key.pem"
    
    if os.path.exists(ssl_cert) and os.path.exists(ssl_key):
        print(f"Starting addon with HTTPS on https://0.0.0.0:{port}")
        print(f"Install URL: https://192.168.7.6:{port}/manifest.json\n")
        uvicorn.run(app, host="0.0.0.0", port=port, ssl_keyfile=ssl_key, ssl_certfile=ssl_cert)
    else:
        print(f"Starting addon with HTTP on http://0.0.0.0:{port}")
        print(f"Install URL: http://127.0.0.1:{port}/manifest.json\n")
        uvicorn.run(app, host="0.0.0.0", port=port)