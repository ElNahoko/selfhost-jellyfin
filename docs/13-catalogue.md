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
| `deploy/uploads/meta/news.py`, `news.html` | the News section: stories from publishers' feeds, sections, story pages, related stories |
| `deploy/uploads/meta/extras.py` | News posts (`data/news.json`) and Contact messages (`data/messages.db`) |
| `deploy/uploads/assets/index.html` | the whole web app (HTML, CSS and JavaScript in one file) |
| `deploy/uploads/assets/pages/` | About, Privacy, Contact, and the News page template (posts are written in by meta) |
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
| `title?id=[&lang=]`, `titles?ids=` | everyone | one or several titles (favorites); with `lang`, its name and description in that language |
| `episodes?id=`, `cast?id=`, `trailer?title=&year=&kind=` | everyone | details of a title |
| `filters?type=` | everyone | countries (platforms for games), genres, decades |
| `lucky?type=movie\|series` | everyone | one good pick (none for games) |
| `rimg?u=&w=` | everyone | a poster or cover, from the disk cache (only TMDB, TVmaze and Wikimedia addresses) |
| `favorites` (GET, POST) | members | the member's favorites |
| `requests` (GET; POST to add or vote) | members see it; approved members add and vote | the wishlist |
| `me` | everyone | who is looking (public, member, uploader, admin) |
| `person?id=nm…&lang=` | everyone | a person: portrait, dates, short bio (Wikidata + Wikipedia), best-known titles, filmography in the catalogue |
| `related?id=` | everyone | "More like this" and "More from the director / creator" (games: the same series) |
| `news` (GET), `contact` (POST) | everyone | the news posts; a contact message (spam trap, 5 an hour per address) |
| `status`, `admin/rebuild`, `members`, `users`, `usage`, `stats` | admin | the admin panels |
| `news` (POST, DELETE), `messages` | admin | write and delete posts; read and delete contact messages |

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

## News and Contact

`/news` is a news section like the big film sites have: Movies, Series, Anime, Games, and Nahoko (our own updates).
Stories come from the publishers' own RSS feeds (Variety, /Film, Screen Rant, IndieWire, TVLine, Anime News Network,
Anime Corner, IGN, GameSpot, Polygon, Eurogamer), read every 30 minutes and kept 45 days. Only the headline, the short
summary the feed carries and the picture are kept; every card and story page names the source and links to the full article.
A story page adds "In the catalogue" (titles quoted in the headline that Nahoko has, linked to `/title/<id>`) and related
stories. Pictures come from the feed or the article's preview picture (`og:image`), stored once and served by `/_meta/nimg/<id>`.

Our own posts are written in Settings → News. `/contact` sends a message to Settings → Messages; set `CONTACT_TO`
in `uploads-meta.env` to also receive each message by email. Every page carries the IMDb credit
("Information courtesy of IMDb (imdb.com). Used with permission."), as the IMDb datasets licence asks.

## Navigation

A title's details end with "More like this" (same genres, then the same country and a close year) and "More from" its
director (films) or creator (series); a game shows other games of its series. Genres in the details open the catalogue
filtered by that genre. "See all" pages show 48 titles a page with numbered pages (`?page=` in the address), and a search
stays when switching between Movies, Series and Games.

## Search engines and AI assistants

`/robots.txt`, `/sitemap.xml` (pages, news stories, the 60,000 best-known title pages in files of 10,000) and `/llms.txt`
(what the site is, for AI assistants) are made by `news.py`. Title pages (`/title/<id>-<name>`) carry Movie / TVSeries /
VideoGame data with the rating; story pages carry NewsArticle data (headline, picture, date, the publisher as author and
source, the titles it is about) and breadcrumbs. Every story ends with "Background" (Nahoko's own text about the titles it
names, from the catalogue) and picks that fit its subject.

## People pages

Every actor, director and writer of a catalogue title has a page at `/people/<IMDb id>-<name>` (cast cards and crew names
on title pages link to it). What they made comes from `cast.db` (IMDb principals: the first 10 billed actors, directors and
writers of each catalogue title), so the filmography is limited to the catalogue. Portrait: TMDB through Jellyfin, TVmaze, else
Wikidata/Commons. Birth, death, place, jobs and a short biography come from Wikidata and the Wikipedia summary of the page's
language (CC BY-SA, linked), kept 30 days in `titles.db` (`pinfo`). The sitemaps list the 30,000 best-known people
(`sitemap-people-N.xml`); each page carries schema.org Person data and breadcrumbs.

## Title pages

A short wide cover (the series' TVmaze background, else a frame of the trailer), breadcrumbs (Home › Movies › title), the cast
as cards, and a slim bar that follows the page once its header has scrolled away (poster, name, Trailer, Save). On series pages
the episode chart stays pinned under that bar.

## Languages

English, French, Spanish, German, Italian, Portuguese and Arabic (right to left). `/fr/...` (and `/es/`, `/de/`, `/it/`, `/pt/`,
`/ar/`) serves the same page in that language; the choice in the footer is remembered (cookie `lang`), and plain links bring
the visitor back to their language. Interface words live in `assets/i18n/strings.tsv` (one row per English text, one column
per language; `{x}` stands for a name or number); the server sends the page's language with it and the page translates what it
shows. Names of titles and people are never translated. A title's own name and description come from TMDB when `TMDB_KEY`
is set in `uploads-meta.env` (a free key from themoviedb.org), else from Wikidata and Wikipedia; kept 60 days (`tl` in
`titles.db`). Every page lists its other languages (`hreflang`) for search engines. News stories stay in their original English.

`/methodology` ("Who we are and how it works") explains the sources, updates, which titles get in, what the ratings mean
and how to report an error.

## Addresses

One address per page, the same words as the breadcrumbs: `/movies`, `/movies/top-rated` (a shelf by its name),
`/movies/tt0111161-the-shawshank-redemption`, `/series/…`, `/games/wg…`, `/people/nm0000151-morgan-freeman`, `/favorites`,
`/wishlist`. Other languages use their own words and the local name of the title: `/fr/films/tt0111161-les-evades`,
`/es/peliculas/…`, `/de/filme/…`. Any other spelling (old `/title/…`, `/person/…`, `/catalogue/…` links, a wrong name, the
wrong language's word) answers 301 to the right address. Staff keep `/catalogue/…` (their library uses `/movies/` folders).

## Search

Typing shows up to six titles and four people with their pictures (`/_meta/suggest`, answered from memory); Enter, or "See
all results", opens the full results with their own address (`?q=`), from any page.

## Speed

The page itself is a 3 KB shell: its styles, code and each language's words are files named by their content
(`/_app/<hash>.css|js`) that browsers keep for a year, so a visit after the first downloads almost nothing. In the
background, the 3,000 best-known people and the 600 best-known titles in every language are prepared in advance, so their
pages open at once. Nothing on a page is loaded from another site (pictures go through `/_meta/rimg` and are stored on
disk); the only exception is the trailer player.

## Favorites are the wishes

The public site offers nothing to watch and has no link to Jellyfin: no Watch button, no "I want it". Visitors keep favorites
(the heart); the admin and uploaders see the Wishlist tab, made of what members saved (most saved first, with who saved it)
plus any old requests. A round green check moves a title to Added (`want_done` in the request database).
Uploaders can delete leftover files (`.txt`, `.nfo`, `.url`, `.html`, pictures) but never a video or a folder (enforced in
`/auth/check`).

## Episodes

The rating chart scrolls with the page; a slim strip stays pinned under the title bar: one chip per season (or block of 25
episodes) with its average, and the season's numbers. Picking a season keeps the page height steady and brings its first
episode under the strip. Each episode shows its still (TVmaze, kept in `episodes.db` table `epimg`; the picture itself is
stored on disk on first view).

The About, How it works, Privacy and Contact pages exist in every language (`assets/pages/<lang>/`), built from the
English ones by `assets/pages/i18n_pages.py`.
