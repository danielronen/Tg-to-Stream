
import re
import os
import httpx
import asyncio
from dotenv import load_dotenv


load_dotenv("config.env")
SURF_TG_BASE_URL = os.getenv("BASE_URL", "")
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
_TMDB_CACHE = {}

tmdb_client = httpx.AsyncClient(
    timeout=httpx.Timeout(10.0, connect=5.0),
    limits=httpx.Limits(max_keepalive_connections=10, max_connections=20)
)

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
    
    
async def get_tmdb_ep_det(tmdb_id, season, ep, tmdb_api_key):
    ep_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{ep}"    
    ep_res = await tmdb_client.get(ep_url, params={"api_key": tmdb_api_key, "language": "he-IL"})
    episode_name = None
    overview = None
    thumbnail = None
    if ep_res.status_code == 200:
        ep_data = ep_res.json()
        ep_name = ep_data.get("name")
        ep_overview = ep_data.get("overview")
        ep_thumb = ep_data.get("still_path")
        if not ep_name.startswith(r"פרק"):
            episode_name = ep_name
        if ep_overview != "":
            overview = ep_overview
        if ep_thumb:
            thumbnail = ep_thumb
    return episode_name, overview, thumbnail
    
        
        
async def get_tmdb_ep_det2(tmdb_id, season, ep, tmdb_api_key, client):

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


async def main():
    result1, result2, result3, result4 = await get_tmdb_ep_det2(231289,1,7,TMDB_API_KEY,tmdb_client)
    print(f"result1: {result1}" )
    print(f"result2: {result2}")
    print(f"result3: {result3}")
    print(f"result4: {result4}")

if __name__ == "__main__":
    #title = input("חפש סרט/סדרה:\n")
    asyncio.run(main())
 