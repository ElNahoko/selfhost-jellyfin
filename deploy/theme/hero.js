/* Netflix-style home page for Jellyfin: a full-width billboard plus rows of landscape thumbnails.
   Self-contained, no external requests. Reads items through the logged-in user's own ApiClient, so it only shows what
   that user may see. Loaded by the patched index.html (see docs/12-theme.md). MIT licensed, part of selfhost-jellyfin. */
(function () {
  "use strict";
  if (window.__jfHeroLoaded) return;
  window.__jfHeroLoaded = true;

  var COUNT = 6, INTERVAL = 10000, REFRESH_MS = 10 * 60 * 1000, MAX_ROWS = 7;
  var state = { items: [], idx: 0, timer: null, root: null, loadedAt: 0, loading: false };

  function client() { return window.ApiClient; }
  function userId() { var c = client(); return c && c.getCurrentUserId && c.getCurrentUserId(); }
  function img(id, type, tag, w) {
    return client().getUrl("Items/" + id + "/Images/" + type + "/0", { tag: tag, maxWidth: w, quality: 85 });
  }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function shuffle(a) { for (var i = a.length - 1; i > 0; i--) { var j = Math.floor(Math.random() * (i + 1)), t = a[i]; a[i] = a[j]; a[j] = t; } return a; }
  function runtime(ticks) { if (!ticks) return ""; var m = Math.round(ticks / 600000000); return m >= 60 ? Math.floor(m / 60) + "h " + (m % 60 ? (m % 60) + "min" : "") : m + " min"; }

  /* ---------- silent video previews (like the trailer on a streaming app's billboard) ----------
     Plays ~20 s of the real file, muted, straight from the server (no transcoding, no playback session). Only used when
     this browser can decode the file as it is (H.264 8-bit); everything else keeps the still picture. */
  var previewCache = {}, reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  function resolvePreview(it) {
    if (previewCache[it.Id] !== undefined) return Promise.resolve(previewCache[it.Id]);
    var c = client(), uid = userId();
    var get = it.Type === "Series"
      ? c.getItems(uid, { ParentId: it.Id, Recursive: true, IncludeItemTypes: "Episode", SortBy: "ParentIndexNumber,IndexNumber", Limit: 1, Fields: "MediaSources" }).then(function (r) { return r.Items && r.Items[0]; })
      : c.getItem(uid, it.Id);
    return get.then(function (item) {
      var ms = item && item.MediaSources && item.MediaSources[0];
      var v = ms && (ms.MediaStreams || []).filter(function (x) { return x.Type === "Video"; })[0];
      if (!v) return null;
      var codec = String(v.Codec || "").toLowerCase();
      var mime = codec === "h264" ? 'video/mp4; codecs="avc1.640029"' : codec === "vp9" ? 'video/webm; codecs="vp9"' : "";
      if (!mime || (v.BitDepth || 8) > 8 || !document.createElement("video").canPlayType(mime)) return null;
      if (!/^(mp4|m4v|mov|mkv|matroska|webm)/i.test(String(ms.Container || ""))) return null;
      var dur = (item.RunTimeTicks || ms.RunTimeTicks || 0) / 1e7;
      return { url: c.getUrl("Videos/" + item.Id + "/stream", { static: true, MediaSourceId: ms.Id, api_key: c.accessToken() }), start: dur > 240 ? Math.floor(dur * 0.18) : Math.floor(dur * 0.25) };
    }).catch(function () { return null; }).then(function (r) { previewCache[it.Id] = r; return r; });
  }
  // attach a muted looping preview to `host`; returns a stop() function
  function attachPreview(host, info, maxMs, cls, onState) {
    var v = document.createElement("video");
    v.className = cls; v.muted = true; v.defaultMuted = true; v.playsInline = true; v.preload = "auto"; v.disablePictureInPicture = true; v.setAttribute("aria-hidden", "true");
    var stopped = false, timer = null;
    function stop() { if (stopped) return; stopped = true; clearTimeout(timer); v.classList.remove("on"); if (onState) onState(false); setTimeout(function () { try { v.pause(); v.removeAttribute("src"); v.load(); } catch (e) {} if (v.parentNode) v.parentNode.removeChild(v); }, 400); }
    v.addEventListener("loadedmetadata", function () { try { if (info.start) v.currentTime = info.start; } catch (e) {} });
    v.addEventListener("playing", function () { if (stopped) return; v.classList.add("on"); if (onState) onState(true); timer = setTimeout(stop, maxMs); });
    v.addEventListener("error", stop); v.addEventListener("ended", stop);
    v.src = info.url; host.appendChild(v);
    var pr = v.play(); if (pr && pr.catch) pr.catch(stop);
    return stop;
  }

  /* ---------- billboard ---------- */
  function load() {
    var c = client(), uid = userId();
    if (!c || !uid || state.loading) return Promise.resolve();
    state.loading = true;
    return c.getItems(uid, {
      IncludeItemTypes: "Movie,Series", Recursive: true, SortBy: "DateCreated", SortOrder: "Descending", Limit: 40,
      Fields: "Overview,Genres,CommunityRating,OfficialRating,RunTimeTicks,ProductionYear,BackdropImageTags",
      ImageTypeLimit: 1, EnableImageTypes: "Backdrop,Logo"
    }).then(function (r) {
      var items = (r.Items || []).filter(function (i) { return i.BackdropImageTags && i.BackdropImageTags.length; });
      state.items = shuffle(items).slice(0, COUNT);
      state.loadedAt = Date.now(); state.idx = 0; state.loading = false;
    }, function () { state.loading = false; });
  }

  function build() {
    var root = document.createElement("section");
    root.id = "jfHero"; root.setAttribute("aria-roledescription", "carousel"); root.setAttribute("aria-label", "Featured");
    var slides = state.items.map(function (it, n) {
      var meta = [];
      if (it.CommunityRating) meta.push('<span class="jfh-star">★ ' + it.CommunityRating.toFixed(1) + "</span>");
      if (it.ProductionYear) meta.push("<span>" + it.ProductionYear + "</span>");
      var rt = runtime(it.RunTimeTicks); if (rt) meta.push("<span>" + rt + "</span>");
      var title = it.ImageTags && it.ImageTags.Logo
        ? '<img class="jfh-logo" alt="' + esc(it.Name) + '" data-src="' + img(it.Id, "Logo", it.ImageTags.Logo, 700) + '">'
        : '<h2 class="jfh-title">' + esc(it.Name) + "</h2>";
      return '<div class="jfh-slide' + (n === 0 ? " on" : "") + '" data-id="' + it.Id + '" data-server="' + it.ServerId + '" role="group" aria-label="' + (n + 1) + " of " + state.items.length + '">' +
        '<div class="jfh-bg" data-bg="' + img(it.Id, "Backdrop", it.BackdropImageTags[0], 1920) + '"></div>' +
        '<div class="jfh-inner"><div class="jfh-info">' + title +
        '<div class="jfh-meta">' + meta.join('<i></i>') + "</div>" +
        (it.Overview ? '<p class="jfh-over">' + esc(it.Overview) + "</p>" : "") +
        '<div class="jfh-actions"><button type="button" class="jfh-btn jfh-play" data-act="play"><b>▶</b>Play</button>' +
        '<button type="button" class="jfh-btn jfh-more" data-act="more"><b>ⓘ</b>More Info</button></div>' +
        "</div></div>" +
        (it.OfficialRating ? '<div class="jfh-age">' + esc(it.OfficialRating) + "</div>" : "") +
        "</div>";
    }).join("");
    var dots = state.items.map(function (_, n) { return '<button type="button" class="jfh-dot' + (n === 0 ? " on" : "") + '" data-go="' + n + '" aria-label="Show item ' + (n + 1) + '"></button>'; }).join("");
    root.innerHTML = slides + '<div class="jfh-dots">' + dots + "</div>";
    root.addEventListener("click", onClick);
    root.addEventListener("mouseenter", function () { if (state.timer) { clearInterval(state.timer); state.timer = null; } });
    root.addEventListener("mouseleave", start);
    return root;
  }
  function wake(slide) {          // fetch a slide's images the first time it is needed
    if (!slide || slide.dataset.ready) return; slide.dataset.ready = "1";
    var bg = slide.querySelector(".jfh-bg"); if (bg && bg.dataset.bg) bg.style.backgroundImage = "url('" + bg.dataset.bg + "')";
    [].forEach.call(slide.querySelectorAll("img[data-src]"), function (i) { i.src = i.dataset.src; });
  }
  var heroStop = null, heroTimer = null;
  function heroPreview(slideEl, it) {
    if (heroStop) { heroStop(); heroStop = null; } clearTimeout(heroTimer); state.previewing = false;
    if (reduceMotion || !it || document.hidden) return;
    heroTimer = setTimeout(function () {
      resolvePreview(it).then(function (info) {
        if (!info || !slideEl.classList.contains("on") || document.hidden) return;
        heroStop = attachPreview(slideEl.querySelector(".jfh-bg"), info, 30000, "jfh-vid", function (on) { state.previewing = on; });
      });
    }, 2500);
  }
  function show(n) {
    var r = state.root; if (!r) return;
    var count = state.items.length; state.idx = (n + count) % count;
    var slides = r.querySelectorAll(".jfh-slide");
    wake(slides[state.idx]); wake(slides[(state.idx + 1) % count]);
    [].forEach.call(slides, function (s, i) { s.classList.toggle("on", i === state.idx); });
    [].forEach.call(r.querySelectorAll(".jfh-dot"), function (d, i) { d.classList.toggle("on", i === state.idx); });
    heroPreview(slides[state.idx], state.items[state.idx]);
  }
  function start() { stop(); if (state.items.length > 1) state.timer = setInterval(function () { if (!state.previewing) show(state.idx + 1); }, INTERVAL); }
  function stop() { if (state.timer) { clearInterval(state.timer); state.timer = null; } if (heroStop) { heroStop(); heroStop = null; } clearTimeout(heroTimer); state.previewing = false; }

  function details(id, server) { window.location.hash = "#/details?id=" + id + (server ? "&serverId=" + server : ""); }
  function play(id, server) { try { sessionStorage.setItem("jfHeroPlay", id); } catch (x) {} details(id, server); }
  function onClick(e) {
    var b = e.target.closest("button"); if (!b) return;
    if (b.dataset.go) return show(parseInt(b.dataset.go, 10));
    var s = b.closest(".jfh-slide"); if (!s) return;
    if (b.dataset.act === "play") play(s.dataset.id, s.dataset.server); else details(s.dataset.id, s.dataset.server);
  }

  // After "Play" the details page opens; press its own Play button for the user.
  function autoPlay() {
    var id; try { id = sessionStorage.getItem("jfHeroPlay"); } catch (x) {}
    if (!id || location.hash.indexOf("id=" + id) < 0) return;
    var tries = 0, t = setInterval(function () {
      var btn = document.querySelector(".itemDetailPage:not(.hide) .btnPlay:not(.hide)");
      if (btn || ++tries > 30) { clearInterval(t); try { sessionStorage.removeItem("jfHeroPlay"); } catch (x) {} if (btn) btn.click(); }
    }, 200);
  }

  /* ---------- rows of landscape thumbnails ---------- */
  var rowsState = { loadedAt: 0, loading: false, data: [] };
  var F = "CommunityRating,ProductionYear,OfficialRating,Genres,DateCreated,PrimaryImageAspectRatio,SeriesName,ParentIndexNumber,IndexNumber,SeriesId,ParentBackdropImageTags";
  var IMG = "Primary,Backdrop,Thumb";

  function thumbUrl(it, w) {                 // landscape picture: Thumb, else Backdrop, else (episode) screenshot, else poster
    var t = it.ImageTags || {};
    if (t.Thumb) return img(it.Id, "Thumb", t.Thumb, w);
    if (it.BackdropImageTags && it.BackdropImageTags.length) return img(it.Id, "Backdrop", it.BackdropImageTags[0], w);
    if (it.Type === "Episode" && t.Primary) return img(it.Id, "Primary", t.Primary, w);
    if (it.Type === "Episode" && it.SeriesId && it.ParentBackdropImageTags && it.ParentBackdropImageTags.length)
      return img(it.SeriesId, "Backdrop", it.ParentBackdropImageTags[0], w);
    if (t.Primary) return img(it.Id, "Primary", t.Primary, w);
    return "";
  }
  function href(it) { return "#/details?id=" + it.Id + (it.ServerId ? "&serverId=" + it.ServerId : ""); }
  function subtitle(it) {
    if (it.Type === "Episode") return "S" + (it.ParentIndexNumber || 1) + ":E" + (it.IndexNumber || 1) + " · " + esc(it.Name);
    return "";
  }
  function card(it) {
    var pct = it.UserData && it.UserData.PlayedPercentage;
    var u = thumbUrl(it, 480);
    var name = it.Type === "Episode" && it.SeriesName ? it.SeriesName : it.Name;
    var meta = [];
    if (it.CommunityRating) meta.push('<b class="jfr-rate">★ ' + it.CommunityRating.toFixed(1) + "</b>");
    if (it.OfficialRating) meta.push('<span class="jfr-age">' + esc(it.OfficialRating) + "</span>");
    if (it.ProductionYear) meta.push("<span>" + it.ProductionYear + "</span>");
    var sub = subtitle(it);
    return '<a class="jfr-card" data-id="' + it.Id + '" data-server="' + (it.ServerId || "") + '" href="' + href(it) + '" title="' + esc(it.Name) + '">' +
      '<span class="jfr-thumb">' + (u ? '<img loading="lazy" decoding="async" alt="" src="' + u + '">' : "") +
      '<span class="jfr-name">' + esc(name) + "</span>" +
      (pct ? '<span class="jfr-prog"><i style="width:' + Math.min(100, Math.round(pct)) + '%"></i></span>' : "") + "</span>" +
      '<span class="jfr-pop"><span class="jfr-btns"><button type="button" class="jfr-play" data-act="play" aria-label="Play ' + esc(it.Name) + '">▶</button>' +
      '<button type="button" class="jfr-more" data-act="more" aria-label="More info">⌄</button></span>' +
      (sub ? '<span class="jfr-sub">' + sub + "</span>" : "") +
      '<span class="jfr-meta">' + meta.join("") + "</span>" +
      (it.Genres && it.Genres.length ? '<span class="jfr-genres">' + it.Genres.slice(0, 3).map(esc).join(" · ") + "</span>" : "") +
      "</span></a>";
  }
  function topCard(it, n) {
    var t = it.ImageTags && it.ImageTags.Primary;
    return '<a class="jfr-card jfr-top" href="' + href(it) + '" title="' + esc(it.Name) + '"><span class="jfr-num">' + n + "</span>" +
      '<span class="jfr-poster">' + (t ? '<img loading="lazy" decoding="async" alt="" src="' + img(it.Id, "Primary", t, 360) + '">' : "") + "</span></a>";
  }
  function rowHtml(row) {
    var cards = row.top ? row.items.slice(0, 10).map(function (it, i) { return topCard(it, i + 1); }).join("") : row.items.map(card).join("");
    return '<div class="jfr-row' + (row.top ? " jfr-toprow" : "") + '"><h2 class="jfr-title">' + esc(row.title) + "</h2>" +
      '<div class="jfr-wrap"><button type="button" class="jfr-arrow jfr-prev" aria-label="Scroll left">‹</button>' +
      '<div class="jfr-scroller">' + cards + '</div><button type="button" class="jfr-arrow jfr-next" aria-label="Scroll right">›</button></div></div>';
  }

  function fetchFixed() {          // always fresh: what you are in the middle of
    var c = client(), uid = userId();
    var resume = c.getJSON(c.getUrl("Users/" + uid + "/Items/Resume", { Limit: 12, MediaTypes: "Video", Fields: F, EnableImageTypes: IMG, ImageTypeLimit: 1, EnableTotalRecordCount: false }))
      .then(function (r) { return [{ title: "Continue Watching", items: r.Items || [] }]; }, function () { return []; });
    var next = c.getJSON(c.getUrl("Shows/NextUp", { userId: uid, Limit: 12, Fields: F, EnableImageTypes: IMG, ImageTypeLimit: 1, EnableTotalRecordCount: false }))
      .then(function (r) { return [{ title: "Next Up", items: r.Items || [] }]; }, function () { return []; });
    return Promise.all([resume, next]).then(function (p) {
      var seen = {}; (p[0][0] ? p[0][0].items : []).forEach(function (i) { seen[i.Id] = 1; });
      if (p[1][0]) p[1][0].items = p[1][0].items.filter(function (i) { return !seen[i.Id]; });      // Next Up: only what is not already in Continue Watching
      return p[0].concat(p[1]);
    });
  }
  function fetchBrowse() {         // cached for REFRESH_MS: needs a big query, changes slowly
    var c = client(), uid = userId();
    try {
      var cached = JSON.parse(sessionStorage.getItem("jfRows3:" + uid) || "null");
      if (cached && Date.now() - cached.t < REFRESH_MS) return Promise.resolve(cached.rows);
    } catch (x) {}
    var big = c.getItems(uid, { IncludeItemTypes: "Movie,Series", Recursive: true, SortBy: "CommunityRating", SortOrder: "Descending", Limit: 300, Fields: F, ImageTypeLimit: 1, EnableImageTypes: IMG })
      .then(function (r) {
        var all = r.Items || [], by = {}, rows = [];
        rows.push({ title: "Recently Added", items: all.slice().sort(function (x, y) { return (y.DateCreated || "") < (x.DateCreated || "") ? -1 : 1; }).slice(0, 14) });
        if (all.length >= 4) rows.push({ title: "Top 10 on Home Cinema", items: all.slice(0, 10), top: true });
        all.forEach(function (it) { (it.Genres || []).forEach(function (g) { (by[g] = by[g] || []).push(it); }); });
        Object.keys(by).filter(function (g) { return by[g].length >= 3; })
          .sort(function (x, y) { return by[y].length - by[x].length || (x < y ? -1 : 1); })
          .slice(0, 3).forEach(function (g) { rows.push({ title: g, items: by[g].slice(0, 14) }); });
        return rows;
      }, function () { return []; });
    var recs = c.getJSON(c.getUrl("Movies/Recommendations", { userId: uid, categoryLimit: 1, itemLimit: 12, fields: F }))
      .then(function (list) {
        return (list || []).slice(0, 1).map(function (g) {
          var t = g.BaselineItemName;
          return { title: g.RecommendationType === "SimilarToRecentlyPlayed" ? "Because You Watched " + t : g.RecommendationType === "SimilarToLikedItem" ? "Because You Liked " + t : "Recommended for You", items: g.Items || [] };
        });
      }, function () { return []; });
    return Promise.all([big, recs]).then(function (p) {
      var rows = p[0].slice(0, 1).concat(p[1], p[0].slice(1));
      try { sessionStorage.setItem("jfRows3:" + uid, JSON.stringify({ t: Date.now(), rows: rows })); } catch (x) {}
      return rows;
    });
  }
  function loadRows() {
    var c = client(), uid = userId();
    if (!c || !uid || rowsState.loading) return Promise.resolve();
    rowsState.loading = true;
    return Promise.all([fetchFixed(), fetchBrowse()]).then(function (p) {
      var seenTitles = {}, seenSets = {}, rows = [];
      p[0].concat(p[1]).forEach(function (row) {
        var mine = /^(Continue|Next Up)/.test(row.title);
        if (!row.items || !row.items.length || seenTitles[row.title]) return;
        if (!mine && row.items.length < 2) return;
        var sig = row.items.map(function (i) { return i.Id; }).sort().join(",");            // skip a browse row that repeats an earlier one
        if (!mine && !row.top && seenSets[sig]) return;
        seenTitles[row.title] = 1; seenSets[sig] = 1; rows.push(row);
      });
      rowsState.data = rows.slice(0, MAX_ROWS); rowsState.loadedAt = Date.now(); rowsState.loading = false;
    }, function () { rowsState.loading = false; });
  }

  function tidyHome() {            // our rows replace Jellyfin's own video rows and the library tiles (libraries are in the top bar)
    var c = container(); if (!c) return;
    [].forEach.call(c.querySelectorAll(".verticalSection"), function (sec) {
      if (sec.closest("#jfRows")) return;
      var t = sec.querySelector(".sectionTitle, h2"), title = t ? t.textContent.trim() : "";
      if (sec.querySelector('.card[data-type="CollectionFolder"]') || /^(Recently Added|Continue Watching|Next Up)/i.test(title)) sec.classList.add("jf-hide");
    });
  }
  function mountRows() {
    var tries = 0, t = setInterval(function () {
      var c = container(), anchor = c && c.querySelector(".verticalSection");
      if (anchor || ++tries > 100) {
        clearInterval(t); if (!anchor) return;
        tidyHome(); setTimeout(tidyHome, 1500);
        loadRows().then(function () {
          var c2 = container(); if (!c2) return;
          var old = document.getElementById("jfRows"); if (old) old.remove();
          if (!rowsState.data.length) return;
          var wrap = document.createElement("div"); wrap.id = "jfRows";
          wrap.innerHTML = rowsState.data.map(rowHtml).join("");
          wrap.addEventListener("click", onRowsClick);
          if (!(window.matchMedia && window.matchMedia("(hover: none)").matches) && !reduceMotion) wireCardPreviews(wrap);
          var hero = document.getElementById("jfHero");
          if (hero && hero.parentNode === c2) c2.insertBefore(wrap, hero.nextSibling); else c2.insertBefore(wrap, c2.firstChild);
        });
      }
    }, 300);
  }
  function wireCardPreviews(wrap) {
    var byId = {}; rowsState.data.forEach(function (r) { r.items.forEach(function (it) { byId[it.Id] = it; }); });
    [].forEach.call(wrap.querySelectorAll(".jfr-card:not(.jfr-top)"), function (card) {
      var t = null, stop = null;
      card.addEventListener("mouseenter", function () {
        var it = byId[card.dataset.id]; if (!it) return;
        t = setTimeout(function () {
          resolvePreview(it).then(function (info) {
            if (!info || !card.matches(":hover")) return;
            if (stop) stop();
            stop = attachPreview(card.querySelector(".jfr-thumb"), info, 20000, "jfr-vid");
          });
        }, 650);
      });
      card.addEventListener("mouseleave", function () { clearTimeout(t); if (stop) { stop(); stop = null; } });
    });
  }
  function onRowsClick(e) {
    var a = e.target.closest(".jfr-arrow");
    if (a) {
      var sc = a.parentNode.querySelector(".jfr-scroller");
      sc.scrollBy({ left: (a.classList.contains("jfr-prev") ? -1 : 1) * sc.clientWidth * 0.9, behavior: "smooth" });
      return;
    }
    var b = e.target.closest("button[data-act]");
    if (b) {
      e.preventDefault(); var card = b.closest(".jfr-card");
      if (b.dataset.act === "play") play(card.dataset.id, card.dataset.server); else details(card.dataset.id, card.dataset.server);
    }
  }

  function container() { return document.querySelector(".homePage .homeSectionsContainer"); }

  function mount() {
    var c = container(); if (!c) return;
    mountRows();
    var existing = document.getElementById("jfHero");
    var fresh = Date.now() - state.loadedAt < REFRESH_MS;
    if (existing && fresh) { start(); return; }
    if (existing) existing.remove();
    load().then(function () {
      var c2 = container(); if (!c2 || !state.items.length) return;
      var old = document.getElementById("jfHero"); if (old) old.remove();
      state.root = build();
      c2.insertBefore(state.root, c2.firstChild);
      show(0);
      start();
    });
  }

  // Netflix header: transparent on top of the picture, solid once the page is scrolled
  function onScroll() { document.documentElement.classList.toggle("jf-scrolled", (window.scrollY || document.documentElement.scrollTop) > 40); }
  window.addEventListener("scroll", onScroll, { passive: true }); onScroll();

  function onRoute() {
    if (/^#\/home/.test(location.hash) || location.hash === "" || location.hash === "#/") { setTimeout(mount, 50); }
    else { stop(); autoPlay(); }
  }
  window.addEventListener("hashchange", onRoute);
  document.addEventListener("viewshow", onRoute);
  // Home page is built asynchronously after login: poll briefly until its container exists.
  var boot = setInterval(function () { if (userId() && container()) { clearInterval(boot); onRoute(); } }, 400);
  setTimeout(function () { clearInterval(boot); }, 60000);
})();
