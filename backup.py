async def proxy_thumb321(request):
    """Forward thumbnail requests to the Surf-TG backend"""
    chat_id = request.path_params.get('chat_id')
    msg_id = request.query_params.get('id')
    
    # Construct the internal URL to your Surf-TG backend
    # We use 127.0.0.1 because Surf-TG is running on the same machine
    internal_thumb_url = f"http://127.0.0.1:8080/api/thumb/{chat_id}?id={msg_id}"
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(internal_thumb_url)  
            return Response(content=resp.content, media_type=resp.headers.get("content-type"))
        except Exception as e:
            print(f"Error fetching thumb: {e}")
            return JSONResponse({"error": "Thumbnail not found"}, status_code=404)


async def get_tmdb_poster(title):
    """
    Helper: Searches TMDB for a poster using the Hebrew title.
    Returns None if not found.
    """
    if not TMDB_API_KEY or not title:
        return None
        
    # Clean the title for better search results
    clean_title = re.sub(r'\.(mkv|mp4|avi|mov|ts)$', '', title, flags=re.IGNORECASE)
    clean_title = re.sub(r'\(.*?\)|\[.*?\]', '', clean_title).strip()
    
    # Search with Hebrew preference
    url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={quote(clean_title)}&language=he-IL"
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, timeout=3.0)
            data = response.json()
            if data.get('results'):
                # Return the first result that has a poster
                for res in data['results']:
                    if res.get('poster_path'):
                        return f"https://image.tmdb.org/t/p/w500{res['poster_path']}"
        except Exception as e:
            print(f"TMDB Fetch Error: {e}")
            pass
    return None


"""

            # Clean the title for better search results
        clean_title = re.sub(r'\b(1080p|720p|WEB-?DL|HDTV|WEB|x264|x265|HEVC)\b', '', title, flags=re.IGNORECASE)
        groups = ['זירה מדיה', 'ז\.מ', 'דב סרטים', 'שלמה סרטים', 'תוצרת קוריאה']
        for group in groups:
            clean_title = re.sub(group, '', clean_title)
        # 3. Extract Season/Episode info but remove it from the title search
        # Matches: ע1 פ1, עונה 1, פרק 1
        clean_title = re.sub(r'ע(ונה)?\s?\d+', '', clean_title)
        clean_title = re.sub(r'פ(רק)?\s?\d+', '', clean_title)
        
        # 4. Remove dashes, quotes and extra spaces
        clean_title = clean_title.replace('"', '').replace('-', ' ').strip()
        
        # 5. Clean up multiple spaces
        clean_title = re.sub(r'\s+', ' ', clean_title)
        
        # Search with Hebrew preference
        url = f"https://api.themoviedb.org/3/search/multi?api_key={TMDB_API_KEY}&query={quote(clean_title)}&language=he-IL"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, timeout=3.0)
                data = response.json()
                if data.get('results'):
                    # Return the first result that has a poster
                    for res in data['results']:
                        if res.get('poster_path'):
                            poster_url = f"https://image.tmdb.org/t/p/w500{res['poster_path']}"
            except Exception as e:
                print(f"TMDB Fetch Error: {e}")
                pass
            
            """