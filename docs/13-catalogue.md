# 13 · The catalogue (Nahoko)

The public part of the site: films, series and games to browse, search, save as favorites and add to the wishlist.
It is a small Python server (no framework, no database server) plus one web page. Everything is free data, no API key except Jellyfin's own.

## Files

| File | Role |
|---|---|
| `deploy/uploads/meta/meta.py` | the HTTP server: pages, `/_meta/*` API, sign-in, wishlist, image proxy, short answer cache |
| `deploy/uploads/meta/catalog.py` | films and series: building the catalogue, shelves, filters, search, lucky pick, episodes, cast, countries |
| `deploy/uploads/meta/games.py` | games: list, covers, scores, shelves, filters, search |
| `deploy/uploads/meta/auth.py` | staff accounts, members (email codes), sessions, favorites |
| `deploy/uploads/meta/mail.py` | sends the sign-in code (SMTP, Resend) |
| `deploy/uploads/assets/index.html` | the whole web app (HTML, CSS and JavaScript in one file) |
| `deploy/uploads/assets/pages/` | About and Privacy |
| `deploy/uploads/assets/pwa/` | installable app: manifest, icons, service worker (offline screen only, nothing cached) |
| `deploy/uploads/files.caddy.example` | which paths Caddy sends to meta and which stay behind the staff sign-in |

## Where the data comes from

| Data | Source | How often |
|---|---|---|
| titles, years, genres, runtimes, ratings, votes | IMDb datasets (`datasets.imdbws.com`) | weekly |
| posters, plots | TMDB, asked through Jellyfin's own metadata search (by IMDb id) | when a title is first shown, plus a slow background pass |
| episodes and their ratings | IMDb datasets | weekly |
| air dates, runtimes, status | TVmaze | one series every 1.5 s, in the background |
| cast and directors | IMDb datasets; portraits from TVmaze, then TMDB through Jellyfin | weekly; portraits when a title is opened |
| anime | AniList (5,000 most popular) | weekly |
| national cinemas (French, Spanish, Italian, German, Japanese, Korean, Indian, Belgian, Brazilian, Scandinavian, Chinese, Iranian, Turkish) | Wikidata: original language **or** country of origin, never English-language | weekly, one query a minute |
| games (PC, PlayStation 1-5, Xbox, Switch, Wii, GameCube, N64, DS) | Wikidata (platforms, dates, genres, developer, popularity) + English Wikipedia (cover, description, Metacritic score from the review box) | weekly; covers downloaded once, politely |
| trailers | first YouTube results page, scored | when asked, kept a week |

### Who gets into the catalogue

A film needs 5,000 IMDb votes (1,000 before 1970, 2,000 for 1970-1989); a series 2,000 (600 before 1990).
Titles of the national cinemas enter with 1,000 (films) or 400 (series) votes, since they get far fewer IMDb votes than Hollywood.
Releases of the last year enter with 1,000 (films) or 400 (series). Animated titles need fewer and are kept only if AniList knows them.

A game is shown once its cover is stored on the server (so no tile is ever empty).

## Shelves

Each shelf is a rule over the titles (`_rules` in `catalog.py` and `games.py`): New and notable, Just released, Top rated, Popular,
Anime, Hidden gems, Mind-benders, Feel-good, Classics, the decades, genres… Shelves marked `home` appear on the start page, the others
under "More categories". "Top rated" uses a vote-weighted rating, so a 9.1 with 2,000 votes does not beat a classic with a million.

With a filter (country, genre, decade, rating) the shelves are computed again inside it: "Top rated" becomes French top rated, and
shelves for the genres that country is known for are added. Anime appears only for Japan.

On the games page every game appears once, and a shelf shows at most two games of the same series.

## The API (`/_meta/…`)

| Path | For | Answer |
|---|---|---|
| `catalog?type=movie\|series\|game[&row=][&country=&genre=&decade=&min=][&sort=&offset=&limit=]` | everyone | shelves, or one shelf page by page |
| `find?type=&q=` | everyone | search in the catalogue |
| `search?type=&q=` | everyone | search beyond it (TMDB through Jellyfin; not for games) |
| `title?id=`, `titles?ids=` | everyone | one or several titles (favorites) |
| `episodes?id=`, `cast?id=`, `trailer?title=&year=&kind=` | everyone | details of a title |
| `filters?type=` | everyone | countries (platforms for games), genres, decades |
| `lucky?type=movie\|series` | everyone | one good pick (none for games) |
| `rimg?u=&w=` | everyone | a poster or cover, from the disk cache (only TMDB, TVmaze and Wikimedia addresses) |
| `favorites` (GET, POST) | members | the member's favorites |
| `requests` (GET; POST to add or vote) | members see it; approved members add and vote | the wishlist |
| `me` | everyone | who is looking (public, member, uploader, admin) |
| `status`, `admin/rebuild`, `members`, `users`, `usage`, `stats` | admin | the admin panels |

IDs: films and series use IMDb ids (`tt…`), games `wg` + their Wikidata number.

## Speed

- The catalogue lives in memory; a page of shelves is computed in a few milliseconds.
- Answers that are the same for everyone (`catalog`, `filters`, `find`, `title`, `episodes`, `cast`, `search`) are kept 1-60 minutes.
- Every picture is fetched once, stored in `data/img`, and sent with a one-year cache; a missing picture answers 404 cached for an hour.
- Small answers leave in one packet (no Nagle delay), connections are kept alive between Caddy and meta.
- The page retries by itself if a load fails (for example during a restart).
- Background jobs run at low priority (`nice 10`) and respect the sources' rate limits (Wikidata: about one query a minute; Wikimedia images: waits on 429).

## Roles

| Role | Sees |
|---|---|
| visitor | the catalogue (films, series, games), About, Privacy |
| member (email code) | + favorites, the wishlist; adding to it and voting once the admin approves (Profiles → Members) |
| uploader | + the library and the upload page |
| admin | + delete, tidy, profiles, settings, catalogue data, what uses the server |

## Admin: Settings → Catalogue data

Totals (films, series, games, anime, posters), each country (in the catalogue, out of how many Wikidata knows), and one line per
background job with its progress. **Update now** starts that job right away; normally they run by themselves every week.

## Changing it

See `SERVER.md` in the project folder (next to this repository) for deploying. In short: `index.html` and the pages need no restart;
a `.py` change needs `docker restart uploads-meta` (check it compiles first). Bump `SCHEMA` in `catalog.py` to make the next start
rebuild the catalogue in the background (the old one keeps being served meanwhile).
