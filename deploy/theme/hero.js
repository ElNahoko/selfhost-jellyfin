/* Streaming-style front end for Jellyfin: billboard, rows of thumbnails, click-panel with episodes, silent previews,
   category pages (Movies / TV Shows) built like the home page.
   Self-contained, no external requests. Everything is read through the logged-in user's own ApiClient, so it only shows
   what that user may see. Loaded by the patched index.html (see docs/12-theme.md). MIT licensed, part of selfhost-jellyfin. */
(function () {
  "use strict";
  if (window.__jfHeroLoaded) return;
  window.__jfHeroLoaded = true;

  var BRAND = "Lumio", COUNT = 6, INTERVAL = 10000, REFRESH_MS = 3 * 60 * 1000, MAX_ROWS = 7;

  /* ======================================================= helpers ======================================================= */
  function client() { return window.ApiClient; }
  function userId() { var c = client(); return c && c.getCurrentUserId && c.getCurrentUserId(); }
  function img(id, type, tag, w) { return client().getUrl("Items/" + id + "/Images/" + type + "/0", { tag: tag, maxWidth: w, quality: 85 }); }
  var ICO = {
    play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>',
    plus: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6z"/></svg>',
    check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z"/></svg>',
    info: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M11 7h2v2h-2zm0 4h2v6h-2zm1-9a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 18a8 8 0 1 1 0-16 8 8 0 0 1 0 16z"/></svg>',
    down: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M7.4 8.6 12 13.2l4.6-4.6L18 10l-6 6-6-6z"/></svg>',
    close: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="M19 6.4 17.6 5 12 10.6 6.4 5 5 6.4 10.6 12 5 17.6 6.4 19 12 13.4 17.6 19 19 17.6 13.4 12z"/></svg>',
    star: '<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="m12 17.3 6.2 3.7-1.6-7L22 9.2l-7.2-.6L12 2 9.2 8.6 2 9.2 7.5 14l-1.7 7z"/></svg>',
    on: '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path fill="currentColor" d="M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0 0 14 8v8a4.5 4.5 0 0 0 2.5-4zM14 3.2v2.1a7 7 0 0 1 0 13.4v2.1a9 9 0 0 0 0-17.6z"/></svg>',
    off: '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path fill="currentColor" d="M16.5 12A4.5 4.5 0 0 0 14 8v2.2l2.5 2.5V12zM19 12a7 7 0 0 1-.9 3.4l1.5 1.5A9 9 0 0 0 21 12a9 9 0 0 0-7-8.8v2.1a7 7 0 0 1 5 6.7zM4.3 3 3 4.3 7.7 9H3v6h4l5 5v-6.7l4.3 4.3a8.4 8.4 0 0 1-2.3 1.2v2.1a10.6 10.6 0 0 0 3.7-1.9l2 2 1.3-1.3L4.3 3zM12 4 9.9 6.1 12 8.2V4z"/></svg>'
  };
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function shuffle(a) { for (var i = a.length - 1; i > 0; i--) { var j = Math.floor(Math.random() * (i + 1)), t = a[i]; a[i] = a[j]; a[j] = t; } return a; }
  function runtime(ticks) { if (!ticks) return ""; var m = Math.round(ticks / 600000000); if (m < 1) return ""; return m >= 60 ? Math.floor(m / 60) + "h " + (m % 60 ? (m % 60) + "min" : "") : m + " min"; }
  function details(id, server) { window.location.hash = "#/details?id=" + id + (server ? "&serverId=" + server : ""); }
  function play(id, server) { try { sessionStorage.setItem("jfHeroPlay", id); } catch (x) {} details(id, server); }
  function href(it) { return "#/details?id=" + it.Id + (it.ServerId ? "&serverId=" + it.ServerId : ""); }
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var canHover = !(window.matchMedia && window.matchMedia("(hover: none)").matches);

  // item details are fetched once and reused (hovering a card warms the cache, so the panel opens instantly)
  var itemCache = {};
  function getItem(id) {
    if (!itemCache[id]) itemCache[id] = client().getItem(userId(), id).catch(function (e) { delete itemCache[id]; throw e; });
    return itemCache[id];
  }
  // "My list" = Jellyfin's favourite flag
  function setFav(id, on) {
    var c = client(), uid = userId();
    return c.ajax({ type: on ? "POST" : "DELETE", url: c.getUrl("Users/" + uid + "/FavoriteItems/" + id), dataType: "json" }).then(function () {
      delete itemCache[id];
      try { var k = []; for (var i = 0; i < sessionStorage.length; i++) { var n = sessionStorage.key(i); if (n && n.indexOf("jfRows4:") === 0) k.push(n); } k.forEach(function (n) { sessionStorage.removeItem(n); }); } catch (x) {}
    });
  }

  /* ============================================ silent video previews =============================================
     About 20 s of the real file, muted, straight from the server (no transcoding, no playback session). Only when this
     browser can decode the file as it is (H.264 8-bit); everything else keeps the still picture. */
  var previewCache = {};
  function speakerButton(cls, video) {
    var b = document.createElement("button"); b.type = "button"; b.className = cls; b.setAttribute("aria-label", "Sound on/off");
    function paint() { b.innerHTML = video.muted ? ICO.off : ICO.on; }
    paint();
    b.addEventListener("click", function (e) { e.stopPropagation(); video.muted = !video.muted; if (!video.muted) video.volume = 0.8; paint(); });
    return b;
  }
  function resolvePreview(it) {
    if (previewCache[it.Id] !== undefined) return Promise.resolve(previewCache[it.Id]);
    var c = client(), uid = userId();
    var get = it.Type === "Series"
      ? c.getItems(uid, { ParentId: it.Id, Recursive: true, IncludeItemTypes: "Episode", SortBy: "ParentIndexNumber,IndexNumber", Limit: 1, Fields: "MediaSources" }).then(function (r) { return r.Items && r.Items[0]; })
      : getItem(it.Id);
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
  function attachPreview(host, info, maxMs, cls, onState, onVideo) {       // returns stop()
    var v = document.createElement("video");
    v.className = cls; v.muted = true; v.defaultMuted = true; v.playsInline = true; v.preload = "auto"; v.disablePictureInPicture = true; v.setAttribute("aria-hidden", "true");
    var stopped = false, timer = null;
    function stop() { if (stopped) return; stopped = true; clearTimeout(timer); v.classList.remove("on"); if (onState) onState(false); setTimeout(function () { try { v.pause(); v.removeAttribute("src"); v.load(); } catch (e) {} if (v.parentNode) v.parentNode.removeChild(v); }, 400); }
    v.addEventListener("loadedmetadata", function () { try { if (info.start) v.currentTime = info.start; } catch (e) {} });
    v.addEventListener("playing", function () {
      if (stopped) return; timer = setTimeout(stop, maxMs);
      var shown = false;
      function reveal() { if (shown || stopped || !v.videoWidth) return; shown = true; v.classList.add("on"); if (onState) onState(true); }
      if (v.requestVideoFrameCallback) v.requestVideoFrameCallback(function () { reveal(); });
      else v.addEventListener("timeupdate", function () { if (v.currentTime > (info.start || 0) + 0.3) reveal(); });
    });
    v.addEventListener("error", stop); v.addEventListener("ended", stop);
    v.src = info.url; host.appendChild(v); if (onVideo) onVideo(v);
    var pr = v.play(); if (pr && pr.catch) pr.catch(stop);
    return stop;
  }

  /* ================================================== billboard ================================================== */
  function loadHeroItems(scope) {
    var c = client(), uid = userId();
    var q = { IncludeItemTypes: scope.types, Recursive: true, SortBy: "DateCreated", SortOrder: "Descending", Limit: 40,
      Fields: "Overview,Genres,CommunityRating,OfficialRating,RunTimeTicks,ProductionYear,BackdropImageTags", ImageTypeLimit: 1, EnableImageTypes: "Backdrop,Logo" };
    if (scope.parentId) q.ParentId = scope.parentId;
    return c.getItems(uid, q).then(function (r) {
      return shuffle((r.Items || []).filter(function (i) { return i.BackdropImageTags && i.BackdropImageTags.length; })).slice(0, COUNT);
    });
  }
  function createHero(items, extraClass) {
    var h = { items: items, idx: 0, timer: null, previewing: false, stopPrev: null, prevTimer: null, root: null };
    var root = document.createElement("section");
    root.className = "jfh-root " + (extraClass || ""); root.setAttribute("aria-roledescription", "carousel"); root.setAttribute("aria-label", "Featured");
    var slides = items.map(function (it, n) {
      var meta = [];
      if (it.CommunityRating) meta.push('<span class="jfh-star">' + ICO.star + it.CommunityRating.toFixed(1) + "</span>");
      if (it.ProductionYear) meta.push("<span>" + it.ProductionYear + "</span>");
      var rt = runtime(it.RunTimeTicks); if (rt) meta.push("<span>" + rt + "</span>");
      var title = it.ImageTags && it.ImageTags.Logo
        ? '<img class="jfh-logo" alt="' + esc(it.Name) + '" data-src="' + img(it.Id, "Logo", it.ImageTags.Logo, 700) + '">'
        : '<h2 class="jfh-title">' + esc(it.Name) + "</h2>";
      return '<div class="jfh-slide' + (n === 0 ? " on" : "") + '" data-id="' + it.Id + '" data-server="' + it.ServerId + '" role="group" aria-label="' + (n + 1) + " of " + items.length + '">' +
        '<div class="jfh-bg" data-bg="' + img(it.Id, "Backdrop", it.BackdropImageTags[0], 1920) + '"></div>' +
        '<div class="jfh-inner"><div class="jfh-info">' + title + '<div class="jfh-meta">' + meta.join("<i></i>") + "</div>" +
        (it.Overview ? '<p class="jfh-over">' + esc(it.Overview) + "</p>" : "") +
        '<div class="jfh-actions"><button type="button" class="jfh-btn jfh-play" data-act="play">' + ICO.play + 'Play</button>' +
        '<button type="button" class="jfh-btn jfh-more" data-act="more">' + ICO.info + 'More Info</button></div></div></div>' +
        (it.OfficialRating ? '<div class="jfh-age">' + esc(it.OfficialRating) + "</div>" : "") + "</div>";
    }).join("");
    root.innerHTML = slides + '<div class="jfh-dots">' + items.map(function (_, n) { return '<button type="button" class="jfh-dot' + (n === 0 ? " on" : "") + '" data-go="' + n + '" aria-label="Show item ' + (n + 1) + '"></button>'; }).join("") + "</div>";
    h.root = root;

    function wake(slide) {
      if (!slide || slide.dataset.ready) return; slide.dataset.ready = "1";
      var bg = slide.querySelector(".jfh-bg"); if (bg && bg.dataset.bg) bg.style.backgroundImage = "url('" + bg.dataset.bg + "')";
      [].forEach.call(slide.querySelectorAll("img[data-src]"), function (i) { i.src = i.dataset.src; });
    }
    function previewFor(slideEl, it) {
      if (h.stopPrev) { h.stopPrev(); h.stopPrev = null; } clearTimeout(h.prevTimer); h.previewing = false;
      if (reduceMotion || !it || document.hidden || modal.el) return;
      h.prevTimer = setTimeout(function () {
        resolvePreview(it).then(function (info) {
          if (!info || !slideEl.classList.contains("on") || document.hidden || modal.el) return;
          var sp = null;
          h.stopPrev = attachPreview(slideEl.querySelector(".jfh-bg"), info, 30000, "jfh-vid",
            function (on) { h.previewing = on; if (sp && !on && sp.parentNode) sp.parentNode.removeChild(sp); },
            function (v) { sp = speakerButton("jfh-mute", v); slideEl.appendChild(sp); });
        });
      }, 2500);
    }
    h.show = function (n) {
      var count = items.length; h.idx = (n + count) % count;
      var els = root.querySelectorAll(".jfh-slide");
      wake(els[h.idx]); wake(els[(h.idx + 1) % count]);
      [].forEach.call(els, function (s, i) { s.classList.toggle("on", i === h.idx); });
      [].forEach.call(root.querySelectorAll(".jfh-dot"), function (d, i) { d.classList.toggle("on", i === h.idx); });
      previewFor(els[h.idx], items[h.idx]);
    };
    h.start = function () { h.stopRotation(); if (items.length > 1) h.timer = setInterval(function () { if (!h.previewing && !modal.el) h.show(h.idx + 1); }, INTERVAL); };
    h.stopRotation = function () { if (h.timer) { clearInterval(h.timer); h.timer = null; } };
    h.stop = function () { h.stopRotation(); if (h.stopPrev) { h.stopPrev(); h.stopPrev = null; } clearTimeout(h.prevTimer); h.previewing = false; };
    root.addEventListener("click", function (e) {
      var b = e.target.closest("button"); if (!b) return;
      if (b.dataset.go) return h.show(parseInt(b.dataset.go, 10));
      var s = b.closest(".jfh-slide"); if (!s) return;
      if (b.dataset.act === "play") play(s.dataset.id, s.dataset.server); else openModal(s.dataset.id);
    });
    root.addEventListener("mouseover", function (e) { var b = e.target.closest && e.target.closest(".jfh-more"); if (b && !b.dataset.warm) { b.dataset.warm = "1"; getItem(b.closest(".jfh-slide").dataset.id).catch(function () {}); } });
    root.addEventListener("mouseenter", h.stopRotation);
    root.addEventListener("mouseleave", h.start);
    h.show(0);
    return h;
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

  /* =========================================== rows of landscape thumbnails =========================================== */
  var F = "CommunityRating,ProductionYear,OfficialRating,Genres,DateCreated,PrimaryImageAspectRatio,SeriesName,ParentIndexNumber,IndexNumber,SeriesId,ParentBackdropImageTags";
  var IMG = "Primary,Backdrop,Thumb";
  function thumbUrl(it, w) {                 // landscape picture: Thumb, else Backdrop, else (episode) screenshot, else poster
    var t = it.ImageTags || {};
    if (t.Thumb) return img(it.Id, "Thumb", t.Thumb, w);
    if (it.BackdropImageTags && it.BackdropImageTags.length) return img(it.Id, "Backdrop", it.BackdropImageTags[0], w);
    if (it.Type === "Episode" && t.Primary) return img(it.Id, "Primary", t.Primary, w);
    if (it.Type === "Episode" && it.SeriesId && it.ParentBackdropImageTags && it.ParentBackdropImageTags.length) return img(it.SeriesId, "Backdrop", it.ParentBackdropImageTags[0], w);
    if (t.Primary) return img(it.Id, "Primary", t.Primary, w);
    return "";
  }
  function card(it) {
    var pct = it.UserData && it.UserData.PlayedPercentage, fav = it.UserData && it.UserData.IsFavorite;
    var u = thumbUrl(it, 480);
    var name = it.Type === "Episode" && it.SeriesName ? it.SeriesName : it.Name;
    var meta = [];
    if (it.CommunityRating) meta.push('<b class="jfr-rate">' + ICO.star + it.CommunityRating.toFixed(1) + "</b>");
    if (it.OfficialRating) meta.push('<span class="jfr-age">' + esc(it.OfficialRating) + "</span>");
    if (it.ProductionYear) meta.push("<span>" + it.ProductionYear + "</span>");
    var sub = it.Type === "Episode" ? "S" + (it.ParentIndexNumber || 1) + ":E" + (it.IndexNumber || 1) + " · " + esc(it.Name) : "";
    return '<a class="jfr-card" data-id="' + it.Id + '" data-server="' + (it.ServerId || "") + '" href="' + href(it) + '" title="' + esc(it.Name) + '">' +
      '<span class="jfr-thumb">' + (u ? '<img loading="lazy" decoding="async" alt="" src="' + u + '">' : "") +
      '<span class="jfr-name">' + esc(name) + "</span>" +
      (pct ? '<span class="jfr-prog"><i style="width:' + Math.min(100, Math.round(pct)) + '%"></i></span>' : "") + "</span>" +
      '<span class="jfr-pop"><span class="jfr-btns"><button type="button" class="jfr-play" data-act="play" aria-label="Play ' + esc(it.Name) + '" title="Play">' + ICO.play + '</button>' +
      '<button type="button" class="jfr-list' + (fav ? " on" : "") + '" data-act="fav" aria-label="My list" title="My list">' + (fav ? ICO.check : ICO.plus) + '</button>' +
      '<button type="button" class="jfr-more" data-act="more" aria-label="More info" title="More info">' + ICO.down + '</button></span>' +
      (sub ? '<span class="jfr-sub">' + sub + "</span>" : "") + '<span class="jfr-meta">' + meta.join("") + "</span>" +
      (it.Genres && it.Genres.length ? '<span class="jfr-genres">' + it.Genres.slice(0, 3).map(esc).join(" · ") + "</span>" : "") + "</span></a>";
  }
  function topCard(it, n) {
    var t = it.ImageTags && it.ImageTags.Primary;
    return '<a class="jfr-card jfr-top" data-id="' + it.Id + '" data-server="' + (it.ServerId || "") + '" href="' + href(it) + '" title="' + esc(it.Name) + '"><span class="jfr-num">' + n + "</span>" +
      '<span class="jfr-poster">' + (t ? '<img loading="lazy" decoding="async" alt="" src="' + img(it.Id, "Primary", t, 360) + '">' : "") + "</span></a>";
  }
  function rowHtml(row) {
    var cards = row.top ? row.items.slice(0, 10).map(function (it, i) { return topCard(it, i + 1); }).join("") : row.items.map(card).join("");
    return '<div class="jfr-row' + (row.top ? " jfr-toprow" : "") + '"><h2 class="jfr-title">' + esc(row.title) + "</h2>" +
      '<div class="jfr-wrap"><button type="button" class="jfr-arrow jfr-prev" aria-label="Scroll left"></button>' +
      '<div class="jfr-scroller">' + cards + '</div><button type="button" class="jfr-arrow jfr-next" aria-label="Scroll right"></button></div></div>';
  }

  function fetchFixed() {          // always fresh: what you are in the middle of (home only)
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
  function fetchBrowse(scope) {    // cached for REFRESH_MS: needs a big query, changes slowly
    var c = client(), uid = userId(), key = "jfRows4:" + uid + ":" + scope.key;
    try { var cached = JSON.parse(sessionStorage.getItem(key) || "null"); if (cached && Date.now() - cached.t < REFRESH_MS) return Promise.resolve(cached.rows); } catch (x) {}
    var q = { IncludeItemTypes: scope.types, Recursive: true, SortBy: "CommunityRating", SortOrder: "Descending", Limit: 300, Fields: F, ImageTypeLimit: 1, EnableImageTypes: IMG };
    if (scope.parentId) q.ParentId = scope.parentId;
    var big = c.getItems(uid, q).then(function (r) {
      var all = r.Items || [], by = {}, rows = [];
      rows.push({ title: "Recently Added", items: all.slice().sort(function (x, y) { return (y.DateCreated || "") < (x.DateCreated || "") ? -1 : 1; }).slice(0, 14) });
      if (all.length >= 4) rows.push({ title: "Top 10 " + scope.top, items: all.slice(0, 10), top: true });
      all.forEach(function (it) { (it.Genres || []).forEach(function (g) { (by[g] = by[g] || []).push(it); }); });
      Object.keys(by).filter(function (g) { return by[g].length >= 3; })
        .sort(function (x, y) { return by[y].length - by[x].length || (x < y ? -1 : 1); })
        .slice(0, scope.genres).forEach(function (g) { rows.push({ title: g, items: by[g].slice(0, 14) }); });
      return rows;
    }, function () { return []; });
    var recs = scope.recs ? c.getJSON(c.getUrl("Movies/Recommendations", { userId: uid, categoryLimit: 1, itemLimit: 12, fields: F }))
      .then(function (list) {
        return (list || []).slice(0, 1).map(function (g) {
          var t = g.BaselineItemName;
          return { title: g.RecommendationType === "SimilarToRecentlyPlayed" ? "Because You Watched " + t : g.RecommendationType === "SimilarToLikedItem" ? "Because You Liked " + t : "Recommended for You", items: g.Items || [] };
        });
      }, function () { return []; }) : Promise.resolve([]);
    return Promise.all([big, recs]).then(function (p) {
      var rows = p[0].slice(0, 1).concat(p[1], p[0].slice(1));
      try { sessionStorage.setItem(key, JSON.stringify({ t: Date.now(), rows: rows })); } catch (x) {}
      return rows;
    });
  }
  function loadRows(scope) {
    return Promise.all([scope.fixed ? fetchFixed() : Promise.resolve([]), fetchBrowse(scope)]).then(function (p) {
      var seenTitles = {}, seenSets = {}, rows = [];
      p[0].concat(p[1]).forEach(function (row) {
        var mine = /^(Continue|Next Up)/.test(row.title);
        if (!row.items || !row.items.length || seenTitles[row.title]) return;
        if (!mine && row.items.length < 2) return;
        var sig = row.items.map(function (i) { return i.Id; }).sort().join(",");            // skip a browse row that repeats an earlier one
        if (!mine && !row.top && seenSets[sig]) return;
        seenTitles[row.title] = 1; seenSets[sig] = 1; rows.push(row);
      });
      return rows.slice(0, MAX_ROWS);
    });
  }
  function renderRows(rows) {
    var wrap = document.createElement("div"); wrap.className = "jfr-root";
    wrap.innerHTML = rows.map(rowHtml).join("");
    wrap.addEventListener("click", onRowsClick);
    var byId = {}; rows.forEach(function (r) { r.items.forEach(function (it) { byId[it.Id] = it; }); });
    [].forEach.call(wrap.querySelectorAll(".jfr-card"), function (cardEl) {
      var warm = null, t = null, stop = null;
      cardEl.addEventListener("mouseenter", function () {
        warm = setTimeout(function () { getItem(cardEl.dataset.id).catch(function () {}); }, 120);      // panel opens instantly after a short hover
        if (!canHover || reduceMotion || cardEl.classList.contains("jfr-top")) return;
        var it = byId[cardEl.dataset.id]; if (!it) return;
        t = setTimeout(function () {
          resolvePreview(it).then(function (info) {
            if (!info || !cardEl.matches(":hover")) return;
            if (stop) stop();
            stop = attachPreview(cardEl.querySelector(".jfr-thumb"), info, 20000, "jfr-vid");
          });
        }, 650);
      });
      cardEl.addEventListener("mouseleave", function () { clearTimeout(warm); clearTimeout(t); if (stop) { stop(); stop = null; } });
    });
    return wrap;
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
      e.preventDefault(); e.stopPropagation(); var cardEl = b.closest(".jfr-card");
      if (b.dataset.act === "play") play(cardEl.dataset.id, cardEl.dataset.server);
      else if (b.dataset.act === "fav") {
        if (b.dataset.busy) return; b.dataset.busy = "1";
        var on = !b.classList.contains("on");
        b.classList.toggle("on", on); b.innerHTML = on ? ICO.check : ICO.plus;                 // instant feedback, undone if the server refuses
        setFav(cardEl.dataset.id, on).then(function () { delete b.dataset.busy; }, function () { delete b.dataset.busy; b.classList.toggle("on", !on); b.innerHTML = on ? ICO.plus : ICO.check; });
      } else openModal(cardEl.dataset.id);
      return;
    }
    var k = e.target.closest(".jfr-card");
    if (k && k.dataset.id) { e.preventDefault(); openModal(k.dataset.id); }   // an episode id opens its series with the episode highlighted
  }

  /* ======================================= click panel (details, episodes, similar) ======================================= */
  var modal = { el: null, stopPreview: null, token: 0 };
  function closeModal() {
    if (!modal.el) return;
    if (modal.stopPreview) { modal.stopPreview(); modal.stopPreview = null; }
    modal.token++; document.documentElement.classList.remove("jfm-open");
    var el = modal.el; modal.el = null; el.classList.remove("on"); setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 200);
    document.removeEventListener("keydown", onModalKey, true);
  }
  window.addEventListener("hashchange", function () { closeModal(); });                 // leaving the page closes the panel
  function onModalKey(e) {
    if (e.key !== "Escape") return;
    e.stopPropagation();
    var dd = modal.el && modal.el.querySelector(".jfm-dd.open"); if (dd) { dd.classList.remove("open"); return; }
    closeModal();
  }
  function playFirst(item) {          // series: next unwatched episode, else the first one; anything else: itself
    if (item.Type !== "Series") return play(item.Id, item.ServerId);
    var c = client(), uid = userId();
    c.getJSON(c.getUrl("Shows/NextUp", { userId: uid, SeriesId: item.Id, Limit: 1 })).then(function (r) {
      var e = r.Items && r.Items[0];
      if (e) return play(e.Id, e.ServerId);
      return c.getItems(uid, { ParentId: item.Id, Recursive: true, IncludeItemTypes: "Episode", SortBy: "ParentIndexNumber,IndexNumber", Limit: 1 }).then(function (x) { if (x.Items[0]) play(x.Items[0].Id, x.Items[0].ServerId); else details(item.Id, item.ServerId); });
    }, function () { details(item.Id, item.ServerId); });
  }
  function openModal(id) {
    var c = client(), uid = userId(); if (!c || !uid) return details(id);
    closeModal(); stopHeroes();                     // the banners behind the panel stop their previews
    var token = ++modal.token;
    var back = document.createElement("div"); back.className = "jfm-back"; back.setAttribute("role", "dialog"); back.setAttribute("aria-modal", "true");
    back.innerHTML = '<div class="jfm-panel"><button type="button" class="jfm-close" aria-label="Close">' + ICO.close + '</button><div class="jfm-loading">Loading…</div></div>';
    document.body.appendChild(back); modal.el = back; document.documentElement.classList.add("jfm-open");
    requestAnimationFrame(function () { back.classList.add("on"); });
    back.addEventListener("mousedown", function (e) { if (e.target === back) closeModal(); });
    back.querySelector(".jfm-close").addEventListener("click", closeModal);
    document.addEventListener("keydown", onModalKey, true);
    getItem(id).then(function (item) {
      if (token !== modal.token) return;
      if (item.Type === "Episode" && item.SeriesId) return getItem(item.SeriesId).then(function (s2) { if (token === modal.token) renderModal(back, s2, item.Id, item.SeasonId); });
      renderModal(back, item, null, null);
    }, function () { closeModal(); details(id); });
  }
  function renderModal(back, item, focusEp, focusSeason) {
    var c = client(), uid = userId(), token = modal.token;
    var tags = item.ImageTags || {}, bd = item.BackdropImageTags || [];
    var bgUrl = bd.length ? img(item.Id, "Backdrop", bd[0], 1280) : tags.Primary ? img(item.Id, "Primary", tags.Primary, 800) : "";
    var head = tags.Logo ? '<img class="jfm-logo" alt="' + esc(item.Name) + '" src="' + img(item.Id, "Logo", tags.Logo, 600) + '">' : '<h2 class="jfm-title">' + esc(item.Name) + "</h2>";
    var ud = item.UserData || {}, resume = ud.PlaybackPositionTicks > 0 || (item.Type === "Series" && ud.UnplayedItemCount != null && item.RecursiveItemCount && ud.UnplayedItemCount < item.RecursiveItemCount);
    var meta = [];
    if (item.CommunityRating) meta.push('<b class="jfm-rate">' + ICO.star + item.CommunityRating.toFixed(1) + "</b>");
    if (item.ProductionYear) meta.push("<span>" + item.ProductionYear + (item.Type === "Series" ? (item.EndDate ? " – " + new Date(item.EndDate).getFullYear() : " – now") : "") + "</span>");
    var rt = runtime(item.RunTimeTicks); if (rt && item.Type !== "Series") meta.push("<span>" + rt + "</span>");
    if (item.OfficialRating) meta.push('<span class="jfm-age">' + esc(item.OfficialRating) + "</span>");
    var cast = (item.People || []).filter(function (p) { return p.Type === "Actor"; }).slice(0, 6).map(function (p) { return esc(p.Name); }).join(", ");
    var dir = (item.People || []).filter(function (p) { return p.Type === "Director"; }).slice(0, 2).map(function (p) { return esc(p.Name); }).join(", ");
    var side = "";
    if (cast) side += '<p><i>Cast:</i> ' + cast + "</p>";
    if (dir) side += '<p><i>Director:</i> ' + dir + "</p>";
    if (item.Genres && item.Genres.length) side += '<p><i>Genres:</i> ' + item.Genres.map(esc).join(", ") + "</p>";
    if (item.Studios && item.Studios.length) side += '<p><i>Studio:</i> ' + item.Studios.slice(0, 2).map(function (x) { return esc(x.Name); }).join(", ") + "</p>";
    var fav = ud.IsFavorite;
    var trailerUrl = ""; (item.RemoteTrailers || []).some(function (t) { if (t && /^https:\/\//.test(t.Url || "")) { trailerUrl = t.Url; return true; } return false; });
    back.querySelector(".jfm-panel").innerHTML =
      '<button type="button" class="jfm-close" aria-label="Close">' + ICO.close + '</button>' +
      '<div class="jfm-hero"><div class="jfm-bg"' + (bgUrl ? ' style="background-image:url(\'' + bgUrl + '\')"' : "") + '></div><div class="jfm-grad"></div>' +
      '<div class="jfm-head">' + head + '<div class="jfm-actions"><button type="button" class="jfm-play">' + ICO.play + (resume ? "Resume" : "Play") + '</button>' +
      '<button type="button" class="jfm-round jfm-fav' + (fav ? " on" : "") + '" aria-label="My list" title="My list">' + (fav ? ICO.check : ICO.plus) + '</button>' +
      '<a class="jfm-secondary jfm-open-page" href="#/details?id=' + item.Id + '" title="Open the full details page">' + ICO.info + 'Details</a>' +
      (trailerUrl ? '<a class="jfm-secondary jfm-trailer" href="' + esc(trailerUrl) + '" target="_blank" rel="noopener noreferrer" title="Opens the trailer on the web (new tab)">Trailer</a>' : "") +
      '</div></div></div>' +
      '<div class="jfm-body"><div class="jfm-main"><div class="jfm-meta">' + meta.join("") + "</div>" +
      (item.Taglines && item.Taglines[0] ? '<p class="jfm-tag">' + esc(item.Taglines[0]) + "</p>" : "") +
      '<p class="jfm-over">' + esc(item.Overview || "No description yet.") + '</p></div><div class="jfm-side">' + side + "</div></div>" +
      (item.Type === "Series" ? '<div class="jfm-eps"><div class="jfm-eps-head"><h3>Episodes</h3><div class="jfm-dd" hidden><button type="button" class="jfm-dd-btn" aria-haspopup="listbox"><span></span>' + ICO.down + '</button><div class="jfm-dd-list" role="listbox"></div></div></div><div class="jfm-eplist"><div class="jfm-loading">Loading…</div></div></div>' : "") +
      '<div class="jfm-more" hidden><h3>More like this</h3><div class="jfm-grid"></div></div>';
    var panel = back.querySelector(".jfm-panel");
    panel.querySelector(".jfm-close").addEventListener("click", closeModal);
    panel.querySelector(".jfm-play").addEventListener("click", function () { closeModal(); playFirst(item); });
    panel.querySelector(".jfm-open-page").addEventListener("click", function () { closeModal(); });
    panel.querySelector(".jfm-fav").addEventListener("click", function (e) {
      var b = e.currentTarget; if (b.dataset.busy) return; b.dataset.busy = "1";
      var on = !b.classList.contains("on"); b.classList.toggle("on", on); b.innerHTML = on ? ICO.check : ICO.plus;
      setFav(item.Id, on).then(function () { delete b.dataset.busy; }, function () { delete b.dataset.busy; b.classList.toggle("on", !on); b.innerHTML = on ? ICO.plus : ICO.check; });
    });
    if (!reduceMotion) setTimeout(function () {      // silent preview behind the top picture
      if (token !== modal.token) return;
      resolvePreview(item).then(function (info) { if (info && token === modal.token) modal.stopPreview = attachPreview(panel.querySelector(".jfm-bg"), info, 30000, "jfm-vid", null, function (v) { panel.querySelector(".jfm-hero").appendChild(speakerButton("jfm-mute", v)); }); });
    }, 900);
    c.getJSON(c.getUrl("Items/" + item.Id + "/Similar", { userId: uid, limit: 8, Fields: "ProductionYear,CommunityRating", EnableImageTypes: "Primary,Thumb,Backdrop", ImageTypeLimit: 1 })).then(function (r) {
      if (token !== modal.token) return;
      var list = (r.Items || []).filter(function (x) { return x.ImageTags && x.ImageTags.Primary; }).slice(0, 6);
      if (!list.length) return;
      var more = panel.querySelector(".jfm-more"); more.hidden = false;
      more.querySelector(".jfm-grid").innerHTML = list.map(function (x) {
        return '<a class="jfm-sim" data-id="' + x.Id + '" href="' + href(x) + '"><img loading="lazy" alt="" src="' + img(x.Id, "Primary", x.ImageTags.Primary, 300) + '"><span>' + esc(x.Name) + "</span></a>";
      }).join("");
      [].forEach.call(more.querySelectorAll(".jfm-sim"), function (a) { a.addEventListener("click", function (e) { e.preventDefault(); openModal(a.dataset.id); }); });
    }, function () {});
    if (item.Type === "Series") loadEpisodes(panel, item, focusEp, focusSeason);
  }
  function loadEpisodes(panel, series, focusEp, focusSeason) {
    var c = client(), uid = userId(), token = modal.token;
    var dd = panel.querySelector(".jfm-dd"), btn = dd.querySelector(".jfm-dd-btn"), listBox = dd.querySelector(".jfm-dd-list"), list = panel.querySelector(".jfm-eplist");
    c.getJSON(c.getUrl("Shows/" + series.Id + "/Seasons", { userId: uid })).then(function (r) {
      if (token !== modal.token) return;
      var seasons = r.Items || [];
      if (!seasons.length) { list.innerHTML = '<div class="jfm-loading">No episodes yet.</div>'; return; }
      var current = null;
      function pick(seasonId) {
        current = seasonId;
        var s = seasons.filter(function (x) { return x.Id === seasonId; })[0] || seasons[0];
        btn.firstChild.textContent = s.Name; dd.classList.remove("open");
        [].forEach.call(listBox.children, function (o) { o.classList.toggle("on", o.dataset.id === s.Id); });
        showSeason(s.Id);
      }
      listBox.innerHTML = seasons.map(function (s) { return '<button type="button" role="option" class="jfm-dd-opt" data-id="' + s.Id + '">' + esc(s.Name) + "</button>"; }).join("");
      [].forEach.call(listBox.children, function (o) { o.addEventListener("click", function () { pick(o.dataset.id); }); });
      btn.addEventListener("click", function (e) { e.stopPropagation(); dd.classList.toggle("open"); });
      panel.addEventListener("click", function (e) { if (!e.target.closest(".jfm-dd")) dd.classList.remove("open"); });
      if (seasons.length > 1) dd.hidden = false;
      function showSeason(seasonId) {
        list.innerHTML = '<div class="jfm-loading">Loading…</div>';
        c.getJSON(c.getUrl("Shows/" + series.Id + "/Episodes", { userId: uid, seasonId: seasonId, Fields: "Overview,RunTimeTicks", EnableImageTypes: "Primary", ImageTypeLimit: 1 })).then(function (x) {
          if (token !== modal.token || current !== seasonId) return;
          list.innerHTML = (x.Items || []).map(function (e) {
            var pct = e.UserData && e.UserData.PlayedPercentage, t = e.ImageTags && e.ImageTags.Primary;
            return '<div class="jfm-ep' + (e.Id === focusEp ? " focus" : "") + '" data-id="' + e.Id + '" tabindex="0"><span class="jfm-epn">' + (e.IndexNumber || "") + '</span>' +
              '<span class="jfm-epimg">' + (t ? '<img loading="lazy" alt="" src="' + img(e.Id, "Primary", t, 360) + '">' : "") + '<span class="jfm-epplay">' + ICO.play + '</span>' + (pct ? '<i class="jfm-epprog"><b style="width:' + Math.round(pct) + '%"></b></i>' : "") + "</span>" +
              '<span class="jfm-ept"><b>' + esc(e.Name) + "</b><em>" + (runtime(e.RunTimeTicks) || "") + (e.UserData && e.UserData.Played ? " · watched" : "") + "</em><small>" + esc(e.Overview || "") + "</small></span></div>";
          }).join("") || '<div class="jfm-loading">No episodes in this season.</div>';
          [].forEach.call(list.querySelectorAll(".jfm-ep"), function (row) {
            function go() { closeModal(); play(row.dataset.id, series.ServerId); }
            row.addEventListener("click", go); row.addEventListener("keydown", function (e) { if (e.key === "Enter") go(); });
          });
          var f = list.querySelector(".jfm-ep.focus"); if (f) f.scrollIntoView({ block: "nearest" });
        }, function () { list.innerHTML = '<div class="jfm-loading">Could not load episodes.</div>'; });
      }
      var firstReal = seasons.filter(function (s) { return s.IndexNumber > 0; })[0] || seasons[0];
      pick(focusSeason && seasons.some(function (s) { return s.Id === focusSeason; }) ? focusSeason : firstReal.Id);
    }, function () { list.innerHTML = '<div class="jfm-loading">Could not load episodes.</div>'; });
  }
  // library grids (Movies / TV Shows lists) open the same panel
  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return;
    if (e.target.closest(".jfr-root, .jfh-root, .jfm-back")) return;
    var cardEl = e.target.closest(".card[data-id]"); if (!cardEl) return;
    if (e.target.closest("button, .cardOverlayButton, .cardOverlayFab-primary, .cardIndicators, input, select")) return;
    var type = cardEl.getAttribute("data-type"); if (type !== "Movie" && type !== "Series") return;
    if (!e.target.closest('[data-action="link"], .cardImageContainer, .cardText, a')) return;
    e.preventDefault(); e.stopPropagation(); openModal(cardEl.getAttribute("data-id"));
  }, true);

  /* ================================================ pages: home + categories ================================================ */
  var home = { hero: null, items: [], loadedAt: 0, heroBusy: false, rowsBusy: false, rowsDirty: true };
  var cats = {};           // key -> { hero, items, loadedAt, busy, rows }

  function isHome() { return /^#\/home/.test(location.hash) || location.hash === "" || location.hash === "#/"; }
  function homeContainer() { return document.querySelector(".homePage:not(.hide) .homeSectionsContainer") || document.querySelector(".homePage .homeSectionsContainer"); }
  function catScope() {            // #/movies?topParentId=...  or  #/tv?topParentId=...
    var m = location.hash.match(/^#\/(movies|tv)(?:\.html)?(?:\?(.*))?$/); if (!m) return null;
    var id = (/(?:^|&)topParentId=([0-9a-f]+)/i.exec(m[2] || "") || [])[1]; if (!id) return null;
    var movies = m[1] === "movies";
    return { key: "cat:" + id, parentId: id, types: movies ? "Movie" : "Series", top: movies ? "Movies" : "TV Shows", all: movies ? "All Movies" : "All TV Shows", genres: 5, recs: false, fixed: false };
  }
  var HOME_SCOPE = { key: "home", parentId: null, types: "Movie,Series", top: "on " + BRAND, genres: 3, recs: true, fixed: true };

  function stopHeroes() {
    if (home.hero) home.hero.stop();
    Object.keys(cats).forEach(function (k) { if (cats[k].hero) cats[k].hero.stop(); });
  }
  function tidyHome() {            // our rows replace Jellyfin's own video rows and the library tiles (libraries are in the top bar)
    var c = homeContainer(); if (!c) return;
    [].forEach.call(c.querySelectorAll(".verticalSection"), function (sec) {
      if (sec.closest(".jfr-root")) return;
      var t = sec.querySelector(".sectionTitle, h2"), title = t ? t.textContent.trim() : "";
      if (sec.querySelector('.card[data-type="CollectionFolder"]') || /^(Recently Added|Continue Watching|Next Up)/i.test(title)) sec.classList.add("jf-hide");
    });
  }
  function ensureHome(c) {
    // billboard
    var existing = c.querySelector(":scope > .jfh-root");
    if (existing) { if (home.hero && !home.hero.timer) home.hero.start(); }
    else if (!home.heroBusy) {
      home.heroBusy = true;
      var fresh = home.items.length && Date.now() - home.loadedAt < REFRESH_MS;
      (fresh ? Promise.resolve(home.items) : loadHeroItems(HOME_SCOPE)).then(function (items) {
        home.heroBusy = false; home.items = items; home.loadedAt = Date.now();
        var c2 = homeContainer(); if (!c2 || !items.length || !isHome() || c2.querySelector(":scope > .jfh-root")) return;
        home.hero = createHero(items, "jfh-home"); c2.insertBefore(home.hero.root, c2.firstChild); home.hero.start();
      }, function () { home.heroBusy = false; });
    }
    // rows
    var haveRows = !!c.querySelector(":scope > .jfr-root");
    if ((!haveRows || home.rowsDirty) && !home.rowsBusy) {
      home.rowsBusy = true;
      loadRows(HOME_SCOPE).then(function (rows) {
        home.rowsBusy = false; home.rowsDirty = false;
        var c2 = homeContainer(); if (!c2 || !isHome()) return;
        var old = c2.querySelector(":scope > .jfr-root"); if (old) old.remove();
        if (!rows.length) return;
        var wrap = renderRows(rows), hero = c2.querySelector(":scope > .jfh-root");
        if (hero) c2.insertBefore(wrap, hero.nextSibling); else c2.insertBefore(wrap, c2.firstChild);
      }, function () { home.rowsBusy = false; });
    }
    tidyHome();
  }
  function ensureCategory(scope) {
    var host = document.querySelector(".libraryPage:not(.hide) .padded-bottom-page"); if (!host) return;
    var block = host.querySelector(":scope > .jfc-block");
    if (block && block.dataset.key !== scope.key) { block.remove(); block = null; }
    if (block) { var st = cats[scope.key]; if (st && st.hero && !st.hero.timer) st.hero.start(); return; }
    var st2 = cats[scope.key] = cats[scope.key] || { hero: null, items: [], busy: false };
    if (st2.busy) return; st2.busy = true;
    Promise.all([loadHeroItems(scope), loadRows(scope)]).then(function (p) {
      st2.busy = false;
      var h2 = document.querySelector(".libraryPage:not(.hide) .padded-bottom-page"), cur = catScope();
      if (!h2 || !cur || cur.key !== scope.key || h2.querySelector(":scope > .jfc-block")) return;
      var blk = document.createElement("div"); blk.className = "jfc-block"; blk.dataset.key = scope.key;
      if (p[0].length) { st2.hero = createHero(p[0], "jfh-cat"); blk.appendChild(st2.hero.root); }
      if (p[1].length) blk.appendChild(renderRows(p[1]));
      var all = document.createElement("h2"); all.className = "jfc-all"; all.textContent = scope.all; blk.appendChild(all);
      h2.insertBefore(blk, h2.firstChild);
      if (st2.hero) st2.hero.start();
    }, function () { st2.busy = false; });
  }

  // Safety net for pictures: Jellyfin fills posters/backdrops through a lazy loader that can stall (background tab, slow
  // first paint). Anything with a data-src near the screen is loaded here too; same picture, so doing it twice is harmless.
  function forceImages() {
    var vh = window.innerHeight || 800, n = 0;
    var list = document.querySelectorAll("[data-src]:not([data-jf-img])");
    for (var i = 0; i < list.length && n < 40; i++) {
      var el = list[i];
      if (el.tagName === "IMG" || el.closest(".jfh-root, .jfr-root, .jfm-back")) { el.setAttribute("data-jf-img", "skip"); continue; }   // our own pictures are loaded by their own code (a background on top of an <img> draws the picture twice)
      var r = el.getBoundingClientRect();
      if ((!r.width && !r.height) || r.bottom < -vh || r.top > vh * 2.5) continue;
      var url = el.getAttribute("data-src"); if (!url || /^data:/.test(url)) continue;
      el.setAttribute("data-jf-img", "1"); n++;
      (function (e2, u2) {
        var im = new Image();
        im.onload = function () {
          if (!e2.style.backgroundImage || e2.style.backgroundImage === "none") e2.style.backgroundImage = 'url("' + u2 + '")';
          [].forEach.call(e2.querySelectorAll("canvas"), function (cv) { cv.style.opacity = "0"; });
          e2.classList.add("lazy-image-fadein-fast");
        };
        im.src = u2;
      })(el, url);
    }
    var pg = document.querySelector(".itemDetailPage:not(.hide)");     // the big picture on a movie / show page
    if (pg) {
      var b = pg.querySelector(".itemBackdrop");
      if (b && (!b.style.backgroundImage || b.style.backgroundImage === "none")) {
        var g = document.querySelector(".backdropImage"), bi = g && getComputedStyle(g).backgroundImage;
        if (bi && bi !== "none") { b.style.backgroundImage = bi; b.style.backgroundSize = "cover"; b.style.backgroundPosition = "center 20%"; }
      }
    }
  }
  // Runs twice a second: whatever page is on screen gets its billboard/rows if they are missing.
  var tickN = 0;
  function tick() {
    if ((tickN++ & 1) === 0) forceImages();
    if (!userId()) return;
    if (isHome()) { var c = homeContainer(); if (c) ensureHome(c); return; }
    var scope = catScope(); if (scope) ensureCategory(scope);
  }
  function onRoute() {
    if (isHome()) { home.rowsDirty = true; tick(); }
    else { stopHeroes(); autoPlay(); var s = catScope(); if (s) tick(); }
  }

  // header: transparent on top of the picture, solid once the page is scrolled
  function onScroll() { document.documentElement.classList.toggle("jf-scrolled", (window.scrollY || document.documentElement.scrollTop) > 40); }
  window.addEventListener("scroll", onScroll, { passive: true }); onScroll();

  // own brand icon in the browser tab
  (function () {
    var svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ff3b3b"/><stop offset="1" stop-color="#c10f1d"/></linearGradient></defs><rect width="64" height="64" rx="16" fill="url(#g)"/><circle cx="32" cy="32" r="17" fill="none" stroke="#fff" stroke-width="5"/><path d="M28 24.5 41 32 28 39.5z" fill="#fff" stroke="#fff" stroke-width="2" stroke-linejoin="round"/></svg>';
    var url = "data:image/svg+xml," + encodeURIComponent(svg);
    function set() { [].forEach.call(document.querySelectorAll('link[rel~="icon"],link[rel="shortcut icon"],link[rel="apple-touch-icon"]'), function (l) { l.parentNode.removeChild(l); }); var l = document.createElement("link"); l.rel = "icon"; l.type = "image/svg+xml"; l.href = url; document.head.appendChild(l); }
    set(); setTimeout(set, 3000);
  })();

  window.addEventListener("hashchange", onRoute);
  document.addEventListener("viewshow", onRoute);
  setInterval(tick, 500);
})();
