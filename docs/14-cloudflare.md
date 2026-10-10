# 14 · Cloudflare in front of the catalogue (free plan)

Cloudflare keeps copies of the pictures, the app files and the public pages in hundreds of places around the world, so a
visitor gets them from a server near them instead of from our one machine. Everything still comes from our server first:
Cloudflare only keeps copies. The **Free plan** is enough.

Below, `example.com` is the catalogue's domain and `jellyfin.example.com` the Jellyfin one.

## 1. Account and site

1. Create a free account on cloudflare.com, then **Add a domain** → `example.com` → **Free** plan.
2. Cloudflare reads your current DNS records. Check them:
   - `example.com` (A, the server's IPv4) → **Proxied** (orange cloud).
   - `jellyfin.example.com` → **DNS only** (grey cloud). Video must not go through Cloudflare: the free plan's terms forbid
     serving video through it, and it would add nothing.
   - Any other name you use for mail etc. → keep as it was (MX records are never proxied).
3. At the company where you bought the domain, replace its two nameservers with the two Cloudflare shows. It becomes
   **Active** within minutes to a few hours (Cloudflare emails you).

## 2. Settings (dashboard of the domain)

| Where | Setting |
|---|---|
| SSL/TLS → Overview | **Full (strict)** (Caddy already has real certificates) |
| SSL/TLS → Edge Certificates | Always Use HTTPS **on**, Minimum TLS 1.2, Automatic HTTPS Rewrites **on** |
| Speed → Optimization | Brotli **on**, Early Hints **on**, Rocket Loader **off** (it rewrites our scripts) |
| Network | HTTP/3 **on** |
| Caching → Configuration | Browser Cache TTL: **Respect Existing Headers** |
| Scrape Shield | Email Address Obfuscation **off** |

## 3. Cache rules (Caching → Cache Rules → Create rule, "Edit expression")

**Rule 1 · pictures and app files**, kept until they change (they are named by their content or never change):

```
(starts_with(http.request.uri.path, "/_meta/rimg")) or (starts_with(http.request.uri.path, "/_meta/nimg")) or (starts_with(http.request.uri.path, "/_app/")) or (starts_with(http.request.uri.path, "/pwa/"))
```
Cache eligibility: **Eligible for cache** · Edge TTL: **Use cache-control header if present** · Browser TTL: **Respect origin**.

**Rule 2 · public catalogue data** (the same for every visitor), 5 minutes:

```
(http.request.uri.path in {"/_meta/catalog" "/_meta/title" "/_meta/episodes" "/_meta/cast" "/_meta/related" "/_meta/person" "/_meta/suggest" "/_meta/find" "/_meta/filters" "/_meta/home" "/_meta/backdrop"}) and not (http.cookie contains "lm_s=")
```
Eligible for cache · Edge TTL: **Ignore cache-control header and use this TTL: 5 minutes** · Browser TTL: Respect origin.

**Rule 3 · public pages** (not for signed-in people or those who picked a language), 5 minutes:

```
(http.request.uri.path eq "/" or starts_with(http.request.uri.path, "/movies") or starts_with(http.request.uri.path, "/series") or starts_with(http.request.uri.path, "/games") or starts_with(http.request.uri.path, "/people") or starts_with(http.request.uri.path, "/news") or starts_with(http.request.uri.path, "/fr/") or starts_with(http.request.uri.path, "/es/") or starts_with(http.request.uri.path, "/de/") or starts_with(http.request.uri.path, "/it/") or starts_with(http.request.uri.path, "/pt/") or starts_with(http.request.uri.path, "/ar/")) and not (http.cookie contains "lm_s=") and not (http.cookie contains "lang=")
```
Eligible for cache · Edge TTL: **Ignore cache-control header and use this TTL: 5 minutes**, with **status code TTL**: 300–599 → **no cache** · Browser TTL: Respect origin.

The order of the rules does not matter (they cover different addresses).

## 4. On our server (already done in this repository)

- `deploy/Caddyfile` trusts Cloudflare's address ranges and reads `CF-Connecting-IP`, so the app still sees each visitor's
  real address (sign-in and contact limits use it). Update the ranges from cloudflare.com/ips if they ever change.
- Pictures (`/_meta/rimg`) and app files (`/_app/…`) are sent with a one-year `immutable` cache; the start page with 60 s.

## 5. Check

```
curl -sI https://example.com/ | grep -i "cf-cache-status\|server"
```
`server: cloudflare` means it is in front; `cf-cache-status: HIT` (on the second request) means the copy is used.
After a change of the site, a page is at most 5 minutes old; **Caching → Configuration → Purge Everything** refreshes at once.

## Limits of the free plan to know

- An upload request may be at most 100 MB: the upload page sends 16 MB pieces, so it is fine.
- A request that takes more than 100 seconds is cut. On a very slow connection, a 16 MB piece could; if uploads fail,
  give the upload page a grey-cloud name of its own.
