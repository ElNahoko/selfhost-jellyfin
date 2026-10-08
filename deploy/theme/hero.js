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
  var ICON_SOUND_ON = '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0 0 14 8v8a4.5 4.5 0 0 0 2.5-4zM14 3.2v2.1a7 7 0 0 1 0 13.4v2.1a9 9 0 0 0 0-17.6z"/></svg>', ICON_SOUND_OFF = '<svg viewBox="0 0 24 24" width="20" height="20"><path fill="currentColor" d="M16.5 12A4.5 4.5 0 0 0 14 8v2.2l2.5 2.5V12zM19 12a7 7 0 0 1-.9 3.4l1.5 1.5A9 9 0 0 0 21 12a9 9 0 0 0-7-8.8v2.1a7 7 0 0 1 5 6.7zM4.3 3 3 4.3 7.7 9H3v6h4l5 5v-6.7l4.3 4.3a8.4 8.4 0 0 1-2.3 1.2v2.1a10.6 10.6 0 0 0 3.7-1.9l2 2 1.3-1.3L4.3 3zM12 4 9.9 6.1 12 8.2V4z"/></svg>';
  function speakerButton(cls, video) {          // previews start silent; this button lets you hear them
    var b = document.createElement("button"); b.type = "button"; b.className = cls; b.setAttribute("aria-label", "Sound on/off");
    function paint() { b.innerHTML = video.muted ? ICON_SOUND_OFF : ICON_SOUND_ON; }
    paint();
    b.addEventListener("click", function (e) { e.stopPropagation(); video.muted = !video.muted; if (!video.muted) video.volume = 0.8; paint(); });
    return b;
  }
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
  function attachPreview(host, info, maxMs, cls, onState, onVideo) {
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
        var sp = null;
        heroStop = attachPreview(slideEl.querySelector(".jfh-bg"), info, 30000, "jfh-vid", function (on) {
          state.previewing = on; if (sp && !on && sp.parentNode) sp.parentNode.removeChild(sp);
        }, function (v) { sp = speakerButton("jfh-mute", v); slideEl.appendChild(sp); });
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
    if (b.dataset.act === "play") play(s.dataset.id, s.dataset.server); else openModal(s.dataset.id);
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
    return '<a class="jfr-card jfr-top" data-id="' + it.Id + '" data-server="' + (it.ServerId || "") + '" href="' + href(it) + '" title="' + esc(it.Name) + '"><span class="jfr-num">' + n + "</span>" +
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
        if (all.length >= 4) rows.push({ title: "Top 10 on Lumio", items: all.slice(0, 10), top: true });
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
  var rowsBusy = false, rowsDirty = true;
  function buildRows(c) {
    if (rowsBusy) return; rowsBusy = true;
    loadRows().then(function () {
      rowsBusy = false; rowsDirty = false;
      var c2 = container(); if (!c2 || !isHome()) return;
      var old = document.getElementById("jfRows"); if (old) old.remove();
      if (!rowsState.data.length) return;
      var wrap = document.createElement("div"); wrap.id = "jfRows";
      wrap.innerHTML = rowsState.data.map(rowHtml).join("");
      wrap.addEventListener("click", onRowsClick);
      if (!(window.matchMedia && window.matchMedia("(hover: none)").matches) && !reduceMotion) wireCardPreviews(wrap);
      var hero = document.getElementById("jfHero");
      if (hero && hero.parentNode === c2) c2.insertBefore(wrap, hero.nextSibling); else c2.insertBefore(wrap, c2.firstChild);
    }, function () { rowsBusy = false; });
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
      if (b.dataset.act === "play") play(card.dataset.id, card.dataset.server); else openModal(card.dataset.id);
      return;
    }
    var k = e.target.closest(".jfr-card");
    if (k && k.dataset.id) { e.preventDefault(); openModal(k.dataset.id); }   // an episode id opens its series with the episode highlighted
  }


  /* ---------- detail pop-up: click a title and get a panel instead of a page change ---------- */
  var modal = { el: null, stopPreview: null, token: 0 };
  var DETAIL_F = "Overview,Genres,Studios,People,Taglines,CommunityRating,OfficialRating,RunTimeTicks,ProductionYear,MediaSources,ProviderIds,RemoteTrailers";
  function fmtMin(ticks) { return runtime(ticks); }
  function closeModal() {
    if (!modal.el) return;
    if (modal.stopPreview) { modal.stopPreview(); modal.stopPreview = null; }
    modal.token++; document.documentElement.classList.remove("jfm-open");
    var el = modal.el; modal.el = null; el.classList.remove("on"); setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 220);
    document.removeEventListener("keydown", onModalKey, true);
  }
  window.addEventListener("hashchange", function () { closeModal(); });                 // leaving the page closes the panel
  function onModalKey(e) { if (e.key === "Escape") { e.stopPropagation(); closeModal(); } }
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
    closeModal();
    if (heroStop) { heroStop(); heroStop = null; } clearTimeout(heroTimer); state.previewing = false;      // the banner behind the panel stops its own preview
    var token = ++modal.token;
    var back = document.createElement("div"); back.className = "jfm-back"; back.setAttribute("role", "dialog"); back.setAttribute("aria-modal", "true");
    back.innerHTML = '<div class="jfm-panel"><button type="button" class="jfm-close" aria-label="Close">✕</button><div class="jfm-loading">Loading…</div></div>';
    document.body.appendChild(back); modal.el = back; document.documentElement.classList.add("jfm-open");
    requestAnimationFrame(function () { back.classList.add("on"); });
    back.addEventListener("mousedown", function (e) { if (e.target === back) closeModal(); });
    back.querySelector(".jfm-close").addEventListener("click", closeModal);
    document.addEventListener("keydown", onModalKey, true);
    c.getItem(uid, id).then(function (item) {
      if (token !== modal.token) return;
      if (item.Type === "Episode" && item.SeriesId) return c.getItem(uid, item.SeriesId).then(function (s2) { if (token === modal.token) renderModal(back, s2, item.Id); });
      renderModal(back, item, null);
    }, function () { closeModal(); details(id); });
  }
  function renderModal(back, item, focusEp) {
    var c = client(), uid = userId(), token = modal.token;
    var tags = item.ImageTags || {}, bd = item.BackdropImageTags || [];
    var bgUrl = bd.length ? img(item.Id, "Backdrop", bd[0], 1280) : tags.Primary ? img(item.Id, "Primary", tags.Primary, 800) : "";
    var head = tags.Logo ? '<img class="jfm-logo" alt="' + esc(item.Name) + '" src="' + img(item.Id, "Logo", tags.Logo, 600) + '">' : '<h2 class="jfm-title">' + esc(item.Name) + "</h2>";
    var ud = item.UserData || {}, resume = ud.PlaybackPositionTicks > 0 || (ud.UnplayedItemCount != null && ud.UnplayedItemCount < (item.RecursiveItemCount || 1e9) && item.Type === "Series");
    var meta = [];
    if (item.CommunityRating) meta.push('<b class="jfm-rate">★ ' + item.CommunityRating.toFixed(1) + "</b>");
    if (item.ProductionYear) meta.push("<span>" + item.ProductionYear + (item.Type === "Series" ? (item.EndDate ? " – " + new Date(item.EndDate).getFullYear() : " – now") : "") + "</span>");
    var rt = fmtMin(item.RunTimeTicks); if (rt && item.Type !== "Series") meta.push("<span>" + rt + "</span>");
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
      '<button type="button" class="jfm-close" aria-label="Close">✕</button>' +
      '<div class="jfm-hero"><div class="jfm-bg"' + (bgUrl ? ' style="background-image:url(\'' + bgUrl + '\')"' : "") + '></div><div class="jfm-grad"></div>' +
      '<div class="jfm-head">' + head + '<div class="jfm-actions"><button type="button" class="jfm-play"><b>▶</b>' + (resume ? "Resume" : "Play") + '</button>' +
      '<button type="button" class="jfm-round jfm-fav' + (fav ? " on" : "") + '" aria-label="My list" title="My list">' + (fav ? "✓" : "＋") + '</button>' +
      (trailerUrl ? '<a class="jfm-trailer" href="' + esc(trailerUrl) + '" target="_blank" rel="noopener noreferrer" title="Opens the trailer on the web (new tab)">Trailer</a>' : "") +
      '<a class="jfm-round jfm-open-page" href="#/details?id=' + item.Id + '" title="Open the full page" aria-label="Open the full page">⤢</a></div></div></div>' +
      '<div class="jfm-body"><div class="jfm-main"><div class="jfm-meta">' + meta.join("") + "</div>" +
      (item.Taglines && item.Taglines[0] ? '<p class="jfm-tag">' + esc(item.Taglines[0]) + "</p>" : "") +
      '<p class="jfm-over">' + esc(item.Overview || "No description yet.") + '</p></div><div class="jfm-side">' + side + "</div></div>" +
      (item.Type === "Series" ? '<div class="jfm-eps"><div class="jfm-eps-head"><h3>Episodes</h3><select class="jfm-season" aria-label="Season"></select></div><div class="jfm-eplist"><div class="jfm-loading">Loading…</div></div></div>' : "") +
      '<div class="jfm-more" hidden><h3>More like this</h3><div class="jfm-grid"></div></div>';
    var panel = back.querySelector(".jfm-panel");
    panel.querySelector(".jfm-close").addEventListener("click", closeModal);
    panel.querySelector(".jfm-play").addEventListener("click", function () { closeModal(); playFirst(item); });
    panel.querySelector(".jfm-open-page").addEventListener("click", function () { closeModal(); });
    panel.querySelector(".jfm-fav").addEventListener("click", function (e) {
      var b = e.currentTarget, on = !b.classList.contains("on");
      c.updateFavouriteStatus(uid, item.Id, on).then(function () { b.classList.toggle("on", on); b.textContent = on ? "✓" : "＋"; try { sessionStorage.removeItem("jfRows3:" + uid); } catch (x) {} });
    });
    // silent preview behind the top picture
    if (!reduceMotion) setTimeout(function () {
      if (token !== modal.token) return;
      resolvePreview(item).then(function (info) { if (info && token === modal.token) modal.stopPreview = attachPreview(panel.querySelector(".jfm-bg"), info, 30000, "jfm-vid", null, function (v) { panel.querySelector(".jfm-hero").appendChild(speakerButton("jfm-mute", v)); }); });
    }, 900);
    // similar titles
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
    if (item.Type === "Series") loadEpisodes(panel, item, focusEp);
  }
  function loadEpisodes(panel, series, focusEp) {
    var c = client(), uid = userId(), token = modal.token;
    var sel = panel.querySelector(".jfm-season"), list = panel.querySelector(".jfm-eplist");
    c.getJSON(c.getUrl("Shows/" + series.Id + "/Seasons", { userId: uid })).then(function (r) {
      if (token !== modal.token) return;
      var seasons = r.Items || [];
      if (!seasons.length) { list.innerHTML = '<div class="jfm-loading">No episodes yet.</div>'; return; }
      sel.innerHTML = seasons.map(function (s) { return '<option value="' + s.Id + '">' + esc(s.Name) + "</option>"; }).join("");
      if (seasons.length < 2) sel.style.display = "none";
      function show(seasonId) {
        list.innerHTML = '<div class="jfm-loading">Loading…</div>';
        c.getJSON(c.getUrl("Shows/" + series.Id + "/Episodes", { userId: uid, seasonId: seasonId, Fields: "Overview,RunTimeTicks", EnableImageTypes: "Primary", ImageTypeLimit: 1 })).then(function (x) {
          if (token !== modal.token) return;
          list.innerHTML = (x.Items || []).map(function (e) {
            var pct = e.UserData && e.UserData.PlayedPercentage, t = e.ImageTags && e.ImageTags.Primary;
            return '<div class="jfm-ep' + (e.Id === focusEp ? " focus" : "") + '" data-id="' + e.Id + '" tabindex="0"><span class="jfm-epn">' + (e.IndexNumber || "") + '</span>' +
              '<span class="jfm-epimg">' + (t ? '<img loading="lazy" alt="" src="' + img(e.Id, "Primary", t, 360) + '">' : "") + (pct ? '<i class="jfm-epprog"><b style="width:' + Math.round(pct) + '%"></b></i>' : "") + "</span>" +
              '<span class="jfm-ept"><b>' + esc(e.Name) + "</b><em>" + (fmtMin(e.RunTimeTicks) || "") + (e.UserData && e.UserData.Played ? " · ✓ watched" : "") + "</em><small>" + esc(e.Overview || "") + "</small></span></div>";
          }).join("") || '<div class="jfm-loading">No episodes in this season.</div>';
          [].forEach.call(list.querySelectorAll(".jfm-ep"), function (row) {
            function go() { closeModal(); play(row.dataset.id, series.ServerId); }
            row.addEventListener("click", go); row.addEventListener("keydown", function (e) { if (e.key === "Enter") go(); });
          });
          var f = list.querySelector(".jfm-ep.focus"); if (f) f.scrollIntoView({ block: "nearest" });
        }, function () { list.innerHTML = '<div class="jfm-loading">Could not load episodes.</div>'; });
      }
      var start = seasons[0].Id;
      if (focusEp) { c.getItem(uid, focusEp).then(function (e) { if (e.SeasonId) { sel.value = e.SeasonId; show(e.SeasonId); } else show(start); }, function () { show(start); }); }
      else { var p = seasons.filter(function (s) { return s.IndexNumber > 0; })[0]; show((p || seasons[0]).Id); }
      sel.addEventListener("change", function () { show(sel.value); });
    }, function () { list.innerHTML = '<div class="jfm-loading">Could not load episodes.</div>'; });
  }
  // library grids (Movies / TV Shows lists) open the same panel
  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return;
    if (document.getElementById("jfRows") && e.target.closest("#jfRows, #jfHero")) return;
    var card = e.target.closest(".card[data-id]"); if (!card) return;
    if (e.target.closest("button, .cardOverlayButton, .cardOverlayFab-primary, .cardIndicators, input, select")) return;
    var type = card.getAttribute("data-type"); if (type !== "Movie" && type !== "Series") return;
    if (!e.target.closest('[data-action="link"], .cardImageContainer, .cardText, a')) return;
    e.preventDefault(); e.stopPropagation(); openModal(card.getAttribute("data-id"));
  }, true);

  function container() { return document.querySelector(".homePage:not(.hide) .homeSectionsContainer") || document.querySelector(".homePage .homeSectionsContainer"); }
  function isHome() { return /^#\/home/.test(location.hash) || location.hash === "" || location.hash === "#/"; }

  var heroBusy = false;
  function buildHero(c) {
    var existing = document.getElementById("jfHero");
    if (existing) { if (existing.parentNode === c) { if (!state.timer) start(); return; } existing.remove(); }
    if (heroBusy) return; heroBusy = true;
    var fresh = state.items.length && Date.now() - state.loadedAt < REFRESH_MS;
    (fresh ? Promise.resolve() : load()).then(function () {
      heroBusy = false;
      var c2 = container(); if (!c2 || !state.items.length || !isHome() || document.getElementById("jfHero")) return;
      state.root = build(); c2.insertBefore(state.root, c2.firstChild); show(0); start();
    }, function () { heroBusy = false; });
  }
  // Safety net for pictures: Jellyfin fills posters/backdrops through a lazy loader that can stall (background tab, slow
  // first paint). Anything with a data-src near the screen is loaded here too; same picture, so doing it twice is harmless.
  function forceImages() {
    var vh = window.innerHeight || 800;
    [].forEach.call(document.querySelectorAll("[data-src]:not([data-jf-img])"), function (el) {
      var r = el.getBoundingClientRect();
      if ((!r.width && !r.height) || r.bottom < -vh || r.top > vh * 2.5) return;
      var url = el.getAttribute("data-src"); if (!url || /^data:/.test(url)) return;
      el.setAttribute("data-jf-img", "1");
      var im = new Image();
      im.onload = function () {
        if (!el.style.backgroundImage || el.style.backgroundImage === "none") el.style.backgroundImage = 'url("' + url + '")';
        [].forEach.call(el.querySelectorAll("canvas"), function (cv) { cv.style.opacity = "0"; });
        el.classList.add("lazy-image-fadein-fast");
      };
      im.src = url;
    });
    var pg = document.querySelector(".itemDetailPage:not(.hide)");     // the big picture on a movie / show page
    if (pg) {
      var b = pg.querySelector(".itemBackdrop");
      if (b && (!b.style.backgroundImage || b.style.backgroundImage === "none")) {
        var g = document.querySelector(".backdropImage"), bi = g && getComputedStyle(g).backgroundImage;
        if (bi && bi !== "none") { b.style.backgroundImage = bi; b.style.backgroundSize = "cover"; b.style.backgroundPosition = "center 20%"; }
      }
    }
  }
  // Runs twice a second: whenever the home page is on screen and something of ours is missing, put it there.
  function tick() {
    forceImages();
    if (!isHome()) return;
    var c = container(); if (!c || !userId()) return;
    buildHero(c);
    if (!document.getElementById("jfRows") || rowsDirty) buildRows(c);
    tidyHome();
  }
  function onRoute() {
    if (isHome()) { rowsDirty = true; tick(); } else { stop(); autoPlay(); }
  }

  // Netflix header: transparent on top of the picture, solid once the page is scrolled
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
