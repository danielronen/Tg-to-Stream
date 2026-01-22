"""
async def file_receive_handler2(bot: Client, message: Message):
    channel_id = message.chat.id
    AUTH_CHANNEL = await db.get_variable('auth_channel')
    if AUTH_CHANNEL is None or AUTH_CHANNEL.strip() == '':
        AUTH_CHANNEL = Telegram.AUTH_CHANNEL
    else:
        AUTH_CHANNEL = [channel.strip() for channel in AUTH_CHANNEL.split(",")]
    if str(channel_id) in AUTH_CHANNEL:
        try:
            file = message.video or message.document
            #title = file.file_name or message.caption or file.file_id
            #title, _ = splitext(title)
            #title = re.sub(r'[.,|_\',]', ' ', title)
            title = None
            # Priority 1: Caption (often has the best description)
            print(f"Caption:{message.caption}", flush=True)
            if message.caption and message.caption.strip():
                title = message.caption.strip()
                print(f"Title: {title}", flush=True)

            # Priority 2: File name
            print(f"File Name: {file.file_name}", flush=True)
            if not title and file.file_name:
                title = file.file_name
                title = title[:30]
                print(f"Title: {title}", flush=True)

                # Remove file extension
                title, _ = splitext(title)

            # Priority 3: Message text (if any)
            if not title and message.text:
                title = message.text.strip()

            # Priority 4: First line of caption/text if it's multi-line
            if not title and message.caption:
                title = message.caption.split('\n')[0].strip()

            # Priority 5: Use file_id with size as fallback
            if not title or title == "":
                size = get_readable_file_size(file.file_size)
                title = f"Video {msg_id} ({size})"

            # Clean the title - preserve Hebrew and Unicode
            # Only remove problematic characters
            title = re.sub(r'[|_-]', ' ', title)  # Keep dots, commas, quotes - only remove pipes and underscores
            title = re.sub(r'\s+', ' ', title)  # Replace multiple spaces with single space
            title = title.strip()

            # Limit title length to reasonable size (optional)
            if len(title) > 30:
                title = title[:30] 
                
            full_desc = message.caption.strip() if message.caption else "No description available."
            msg_id = message.id
            hash = file.file_unique_id[:6]
            size = get_readable_file_size(file.file_size)
            type = file.mime_type
            print(f"File title before saved from start.py: {title}", flush=True)
            await db.add_tgfiles(str(channel_id), msg_id, str(hash), str(title), str(full_desc), str(size), str(type))
        except FloodWait as e:
            LOGGER.info(f"Sleeping for {str(e.value)}s")
            await sleep(e.value)
            await message.reply(text=f"Got Floodwait of {str(e.value)}s",
                                disable_web_page_preview=True, parse_mode=ParseMode.MARKDOWN)
    else:
        await message.reply(text="Channel is not in AUTH_CHANNEL")

"""