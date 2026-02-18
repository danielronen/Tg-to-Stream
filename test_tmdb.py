
import re
import os
import httpx
from dotenv import load_dotenv

load_dotenv("config.env")
SURF_TG_BASE_URL = os.getenv("BASE_URL", "")
TMDB_API_KEY = os.getenv("TMDB_API_KEY", "")
_TMDB_CACHE = {}



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


if __name__ == "__main__":
    title = input("חפש סרט/סדרה:\n")
    result = get_tmdb_details(title, TMDB_API_KEY)
    if result:
        print(result)
        res = result.get("origin_country")[0]=="IL"
        print(result.get("origin_country")[0])
        print(res)