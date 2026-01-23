"""
Surf-TG Stremio Addon - Complete Working Version
"""

from os import getenv

from requests import Response
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo import MongoClient
from urllib.parse import quote, unquote
import os
import re
import httpx

# ========== CONFIGURATION ==========
load_dotenv("config.env")
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is required")

MONGODB_URI = f"{DATABASE_URL}/surftg"
SURF_TG_BASE_URL = os.getenv("BASE_URL", "http://localhost:8080")
ADDON_NAME = os.getenv("ADDON_NAME", "My Hebrew Videos")
AUTH_CHANNEL = os.getenv("AUTH_CHANNEL", "")
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")

# Connect to MongoDB
print(f"Connecting to MongoDB...")
client = MongoClient(MONGODB_URI)
db = client.get_default_database()
print(f"Connected to database: {db.name}")

# Cache for collection name
_VIDEO_COLLECTION = None

# ========== STREMIO MANIFEST ==========
MANIFEST = {
    "id": "community.surftg.simple",
    "version": "1.0.1",
    "name": ADDON_NAME,
    "description": "Stream all your Telegram videos",
    "resources": ["catalog", "stream", "meta"],
    "types": ["movie"],
    "catalogs": [
        {
            "type": "movie",
            "id": "surftg_all_videos",
            "name": "Telegram Videos",
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
        "files",
        "media",
        "messages",
        "telegram_files",
        "tg_files",
        "channel_files",
        "Videos",
        "videos",
        "file",
        "playlist",
    ]

    collections = db.list_collection_names()

    # Try exact matches first
    for name in possible_names:
        if name in collections:
            count = db[name].count_documents({})
            if count > 0:
                print(f"Found collection '{name}' with {count} documents")
                _VIDEO_COLLECTION = name
                return name

    # If no match, return the first non-empty collection
    for coll in collections:
        if not coll.startswith("system."):
            count = db[coll].count_documents({})
            if count > 0:
                print(f"Using collection '{coll}' with {count} documents")
                _VIDEO_COLLECTION = coll
                return coll

    return None


def get_all_videos(skip=0, limit=100):
    """Fetch all video files from MongoDB"""
    try:
        collection_name = find_video_collection()

        if not collection_name:
            print("No collection found!")
            return []

        collection = db[collection_name]

        # Get videos - ensure msg_id exists
        files = list(
            collection.find({"msg_id": {"$exists": True, "$ne": None}})
            .skip(skip)
            .limit(limit)
            .sort("_id", -1)
        )

        if files:
            print(f"Found {len(files)} files")

        return files

    except Exception as e:
        print(f"Error fetching videos: {e}")
        import traceback

        traceback.print_exc()
        return []


def generate_stream_url(video_doc):
    """Generate Surf-TG streaming URL for Stremio"""

    msg_id = video_doc.get("msg_id")
    chat_id = video_doc.get("chat_id", AUTH_CHANNEL)
    hash_value = video_doc.get("hash", "")
    title = video_doc.get("title", "video")

    # Remove -100 prefix from chat_id
    chat_id_clean = str(chat_id).replace("-100", "")

    # Extract/generate filename from title
    filename = title.replace(" ", "_")
    # Keep only safe characters
    filename = re.sub(r"[^\w\-_\.]", "_", filename)

    # Add extension if not present
    if not any(
        filename.lower().endswith(ext)
        for ext in [".mkv", ".mp4", ".avi", ".mov", ".webm"]
    ):
        video_type = video_doc.get("type", "video/x-matroska")
        if "matroska" in video_type or "mkv" in video_type:
            filename += ".mkv"
        elif "mp4" in video_type:
            filename += ".mp4"
        else:
            filename += ".mkv"

    # URL encode the filename
    filename_encoded = quote(filename, safe="")

    # Build the URL
    stream_url = f"{SURF_TG_BASE_URL}/{chat_id_clean}/{filename_encoded}?id={msg_id}&hash={hash_value}"

    return stream_url


def video_to_meta(video_doc):
    msg_id = str(video_doc.get("msg_id", ""))
    chat_id = str(video_doc.get("chat_id", AUTH_CHANNEL)).replace("-100", "")
    stremio_id = f"surftg_{msg_id}"

    title = video_doc.get("title", f"Video {msg_id}")
    caption = video_doc.get("description") or video_doc.get("caption") or ""

    # FIX: Ensure BASE_URL doesn't end with a slash to avoid // in URL
    img = video_doc.get("img")
    print(f"img url: {img}")
    if img == f"/api/thumb/{chat_id}?id={msg_id}":
        poster_url = f"{SURF_TG_BASE_URL}/api/thumb/-100{chat_id}?id={msg_id}"
        print(f"poster_url1: {poster_url}")
    else:
        # Clean the title for better search results
        clean_title = re.sub(
            r"\b(1080p|720p|WEB-?DL|HDTV|WEB|x264|x265|HEVC)\b",
            "",
            title,
            flags=re.IGNORECASE,
        )
        groups = ["זירה מדיה", "ז\.מ", "דב סרטים", "שלמה סרטים", "תוצרת קוריאה"]
        for group in groups:
            clean_title = re.sub(group, "", clean_title)
        # 3. Extract Season/Episode info but remove it from the title search
        # Matches: ע1 פ1, עונה 1, פרק 1
        clean_title = re.sub(r"ע(ונה)?\s?\d+", "", clean_title)
        clean_title = re.sub(r"פ(רק)?\s?\d+", "", clean_title)

        # 4. Remove dashes, quotes and extra spaces
        clean_title = clean_title.replace('"', "").replace("-", " ").strip()

        # 5. Clean up multiple spaces
        clean_title = re.sub(r"\s+", " ", clean_title)

        # Search with Hebrew preference
        url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={quote(clean_title)}&language=he-IL"
        print(url)
        if url:
            poster_url = url
        else:
            poster_url = video_doc.get("img")
        print(f"poster_url2: {poster_url}")

    # FALLBACK: If thumbnail fails, use a generic movie poster icon
    # Stremio sometimes requires a valid image extension like .jpg at the end
    # + "&format=jpg"
    return {
        "id": stremio_id,
        "type": "movie",
        "name": title,
        "poster": poster_url,  # Check if this URL opens in your browser!
        "description": f"{caption}\n\nSize: {video_doc.get('size', 'Unknown')}",
        "behaviorHints": {"defaultVideoId": stremio_id},
    }


# ========== STREMIO ROUTES ==========


async def manifest(request):
    """Return addon manifest"""
    print("Manifest requested")
    return JSONResponse(
        MANIFEST,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*",
            # ADD THIS LINE BELOW:
            "ngrok-skip-browser-warning": "true",
        },
    )


async def meta(request):
    video_id = request.path_params["id"].replace("surftg_", "")
    collection = db[find_video_collection()]

    # Try finding by string or int
    video_doc = collection.find_one({"msg_id": video_id}) or collection.find_one(
        {"msg_id": int(video_id) if video_id.isdigit() else None}
    )

    if not video_doc:
        return JSONResponse({"meta": {}})

    return JSONResponse(
        {"meta": video_to_meta(video_doc)}, headers={"Access-Control-Allow-Origin": "*"}
    )


async def catalog(request):
    """Handles both normal browsing and search"""
    try:
        # 1. FIX: Get search keyword from the 'extra' path parameter
        # Example URL: /catalog/movie/surftg_all_videos/search=avatar.json
        extra_str = request.path_params.get("extra", "")
        search_query = None

        if "search=" in extra_str:
            search_query = extra_str.replace("search=", "")
            # Decode URL (e.g., %20 to space)
            search_query = unquote(search_query)

        skip = int(request.query_params.get("skip", 0))
        collection_name = find_video_collection()
        collection = db[collection_name]

        if search_query:
            print(f"🔍 Searching for: {search_query}")
            query = {
                "$or": [
                    {"title": {"$regex": search_query, "$options": "i"}},
                    {"description": {"$regex": search_query, "$options": "i"}},
                    {"caption": {"$regex": search_query, "$options": "i"}},
                ]
            }
            cursor = collection.find(query).skip(skip).limit(50)
        else:
            print("📂 Loading main catalog")
            cursor = (
                collection.find({"msg_id": {"$exists": True}})
                .sort("_id", -1)
                .skip(skip)
                .limit(100)
            )

        videos = list(cursor)
        metas = [video_to_meta(v) for v in videos]

        return JSONResponse(
            {"metas": metas, "moreAvailable": len(metas) >= 50},
            headers={
                "Access-Control-Allow-Origin": "*",
                "ngrok-skip-browser-warning": "true",
            },
        )

    except Exception as e:
        print(f"❌ Catalog Error: {e}")
        return JSONResponse({"metas": []})


async def stream(request):
    """Provide stream for a specific video"""
    try:
        video_type = request.path_params["type"]
        video_id = request.path_params["id"]

        print(f"\n{'='*60}")
        print(f"STREAM REQUEST")
        print(f"Type: {video_type}")
        print(f"ID: {video_id}")
        print(f"{'='*60}")

        # Extract msg_id from stremio ID
        if video_id.startswith("surftg_"):
            msg_id_str = video_id.replace("surftg_", "")
        else:
            msg_id_str = video_id

        print(f"Looking for msg_id: '{msg_id_str}'")

        # Find collection
        collection_name = find_video_collection()
        if not collection_name:
            print("❌ No collection found")
            return JSONResponse({"streams": []})

        collection = db[collection_name]

        # Find video - try string first, then integer
        video_doc = collection.find_one({"msg_id": msg_id_str})

        # Try as integer if string search failed
        if not video_doc:
            print(f"Not found as string, trying as integer...")
            try:
                msg_id_int = int(msg_id_str)
                video_doc = collection.find_one({"msg_id": msg_id_int})
                if video_doc:
                    print(f"✅ Found as integer: {msg_id_int}")
            except ValueError:
                pass

        if not video_doc:
            print(f"❌ Video not found with msg_id: {msg_id_str}")
            return JSONResponse({"streams": []})

        # Found the video!
        print(f"✅ Found video: {video_doc.get('title')}")
        print(f"   msg_id: {video_doc.get('msg_id')}")
        print(f"   chat_id: {video_doc.get('chat_id')}")
        print(f"   hash: {video_doc.get('hash')}")

        # Generate stream URL
        stream_url = generate_stream_url(video_doc)
        print(f"\n🎬 Stream URL: {stream_url}")
        print(f"{'='*60}\n")

        # Get metadata
        title = video_doc.get("title", "Unknown Video")
        if title == "Default Name":
            size = video_doc.get("size", "")
            title = f"Video {video_doc.get('msg_id')} ({size})"
        size = video_doc.get("size", "")

        # Return stream
        return JSONResponse(
            {
                "streams": [
                    {
                        "url": stream_url,
                        "title": f"📺 Surf-TG\n{title}\n💾 {size}",
                        "name": "Surf-TG",
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
        print(f"\n❌ ERROR in stream: {e}")
        import traceback

        traceback.print_exc()
        return JSONResponse({"streams": []})


# ========== APP SETUP ==========

routes = [
    Route("/manifest.json", manifest),
    Route("/manifest.json/{type}/surftg_all_videos", catalog),
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

    # Test connection on startup
    print("\n" + "=" * 50)
    print("Testing database connection...")
    videos = get_all_videos(limit=3)
    if videos:
        print(f"✅ Found {len(videos)} videos!")
        for v in videos:
            print(f"   - {v.get('title')} (msg_id: {v.get('msg_id')})")
    else:
        print("⚠️  No videos found. Check your database!")
    print("=" * 50 + "\n")

    port = 7000

    # Check if SSL certificate exists
    import os.path

    ssl_cert = "cert.pem"
    ssl_key = "key.pem"

    if os.path.exists(ssl_cert) and os.path.exists(ssl_key):
        print(f"Starting addon with HTTPS on https://0.0.0.0:{port}")
        print(f"Install URL: https://192.168.7.8:{port}/manifest.json\n")
        uvicorn.run(
            app, host="0.0.0.0", port=port, ssl_keyfile=ssl_key, ssl_certfile=ssl_cert
        )
    else:
        print(f"Starting addon with HTTP on http://0.0.0.0:{port}")
        print(f"Install URL: http://127.0.0.1:{port}/manifest.json (localhost only)")
        print(f"⚠️  For remote access, generate SSL certificate:")
        print(
            f"   openssl req -x509 -newkey rsa:4096 -nodes -out cert.pem -keyout key.pem -days 365"
        )
        print(f"   Then restart the addon.\n")
        uvicorn.run(app, host="0.0.0.0", port=port)
