# 🌊 SurfTG Stremio Addon

A powerful bridge that turns your Telegram media into a personal Stremio library. This project is fully containerized using Docker and secured via Cloudflare Tunnels.

---

## 🚀 Quick Start (Local Testing)

If you are running this on your personal PC for testing:

### 1. Configuration
Create a `config.env` file in the root directory and fill in your credentials:
```env
TELEGRAM_API_ID=your_id
TELEGRAM_API_HASH=your_hash
BOT_TOKEN=your_bot_token
BASE_URL=      # Leave empty for now
```
### 2. Start the Tunnel
Open a terminal and run the following to get a temporary public URL:
```env
cloudflared tunnel --url http://localhost:80
```

* Copy the https://...trycloudflare.com URL provided in the output.

* Paste it into your config.env as the BASE_URL.

### 3. Launch the Addon
Run the Docker container:
```env
docker compose up -d --build
```

### 4. Index & Insall
- Run /index command in the AUTH_CHANNEL chat to index new files
- Install the Addon on Stremio with: BASE_URL/manifest.json

