from os.path import splitext
import re
import os
from urllib.parse import quote
from bot.config import Telegram
from bot.helper.database import Database
from bot.telegram import StreamBot, UserBot
from bot.helper.file_size import get_readable_file_size
from bot.helper.cache import get_cache, save_cache
from asyncio import gather


db = Database()

async def fetch_message(chat_id, message_id):
    try:
        message = await StreamBot.get_messages(chat_id, message_id)
        return message
    except Exception as e:
        return None

def clean_hebrew_title(text):
    # 1. Remove common English technical tags
    text = re.sub(r'\b(1080p|720p|WEB-?DL|HDTV|WEB|x264|x265|HEVC)\b', '', text, flags=re.IGNORECASE)
    
    # 2. Remove Hebrew Uploader/Group tags (specific to your list)
    groups = [r'זירה מדיה', r'ז\.מ', r'דב סרטים', r'שלמה סרטים', r'תוצרת קוריאה']
    for group in groups:
        text = re.sub(group, '', text)
        
    # 3. Extract Season/Episode info but remove it from the title search
    # Matches: ע1 פ1, עונה 1, פרק 1
    text = re.sub(r'ע(ונה)?\s?\d+', '', text)
    text = re.sub(r'פ(רק)?\s?\d+', '', text)
    
    # 4. Remove dashes, quotes and extra spaces
    text = text.replace('"', '').replace('-', ' ').strip()
    
    # 5. Clean up multiple spaces
    text = re.sub(r'\s+', ' ', text)
    
    return text


async def get_messages(chat_id, first_message_id, last_message_id, batch_size=50):
    messages = []
    current_message_id = first_message_id
    while current_message_id <= last_message_id:
        batch_message_ids = list(range(current_message_id, min(current_message_id + batch_size, last_message_id + 1)))
        tasks = [fetch_message(chat_id, message_id) for message_id in batch_message_ids]
        batch_messages = await gather(*tasks)
        for message in batch_messages:
            if message:
                if file := message.video or message.document:
                #title = file.file_name or message.caption or file.file_id
                #title, _ = splitext(title)
                #title = re.sub(r'[.,|_\',]', ' ', title)
                    raw_fn = file.file_name or ""
                    is_default = any(x in raw_fn.lower() for x in ["default_name", "default name", "undefined"])

                    title = None

                    if not is_default and raw_fn:
                        # Priority A: Original File Name (if it's not generic)
                        title, _ = splitext(raw_fn)
                        title = clean_hebrew_title(title)
                        title = title[:30]
                        print(f"Using File Name: {title}", flush=True)
                    elif message.caption:
                        # Priority B: Hebrew Caption (limited to 30 chars)
                        clean_caption = message.caption.strip().split('\n')[0] # Take first line only
                        clean_caption = clean_hebrew_title(clean_caption)
                        title = clean_caption[:30]
                        print(f"Using Caption Snippet: {title}", flush=True)
                         # 2. Last resort fallback if both above failed

                    if not title:
                        title = f"Video {message.id}"

                        # 4. Clean symbols but preserve Hebrew characters
                    title = re.sub(r'[|_*`]', ' ', title)
                    title = re.sub(r'\s+', ' ', title).strip()

                    if message.caption: 
                        full_desc = str(message.caption)
                    else:
                        full_desc = str(raw_fn)
                    
                    has_real_thumb = hasattr(file, 'thumbs') and file.thumbs
                    if has_real_thumb:
                        # Use the local proxy URL for real Telegram thumbs
                        poster_url = f"/api/thumb/{str(chat_id).replace('-100', '')}?id={message.id}"
                    else:
                        # Fallback to Hebrew Placeholder
                        clean_t = quote(title)
                        poster_url = f"https://placehold.jp/40/1a1a2e/ffffff/600x900.png?text={clean_t}"

                    messages.append({"msg_id": message.id, "title": title, "description": full_desc,
                                "hash": file.file_unique_id[:6], "size": get_readable_file_size(file.file_size),
                                "type": file.mime_type, "chat_id": str(chat_id), "img": poster_url})

        current_message_id += batch_size
    return messages


async def get_files(chat_id, page=1):
    if Telegram.SESSION_STRING == '':
        return await db.list_tgfiles(id=chat_id, page=page)
    if cache := get_cache(chat_id, int(page)):
        return cache
    posts = []
    async for post in UserBot.get_chat_history(chat_id=int(chat_id), limit=50, offset=(int(page) - 1) * 50):
        file = post.video or post.document
        if not file:
            continue
        raw_fn = file.file_name or ""
        is_default = any(x in raw_fn.lower() for x in ["default_name", "default name", "undefined"])
        title = None
        if not is_default and raw_fn:
        # Priority A: Original File Name (if it's not generic)
            title, _ = splitext(raw_fn)
            title = clean_hebrew_title(title)
            title = title[:30]
            print(f"Using File Name: {title}", flush=True)
        elif post.caption:
            # Priority B: Hebrew Caption (limited to 30 chars)
            clean_caption = post.caption.strip().split('\n')[0] # Take first line only
            if len(clean_caption) > 30:
                clean_caption = clean_hebrew_title(clean_caption)
                title = clean_caption[:30].rsplit(' ', 1)[0] + "..."
            else:
                clean_caption = clean_hebrew_title(clean_caption)
                title = clean_caption
            print(f"Using Caption Snippet: {title}", flush=True)
        # 2. Last resort fallback if both above failed
        if not title:
            title = f"Video {post.id}"
        # 4. Clean symbols but preserve Hebrew characters
        title = re.sub(r'[|_*`]', ' ', title)
        title = re.sub(r'\s+', ' ', title).strip()
        title = clean_hebrew_title(title)
        full_desc = str(post.caption) if post.caption else ""
        has_real_thumb = hasattr(post, 'thumbs') and post.thumbs
        if has_real_thumb:
            # Use the local proxy URL for real Telegram thumbs
            poster_url = f"/api/thumb/{str(chat_id).replace('-100', '')}?id={post.id}"
        else:
            # Fallback to Hebrew Placeholder
            clean_t = quote(title)
            poster_url = f"https://placehold.jp/40/1a1a2e/ffffff/600x900.png?text={clean_t}"
        print(f"File title before saved from index.py: {title}", flush=True)
        posts.append({"msg_id": post.id, "chat_id": str(chat_id), "title": title, "description":full_desc,
            "hash": file.file_unique_id[:6], "size": get_readable_file_size(file.file_size), "type": file.mime_type, "img": poster_url})
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

    return ''.join(phtml.format(chat_id=str(chat_id).replace("-100", ""), id=post["msg_id"], img=f"/api/thumb/{chat_id}?id={post['msg_id']}", title=post["title"], hash=post["hash"], size=post['size'], type=post['type']) for post in posts)

