# 5. HTTPS and DNS

TVs and phones need a **publicly trusted certificate** on a normal hostname. Self-signed certificates and bare IPs usually fail on TV apps.

## Get a hostname
Options (pick one):
1. **A domain you already own**: add an `A` record: `media.yourdomain.tld → SERVER_IPv4`.
2. **A free dynamic-DNS subdomain** from a reputable free service, pointing to the server IP.
3. **A wildcard-DNS helper** that turns an IP into a hostname (for example `203-0-113-10.example-service` style names). It works for testing, but shared services can hit certificate rate limits. Prefer 1 or 2 for the long term.

Do not buy a domain just for this unless you want to: any of the above works.

Check it resolves **before** starting Caddy:
```bash
dig +short media.yourdomain.tld
```
It must print your server's IP. If you also add an `AAAA` record, make sure the server's IPv6 is reachable on 80/443, or omit it.

## Firewall
UFW already allows 80/tcp (certificate challenge), 443/tcp and 443/udp (HTTP/3). Port 8096 stays closed.

## Start Caddy
Set `JELLYFIN_DOMAIN` in `/opt/jellyfin/.env` and run `sudo docker compose up -d`. Caddy requests and renews certificates automatically; certificates live in the `caddy_data` Docker volume (do not delete it).

Check:
```bash
curl -I https://media.yourdomain.tld/health      # HTTP/2 200
echo | openssl s_client -servername media.yourdomain.tld -connect media.yourdomain.tld:443 2>/dev/null | openssl x509 -noout -dates
```

## Tell Jellyfin about the proxy
Dashboard → **Networking**:
- **Known proxies**: add the Caddy container's address on the Docker network, or the network's subnet. Find it with:
  ```bash
  sudo docker network inspect jellyfin_web --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'
  ```
  Without this, Jellyfin sees every request as coming from the proxy and logs/limits by the wrong IP.
- **Allow remote connections to this server**: on.
- Leave **Enable automatic port mapping (UPnP)** off. Do not enable "Require HTTPS" on Jellyfin itself: TLS ends at Caddy.

## Hardening the public door
- Admin account: long unique password, used rarely. Everyone else gets a normal account ([next page](06-configure-jellyfin.md)).
- Consider Dashboard → Users → each user → "Login attempts before lockout".
- Optionally restrict who can reach the admin pages by IP at the proxy layer once you know your home IP. Skip this if your IP changes often.
- Never expose the Docker socket, SSH on a custom open port to the world with passwords, or any dashboard without auth.

## Renewal / troubleshooting
- `sudo docker compose logs caddy | tail -50` shows ACME errors (usually DNS not pointing to the server, or port 80 blocked).
- Let's Encrypt has rate limits: don't loop `docker compose up` while DNS is wrong.
