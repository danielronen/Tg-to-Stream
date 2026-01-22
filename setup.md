Server surf-tg https Url: https://dual-government-seeks-preparing.trycloudflare.com/
Addon Stremio https Url: https://spots-mba-universal-defence.trycloudflare.com/  //** Maybe not */
Addon installtion Url: https://spots-mba-universal-defence.trycloudflare.com/manifest.json

Commands to run:
server: docker compose up -d / docker compose up --build --force-recreate //after changes
addon: python addon.py

cloudflare tunnels:
cloudflared tunnel --url http://localhost:7000
cloudflared tunnel --url http://localhost:8080
