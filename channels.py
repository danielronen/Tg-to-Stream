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
    "רשת 13": ["רשת 13", "רשת13", "reshet 13", "reshet13", "reshet 13"],
    "ערוץ 12": ["קשת 12", "קשת12", "keshet12", "keshet 12", "keshet"],
    "כאן 11": ["כאן 11", "כאן11", "kan 11", "kan11"],
    "ערוץ 14": ["עכשיו 14", "עכשיו14", "channel 14"],
    "i24News": ["I24NEWS", "i24News", "i24news"],
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
    """Build manifest with genre filters for channels"""
    # Create genres list from TV channels
    genres = list(TV_CHANNELS.keys())
    
    return {
        "id": "community.surftg.series",  
        "version": "3.1.0",  # Bumped version for separate TV catchup catalog
        "name": ADDON_NAME,
        "description": "Stream your Telegram videos as Series, Movies and TV Catchup", 
        "resources": ["catalog", "stream", "meta"],
        "types": ["series", "movie"],  
        "catalogs": [
            # Series catalog (no channel filter - keeps it clean)
            {
                "type": "series",
                "id": "surftg_series",
                "name": "TV Series",
                "extra": [
                    {"name": "search", "isRequired": False}
                ],
            },
            # Movies catalog (no channel filter)
            {
                "type": "movie",
                "id": "surftg_movies",
                "name": "Movies & Other",
                "extra": [
                    {"name": "search", "isRequired": False}
                ],
            },
            # NEW: TV Catchup catalog with channel genre filter
            {
                "type": "movie",
                "id": "surftg_tv_catchup",
                "name": "📺 TV Catchup",
                "extra": [
                    {"name": "search", "isRequired": False},
                    {
                        "name": "genre",
                        "isRequired": False,
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
    Detect which TV channel a video belongs to based on title/description.
    Returns channel name if found, None otherwise.
    """
    search_text = f"{title} {description}".lower()
    
    for channel_name, keywords in TV_CHANNELS.items():
        for keyword in keywords:
            if keyword.lower() in search_text:
                return channel_name
    
    return None

def extract_season_episode(description):
    """
    Extract season and episode numbers from Hebrew or English descriptions.
    
    Supports formats:
    - Hebrew: "עונה 10 | פרק 9" or "עונה 2 פרק 3"
    - Hebrew abbreviated: "ע2 פ3" or "ע1פ2"
    - Hebrew very short: "פ5" (episode only, assumes Season 1)
    - English: "Season 5 Episode 12"
    - Short: "S02E03"
    - Episode only: "פרק 5" or "Episode 5" or "E05" (assumes Season 1)
    
    Returns: (season_number, episode_number) or (None, None) if not found
    """
    if not description:
        return None, None    
    
    # Hebrew format "עונה X פרק Y" with optional separator (|, space, etc.)
    hebrew_pattern = r'עונה\s*(\d+)\s*[|\s]*פרק\s*(\d+)'
    match = re.search(hebrew_pattern, description)
    if match:
        return int(match.group(1)), int(match.group(2))    
    
    # Hebrew abbreviated format "ע2 פ3" or "ע1פ2"
    hebrew_abbrev_pattern = r'ע(\d+)\s*פ(\d+)'
    match = re.search(hebrew_abbrev_pattern, description)
    if match:
        return int(match.group(1)), int(match.group(2))    
    
    # English format "Season X Episode Y"
    english_pattern = r'[Ss]eason\s*(\d+)\s*[Ee]pisode\s*(\d+)'
    match = re.search(english_pattern, description)
    if match:
        return int(match.group(1)), int(match.group(2))    
    
    # Short format "S01E02" or "s1e2"
    short_pattern = r'[Ss](\d+)[Ee](\d+)'
    match = re.search(short_pattern, description)
    if match:
        return int(match.group(1)), int(match.group(2))    
    
    # Episode only in Hebrew "פרק 5" (assumes Season 1)
    hebrew_episode_only = r'פרק\s*(\d+)'
    match = re.search(hebrew_episode_only, description)
    if match:
        return 1, int(match.group(1))
    
    # Very short Hebrew episode only "פ5" (assumes Season 1)
    hebrew_very_short_episode = r'פ(\d+)'
    match = re.search(hebrew_very_short_episode, description)
    if match:
        return 1, int(match.group(1))
    
    # Episode only in English "Episode 5" or "Ep 5" (assumes Season 1)
    english_episode_only = r'(?:Episode|Ep\.?|EP)\s*(\d+)'
    match = re.search(english_episode_only, description, re.IGNORECASE)
    if match:
        return 1, int(match.group(1))
    
    # Very short format "E05" (assumes Season 1)
    short_episode_only = r'\bE(\d+)\b'
    match = re.search(short_episode_only, description, re.IGNORECASE)
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
    # Remove everything after " - " (usually guest names or episode titles)
    normalized = re.sub(r'\s*-\s*.*$', '', normalized)
    return normalized.strip()

def classify_video(video_doc):
    """
    Classify a video as either a series episode or standalone movie.
    
    Returns:
    - ('series', series_name, season, episode) for series episodes
    - ('movie', None, None, None) for standalone videos
    """
    description = video_doc.get("description", "") or ""
    title = video_doc.get("title", "")
    
    # Try to extract season and episode from description
    season, episode = extract_season_episode(description)
    
    if season and episode:
        series_name = normalize_series_name(title)
        return 'series', series_name, season, episode
    else:
        return 'movie', None, None, None

def get_all_series(channel_filter=None):
    """
    Get all series with their episodes, optionally filtered by channel.
    
    NEW: Supports channel filtering via genre parameter
    """
    collection_name = find_video_collection()
    if not collection_name:
        return {}
    
    collection = db[collection_name]
    all_videos = list(collection.find({"msg_id": {"$exists": True}}, {"_id": 0}))
    
    series_dict = defaultdict(list)
    
    for video in all_videos:
        # NEW: Filter by channel if specified
        if channel_filter:
            title = video.get("title", "")
            description = video.get("description", "")
            video_channel = detect_channel(title, description)
            
            # Skip if doesn't match the filter
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
    
    # NEW: Detect channel for series description
    channel = None
    if latest:
        channel = detect_channel(latest.get('title', ''), latest.get('description', ''))
    
    # Build description with channel info
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
    stream_url = f"{SURF_TG_BASE_URL}/{chat_id_clean}/{filename_encoded}?id={msg_id}&hash={hash_value}"
    
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
    
    # NEW: Add channel info to description
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
    Handles catalog for series, movies, and TV catchup with search and channel filter support.
    
    Three catalog types:
    1. surftg_series - TV series only (no channel filter)
    2. surftg_movies - Movies without TV channel info (no channel filter)
    3. surftg_tv_catchup - ALL videos with channel genre filter
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
        
        # TV CATCHUP CATALOG - All videos with channel filter
        if catalog_id == "surftg_tv_catchup":
            # Build base query
            query = {"msg_id": {"$exists": True}}
            
            # Add search filter
            if search_query:
                query["$or"] = [
                    {"title": {"$regex": search_query, "$options": "i"}},
                    {"description": {"$regex": search_query, "$options": "i"}},
                ]
            
            # Get ALL videos (both series and movies)
            videos = list(collection.find(query, {"_id": 0}).skip(skip).limit(limit * 2).sort("msg_id", -1))
            
            for video in videos:
                # Apply channel filter if specified
                if genre_filter:
                    title = video.get("title", "")
                    description = video.get("description", "")
                    video_channel = detect_channel(title, description)
                    
                    if video_channel != genre_filter:
                        continue
                
                # Add to metas (as movie type for Stremio UI)
                metas.append(video_to_movie_meta(video))
                if len(metas) >= limit:
                    break
        
        # SERIES CATALOG - Series only, no channel filter
        elif catalog_type == "series" or catalog_id == "surftg_series":
            # Get all series (no channel filter for clean series catalog)
            series_dict = get_all_series(channel_filter=None)
            
            # Apply search filter if needed
            if search_query:
                series_dict = {
                    name: eps for name, eps in series_dict.items()
                    if search_query.lower() in name.lower()
                }
            
            # Convert to meta objects
            series_list = []
            for series_name, episodes in series_dict.items():
                series_list.append(get_series_meta(series_name, episodes))
            
            # Sort and paginate
            series_list.sort(key=lambda x: x['name'])
            metas = series_list[skip:skip+limit]
        
        # MOVIES CATALOG - Standalone movies only, no channel filter
        else:
            # Build base query
            query = {"msg_id": {"$exists": True}}
            
            # Add search filter
            if search_query:
                query["$or"] = [
                    {"title": {"$regex": search_query, "$options": "i"}},
                    {"description": {"$regex": search_query, "$options": "i"}},
                ]
            
            # Get videos
            videos = list(collection.find(query, {"_id": 0}).skip(skip).limit(limit * 2).sort("msg_id", -1))
            
            # Filter: Only movies (no series episodes, no channel filtering)
            for video in videos:
                vid_type, _, _, _ = classify_video(video)
                if vid_type == 'movie':
                    metas.append(video_to_movie_meta(video))
                    if len(metas) >= limit:
                        break
        
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
        
        # NEW: Add channel info to stream title
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
    print("ISRAEL TV SERIES & MOVIES ADDON WITH CHANNEL FILTERS")
    print("=" * 60)
    
    # Show configured channels
    print("\n📺 Configured Channels:")
    for channel, keywords in TV_CHANNELS.items():
        print(f"   {channel}: {', '.join(keywords[:3])}")
    
    print("\nTesting database connection...")
    collection_name = find_video_collection()
    if collection_name:
        series_dict = get_all_series()
        print(f"✅ Found {len(series_dict)} series")
        
        # Show channel distribution
        channel_counts = defaultdict(int)
        collection = db[collection_name]
        sample_videos = list(collection.find({"msg_id": {"$exists": True}}, {"_id": 0}).limit(100))
        
        for video in sample_videos:
            title = video.get("title", "")
            description = video.get("description", "")
            channel = detect_channel(title, description)
            if channel:
                channel_counts[channel] += 1
        
        if channel_counts:
            print("\n📡 Videos per channel (from sample):")
            for ch, count in sorted(channel_counts.items()):
                print(f"   {ch}: {count} videos")
        else:
            print("\n⚠️  No channel keywords found in filenames!")
            print("   Update TV_CHANNELS in addon.py with your actual keywords")
    
    print("=" * 60 + "\n")
    
    port = 7000
    ssl_cert = "cert.pem"
    ssl_key = "key.pem"
    
    if os.path.exists(ssl_cert) and os.path.exists(ssl_key):
        print(f"Starting addon with HTTPS on https://0.0.0.0:{port}")
        print(f"Install URL: https://192.168.7.8:{port}/manifest.json\n")
        uvicorn.run(app, host="0.0.0.0", port=port, ssl_keyfile=ssl_key, ssl_certfile=ssl_cert)
    else:
        print(f"Starting addon with HTTP on http://0.0.0.0:{port}")
        print(f"Install URL: http://127.0.0.1:{port}/manifest.json\n")
        uvicorn.run(app, host="0.0.0.0", port=port)