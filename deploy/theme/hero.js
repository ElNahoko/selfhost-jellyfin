/* Featured banner for the Jellyfin home page. Self-contained, no external requests.
   Reads items through the logged-in user's own ApiClient, so it only shows what that user may see.
   Loaded by the patched index.html (see scripts/06-patch-web.sh). MIT licensed, part of selfhost-jellyfin. */
(function () {
  "use strict";
  if (window.__jfHeroLoaded) return;
  window.__jfHeroLoaded = true;

  var COUNT = 8, INTERVAL = 9000, REFRESH_MS = 10 * 60 * 1000;
  var state = { items: [], idx: 0, timer: null, root: null, loadedAt: 0, loading: false };

  function client() { return window.ApiClient; }
  function userId() { var c = client(); return c && c.getCurrentUserId && c.getCurrentUserId(); }
  function img(id, type, tag, w) {
    return client().getUrl("Items/" + id + "/Images/" + type + "/0", { tag: tag, maxWidth: w, quality: 88 });
  }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function shuffle(a) { for (var i = a.length - 1; i > 0; i--) { var j = Math.floor(Math.random() * (i + 1)), t = a[i]; a[i] = a[j]; a[j] = t; } return a; }
  function runtime(ticks) { if (!ticks) return ""; var m = Math.round(ticks / 600000000); return m >= 60 ? Math.floor(m / 60) + "h " + (m % 60 ? (m % 60) + "min" : "") : m + " min"; }

  function load() {
    var c = client(), uid = userId();
    if (!c || !uid || state.loading) return Promise.resolve();
    state.loading = true;
    return c.getItems(uid, {
      IncludeItemTypes: "Movie,Series", Recursive: true, SortBy: "DateCreated", SortOrder: "Descending", Limit: 40,
      Fields: "Overview,Genres,CommunityRating,OfficialRating,RunTimeTicks,ProductionYear,BackdropImageTags",
      ImageTypeLimit: 1, EnableImageTypes: "Backdrop,Logo,Primary"
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
      if (it.ProductionYear) meta.push("<span>" + it.ProductionYear + "</span>");
      var rt = runtime(it.RunTimeTicks); if (rt) meta.push("<span>" + rt + "</span>");
      if (it.CommunityRating) meta.push('<span class="jfh-star">★ ' + it.CommunityRating.toFixed(1) + "</span>");
      if (it.OfficialRating) meta.push('<span class="jfh-badge">' + esc(it.OfficialRating) + "</span>");
      var title = it.ImageTags && it.ImageTags.Logo
        ? '<img class="jfh-logo" alt="' + esc(it.Name) + '" src="' + img(it.Id, "Logo", it.ImageTags.Logo, 700) + '">'
        : '<h2 class="jfh-title">' + esc(it.Name) + "</h2>";
      var poster = it.ImageTags && it.ImageTags.Primary
        ? '<img class="jfh-poster" alt="" src="' + img(it.Id, "Primary", it.ImageTags.Primary, 500) + '">' : "";
      return '<div class="jfh-slide' + (n === 0 ? " on" : "") + '" data-id="' + it.Id + '" data-server="' + it.ServerId + '" role="group" aria-label="' + (n + 1) + " of " + state.items.length + '">' +
        '<div class="jfh-bg" style="background-image:url(\'' + img(it.Id, "Backdrop", it.BackdropImageTags[0], 1920) + '\')"></div>' +
        '<div class="jfh-inner">' + poster +
        '<div class="jfh-info">' + title +
        '<div class="jfh-meta">' + meta.join('<i></i>') + "</div>" +
        (it.Genres && it.Genres.length ? '<div class="jfh-genres">' + it.Genres.slice(0, 3).map(esc).join(" · ") + "</div>" : "") +
        (it.Overview ? '<p class="jfh-over">' + esc(it.Overview) + "</p>" : "") +
        '<div class="jfh-actions"><button type="button" class="jfh-btn jfh-play" data-act="play">▶&nbsp; Play</button>' +
        '<button type="button" class="jfh-btn jfh-more" data-act="more">More info</button></div>' +
        "</div></div></div>";
    }).join("");
    var dots = state.items.map(function (_, n) { return '<button type="button" class="jfh-dot' + (n === 0 ? " on" : "") + '" data-go="' + n + '" aria-label="Show item ' + (n + 1) + '"></button>'; }).join("");
    root.innerHTML = slides +
      '<button type="button" class="jfh-nav jfh-prev" data-step="-1" aria-label="Previous">‹</button>' +
      '<button type="button" class="jfh-nav jfh-next" data-step="1" aria-label="Next">›</button>' +
      '<div class="jfh-dots">' + dots + "</div>";
    root.addEventListener("click", onClick);
    root.addEventListener("mouseenter", stop); root.addEventListener("mouseleave", start);
    return root;
  }

  function show(n) {
    var r = state.root; if (!r) return;
    var count = state.items.length; state.idx = (n + count) % count;
    [].forEach.call(r.querySelectorAll(".jfh-slide"), function (s, i) { s.classList.toggle("on", i === state.idx); });
    [].forEach.call(r.querySelectorAll(".jfh-dot"), function (d, i) { d.classList.toggle("on", i === state.idx); });
  }
  function start() { stop(); if (state.items.length > 1) state.timer = setInterval(function () { show(state.idx + 1); }, INTERVAL); }
  function stop() { if (state.timer) { clearInterval(state.timer); state.timer = null; } }

  function details(id, server) { window.location.hash = "#/details?id=" + id + (server ? "&serverId=" + server : ""); }
  function onClick(e) {
    var b = e.target.closest("button"); if (!b) return;
    if (b.dataset.step) return show(state.idx + parseInt(b.dataset.step, 10));
    if (b.dataset.go) return show(parseInt(b.dataset.go, 10));
    var s = b.closest(".jfh-slide"); if (!s) return;
    if (b.dataset.act === "play") { try { sessionStorage.setItem("jfHeroPlay", s.dataset.id); } catch (x) {} }
    details(s.dataset.id, s.dataset.server);
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

  function container() { return document.querySelector(".homePage .homeSectionsContainer"); }

  function mount() {
    var c = container(); if (!c) return;
    var existing = document.getElementById("jfHero");
    var fresh = Date.now() - state.loadedAt < REFRESH_MS;
    if (existing && fresh) { start(); return; }
    if (existing) existing.remove();
    load().then(function () {
      var c2 = container(); if (!c2 || !state.items.length) return;
      var old = document.getElementById("jfHero"); if (old) old.remove();
      state.root = build();
      c2.insertBefore(state.root, c2.firstChild);
      start();
    });
  }

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
