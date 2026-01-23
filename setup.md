### Domains:

##### Server surf-tg https URL: https://comm-coaching-thesis-forgot.trycloudflare.com // Or https domain from cloudflared tunnel port 8080
##### Addon Stremio https URL: https://print-config-katie-convicted.trycloudflare.com // Or https domain from cloudflared tunnel port 7000
##### Addon installtion URL: https://print-config-katie-convicted.trycloudflare.com/manifest.json // Replace with your domain 

### Commands to run:
##### **Server:** docker compose up -d / docker compose up --build --force-recreate **//after changes**
##### **Addon:** python addon.py

### Cloudflare Tunnels:
##### cloudflared tunnel --url http://localhost:7000
##### cloudflared tunnel --url http://localhost:8080
