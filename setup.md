### Domains:

##### Server surf-tg https Url: https://comm-coaching-thesis-forgot.trycloudflare.com // Or https domain from cloudflared tunnel port 8080
##### Addon Stremio https Url: https://print-config-katie-convicted.trycloudflare.com // Or https domain from cloudflared tunnel port 7000
##### Addon installtion Url: https://print-config-katie-convicted.trycloudflare.com/manifest.json // replace for your domain 

### Commands to run:
##### server: docker compose up -d / docker compose up --build --force-recreate //after changes
##### addon: python addon.py

### cloudflare tunnels:
##### cloudflared tunnel --url http://localhost:7000
##### cloudflared tunnel --url http://localhost:8080
