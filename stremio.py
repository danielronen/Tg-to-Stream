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

# Connect to MongoDB
print(f"Connecting to MongoDB...")
client = MongoClient(MONGODB_URI)
db = client.get_default_database()
print(f"Connected to database: {db.name}")

# Cache for collection name
_VIDEO_COLLECTION = None

# ========== STREMIO MANIFEST ==========

MANIFEST = {
    "id": "community.surftg.series",  
    "version": "2.0.0",  
    "name": ADDON_NAME,
    "description": "Stream your Telegram videos as Series and Movies", 
    "resources": ["catalog", "stream", "meta"],
    "types": ["series", "movie"],  
    "catalogs": [
        # Series catalog for TV shows
        {
            "type": "series",
            "id": "surftg_series",
            "name": "TV Series",
            "extra": [{"name": "search", "isRequired": False}],
        },
        # Movies catalog for standalone videos
        {
            "type": "movie",
            "id": "surftg_movies",
            "name": "Movies & Other",
            "extra": [{"name": "search", "isRequired": False}],
        }
    ],
    "idPrefixes": ["surftg_"],
    "behaviorHints": {"configurable": False, "configurationRequired": False},
}

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


# Handle episode-only numbers including short Hebrew format
def extract_season_episode(description):
    """
    Extract season and episode numbers from Hebrew or English descriptions.
    
    MODIFIED: Now handles episode-only cases (assumes Season 1) including short Hebrew
    
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
        return 1, int(match.group(1))  # Season 1, Episode X    
    # Very short Hebrew episode only "פ5" (assumes Season 1)
    hebrew_very_short_episode = r'פ(\d+)'
    match = re.search(hebrew_very_short_episode, description)
    if match:
        return 1, int(match.group(1))  # Season 1, Episode X
    # Episode only in English "Episode 5" or "Ep 5" (assumes Season 1)
    english_episode_only = r'(?:Episode|Ep\.?|EP)\s*(\d+)'
    match = re.search(english_episode_only, description, re.IGNORECASE)
    if match:
        return 1, int(match.group(1))  # Season 1, Episode X
    # Very short format "E05" (assumes Season 1)
    short_episode_only = r'\bE(\d+)\b'
    match = re.search(short_episode_only, description, re.IGNORECASE)
    if match:
        return 1, int(match.group(1))  # Season 1, Episode X
    # No season/episode found
    return None, None

# Generate safe ID from series name
def get_series_id(series_name):
    # Create hash from series name
    hash_object = hashlib.md5(series_name.encode('utf-8'))
    hash_hex = hash_object.hexdigest()[:12]  # Use first 12 characters
    return f"surftg_series_{hash_hex}"

def normalize_series_name(title):
    if not title:
        return "Unknown Series"
    normalized = title.strip()
    # Remove everything after " - " (usually guest names or episode titles)
    normalized = re.sub(r'\s*-\s*.*$', '', normalized)
    return normalized.strip()

# Determine if a video is a series episode or movie
def classify_video(video_doc):
    """
    Classify a video as either a series episode or standalone movie.
    
    Logic:
    - If description contains season/episode info → It's a series episode
    - Otherwise → It's a standalone movie/video
    
    Returns:
    - ('series', series_name, season, episode) for series episodes
    - ('movie', None, None, None) for standalone videos
    """
    description = video_doc.get("description", "") or ""
    title = video_doc.get("title", "")
    
    # Try to extract season and episode from description
    season, episode = extract_season_episode(description)
    
    if season and episode:
        # Has S/E info → It's a series episode
        series_name = normalize_series_name(title)
        return 'series', series_name, season, episode
    else:
        # No S/E info → It's a movie or standalone video
        return 'movie', None, None, None


# Get all series with their episodes
def get_all_series():
    collection_name = find_video_collection()
    if not collection_name:
        return {}
    
    collection = db[collection_name]
    # FIX: Exclude _id field to avoid ObjectId serialization issues
    all_videos = list(collection.find({"msg_id": {"$exists": True}}, {"_id": 0}))
    
    # defaultdict automatically creates empty list for new keys
    series_dict = defaultdict(list)
    
    # Classify and group each video
    for video in all_videos:
        vid_type, series_name, season, episode = classify_video(video)
        
        if vid_type == 'series':
            # Add this episode to the series
            series_dict[series_name].append({
                'video': video,
                'season': season,
                'episode': episode
            })
    
    return dict(series_dict)


def get_series_meta(series_name, episodes_list):
    """
    Create series metadata object for Stremio.
    
    FIXED: Only returns fields that Stremio expects
    """
    # Sort episodes to get the latest one (for poster)
    sorted_eps = sorted(episodes_list, key=lambda x: (x['season'], x['episode']))
    latest = sorted_eps[0]['video'] if sorted_eps else None
    
    # Count unique seasons
    seasons = set(ep['season'] for ep in episodes_list)
    total_episodes = len(episodes_list)
    
    # Use poster from the latest episode
    poster_url = latest.get('img') if latest else None
    background_url = latest.get('background') if latest else None
    
    # Build description showing season and episode counts
    description = f"📺 {len(seasons)} Season(s) • {total_episodes} Episodes"
    
    # Create a simple hash-based ID that's consistent
    series_hash = abs(hash(series_name))
    series_id = f"surftg_series_{series_hash:x}"
    
    return {
        "id": series_id,
        "type": "series",
        "name": series_name,
        "poster": poster_url,
        "description": description,
        "background": background_url
        # DO NOT include: videos, _series_name, or any other custom fields here
        # These will be added in the meta() endpoint
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
    msg_id = str(video_doc.get("msg_id", ""))
    stremio_id = f"surftg_movie_{msg_id}"  # CHANGED: Added "movie_" prefix
    title = video_doc.get("title", f"Video {msg_id}")
    description = video_doc.get("description") or ""
    size = video_doc.get("size", "Unknown")
    poster_url = video_doc.get("img")
    background_url = video_doc.get("background")
    
    return {
        "id": stremio_id,
        "type": "movie",
        "name": title,
        "poster": poster_url,
        "description": f"{description}\n\n💾 Size: {size}",
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
    Handles catalog for both series and movies with search support.
    
    MAJOR CHANGES:
    - Now detects catalog type (series vs movies)
    - Series catalog: Groups videos by series name
    - Movies catalog: Shows only standalone videos (no S/E info)
    - Search works for both catalog types
    """
    try:
        # Get catalog type from URL (series or movie)
        catalog_type = request.path_params.get("type", "series")
        catalog_id = request.path_params.get("id", "")
        extra_str = request.path_params.get("extra", "")
        
        # Extract search query if present
        search_query = None
        if "search=" in extra_str:
            search_query = unquote(extra_str.replace("search=", ""))
        
        skip = int(request.query_params.get("skip", 0))
        limit = 100
        
        collection_name = find_video_collection()
        collection = db[collection_name]
        
        metas = []
        
        # SERIES CATALOG: Show all TV series
        if catalog_type == "series" or catalog_id == "surftg_series":
            # Get all series grouped by name
            series_dict = get_all_series()
            
            # Filter by search if needed
            if search_query:
                series_dict = {
                    name: eps for name, eps in series_dict.items()
                    if search_query.lower() in name.lower()
                }
            
            # Convert each series to a meta object
            series_list = []
            for series_name, episodes in series_dict.items():
                series_list.append(get_series_meta(series_name, episodes))
            
            # Sort alphabetically and apply pagination
            series_list.sort(key=lambda x: x['name'])
            metas = series_list[skip:skip+limit]
            
        # MOVIES CATALOG: Show only standalone videos
        else:
            # Build query for videos
            query = {"msg_id": {"$exists": True}}
            
            # Add search filter if needed
            if search_query:
                query["$or"] = [
                    {"title": {"$regex": search_query, "$options": "i"}},
                    {"description": {"$regex": search_query, "$options": "i"}},
                ]
            
            # FIX: Exclude _id field to avoid ObjectId serialization issues
            videos = list(collection.find(query, {"_id": 0}).skip(skip).limit(limit).sort("msg_id", -1))
            
            # Filter: Only include videos WITHOUT season/episode info
            for video in videos:
                vid_type, _, _, _ = classify_video(video)
                if vid_type == 'movie':  # Only movies, skip series episodes
                    metas.append(video_to_movie_meta(video))
        
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
            # Get all series and find the matching one by ID
            series_dict = get_all_series()            
            matching_name = None            
            for series_name, episodes in series_dict.items():
                series_meta = get_series_meta(series_name, episodes)
                if series_meta["id"] == meta_id:
                    matching_name = series_name
                    break
            
            if not matching_name:
                return JSONResponse({"meta": {}})
                        
            # Get the episodes for this series
            episodes = series_dict[matching_name]            
            # Group episodes by season
            videos_by_season = defaultdict(list)
            for ep_data in episodes:
                videos_by_season[ep_data['season']].append(ep_data)
            # Build episode list for Stremio
            videos = []
            for season in sorted(videos_by_season.keys()):
                for ep_data in sorted(videos_by_season[season], key=lambda x: x['episode']):
                    video = ep_data['video']
                    episode_id = f"surftg_{video.get('msg_id')}"
                    
                    # FIXED: Only include fields with actual values
                    episode_obj = {
                        "id": episode_id,
                        "title": f"S{ep_data['season']:02d}E{ep_data['episode']:02d}",
                        "season": ep_data['season'],
                        "episode": ep_data['episode'],
                    }
                    # Only add optional fields if they have real values (not empty strings)
                    desc = video.get('description', '').strip()
                    if desc:
                        episode_obj["overview"] = desc
                    
                    thumb = video.get('background', '').strip()
                    if thumb and not thumb.startswith('https://placehold.jp'):  # Skip placeholder images
                        episode_obj["thumbnail"] = thumb
                    # DON'T include 'released' field at all if we don't have a date
                    videos.append(episode_obj)
            
            # Get base metadata
            series_meta = get_series_meta(matching_name, episodes)
            
            # FIXED: Only include fields with real values
            final_meta = {
                "id": series_meta["id"],
                "type": "series",
                "name": series_meta["name"],
                "videos": videos
            }
            
            # Only add poster if it exists and is not a placeholder
            poster = series_meta.get("poster", "").strip()
            if poster and not poster.startswith('https://placehold.jp'):
                final_meta["poster"] = poster
                
            # NEW: Add background if it exists and is not a placeholder
            background = series_meta.get("background", "").strip()
            if background and not background.startswith('https://placehold.jp'):
                final_meta["background"] = background
            
            # Only add description if it exists
            desc = series_meta.get("description", "").strip()
            if desc:
                final_meta["description"] = desc

            #import json
            response_obj = {"meta": final_meta}
            #print(f"📤 Clean response (no empty fields):")
            #print(json.dumps(response_obj, indent=2, ensure_ascii=False)[:1000])
            
            return JSONResponse(
                response_obj,
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
        # Extract msg_id from any ID format (handles movie_, series_, surftg_ prefixes)
        msg_id_str = video_id.replace("surftg_", "").replace("movie_", "").replace("series_", "")
        collection = db[find_video_collection()]
        # FIX: Exclude _id field to avoid ObjectId serialization issues
        # Find video by msg_id
        video_doc = collection.find_one({"msg_id": msg_id_str}, {"_id": 0})
        if not video_doc:
            # Try as integer if string fails
            try:
                msg_id_int = int(msg_id_str)
                video_doc = collection.find_one({"msg_id": msg_id_int}, {"_id": 0})
            except ValueError:
                pass
        
        if not video_doc:
            return JSONResponse({"streams": []})
        
        # Generate streaming URL
        stream_url = generate_stream_url(video_doc)
        # Get video metadata
        title = video_doc.get("title", "Unknown")
        size = video_doc.get("size", "")
        description = video_doc.get("description", "")
        # NEW: Extract season/episode for enhanced stream title
        season, episode = extract_season_episode(description)
        if season and episode:
            # Series episode: Show S02E03 format
            stream_title = f"📺 {title} | S{season:02d}E{episode:02d}\n💾 {size}"
        else:
            # Movie: Show regular title
            stream_title = f"📺 {title}\n💾 {size}"
        
        return JSONResponse(
            {
                "streams": [
                    {
                        "url": stream_url,
                        "title": stream_title,  # CHANGED: Now includes S/E info
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
    Route("/detail/{type}/{id}.json", meta),  # NEW: Add detail endpoint (same as meta)
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
    
    # CHANGED: Enhanced startup test to show series info
    print("\n" + "=" * 60)
    print("Testing database connection...")
    collection_name = find_video_collection()
    if collection_name:
        series_dict = get_all_series()
        print(f"✅ Found {len(series_dict)} series:")
        # Show first 5 series as examples
        for name, eps in list(series_dict.items())[:5]:
            print(f"   📺 {name}: {len(eps)} episodes")
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