"""
@StreamBot.on_message(filters.chat([
    -1002655377990,
    
]) & (filters.document | filters.video))
async def forward_videos_to_target(bot:Client, message: Message):
    AUTH_CHANNEL = await db.get_variable("auth_channel")
    try:
        if message.video:
            await message.forward(AUTH_CHANNEL)
            print(f"✅ Forwarded video from {message.chat.title} to target channel")
        elif message.document:
            mime_type = message.document.mime_type or ""
            file_name = message.document.file.name or ""
            if mime_type.startswith("video/") or file_name.endswith(('.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv')):
                await message.forward(AUTH_CHANNEL)
                print(f"✅ Forwarded video document from {message.chat.title} to target channel")
                
    except Exception as e:
        print(f"❌ Error forwarding: {e}")            """