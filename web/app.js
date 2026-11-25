/* AnyLoc frontend v2
 * Features:
 *  - Layer switcher: CartoDB Dark (default) / Light / OSM / Esri Satellite
 *  - Smart search: CJK -> Nominatim (multilingual), Latin -> Photon (fuzzy),
 *    plus "lat, lon" direct; keyboard nav; history + favorites (localStorage)
 *  - Free move: click-teleport, joystick, WASD/arrows
 *  - Route walk: click waypoints A->B->C, OSRM road polyline, auto-walk with
 *    speed control, progress bar, loop option
 * All map/search/routing services are free and CORS-enabled (verified).
 */
"use strict";

const $ = (id) => document.getElementById(id);
const fmt = (n, d = 6) => Number(n).toFixed(d);
const clampLon = (x) => (x > 180 ? x - 360 : x < -180 ? x + 360 : x);
const hasCJK = (s) => /[　-鿿가-힯＀-￯]/.test(s);
// UI languages whose speakers read CJK scripts natively -- used to keep a CJK
// place name in its own script instead of transliterating it.
const CJK_LANGS = new Set(["zh", "ja", "ko"]);

// ============================================================================
// Theme — light / dark, follows system by default, manual override persisted
// ============================================================================
const SUN_SVG = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
const MOON_SVG = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
function systemTheme() {
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
let THEME = localStorage.getItem("anyloc_theme") || "system";
function effectiveTheme() { return THEME === "system" ? systemTheme() : THEME; }
function applyTheme() {
  const eff = effectiveTheme();
  document.documentElement.setAttribute("data-theme", eff);
  const btn = document.getElementById("themeBtn");
  if (btn) btn.innerHTML = eff === "dark" ? MOON_SVG : SUN_SVG;
}
function setupThemeToggle() {
  const btn = document.getElementById("themeBtn");
  if (btn) btn.addEventListener("click", () => {
    // cycle: whatever is showing now -> flip to the opposite explicit theme
    THEME = effectiveTheme() === "dark" ? "light" : "dark";
    localStorage.setItem("anyloc_theme", THEME);
    applyTheme();
  });
  // react to system changes when in "system" mode
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      if (THEME === "system") applyTheme();
    });
  }
}
applyTheme(); // apply ASAP to avoid flash

// ============================================================================
// HELP — getting-started guide
// ============================================================================
// A modal rather than an inline card: the steps are long enough to push the
// whole panel around, and the user reads them once. Content is plain markup in
// index.html carrying data-i18n, so applyI18n() translates it like everything
// else and no strings live in here.
function setupHelp() {
  const ov = document.getElementById("helpOv");
  if (!ov) return;                       // markup missing -> feature just absent
  const open = () => { ov.classList.add("show"); document.getElementById("helpClose").focus(); };
  const close = () => ov.classList.remove("show");

  const btn = document.getElementById("helpBtn");
  if (btn) btn.addEventListener("click", open);
  document.getElementById("helpClose").addEventListener("click", close);

  // Click the backdrop to dismiss, but not a click inside the dialog: the
  // listener is on the overlay, so compare against the target itself.
  ov.addEventListener("click", (e) => { if (e.target === ov) close(); });

  // Escape closes. Bound on document because focus may sit anywhere inside.
  // Guarded on .show so this does not swallow Escape for the favorite-name
  // editor (app.js beginFavEdit), which has its own Escape-cancels handling.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && ov.classList.contains("show")) { close(); e.stopPropagation(); }
  });

  // Platform tabs. Plain show/hide — both sections are already in the DOM and
  // translated, so switching costs nothing and survives a language change.
  const tabs = [
    [document.getElementById("helpTabIos"), document.getElementById("helpSecIos")],
    [document.getElementById("helpTabAnd"), document.getElementById("helpSecAnd")],
  ];
  tabs.forEach(([tab]) => tab && tab.addEventListener("click", () => {
    tabs.forEach(([t, sec]) => {
      const on = t === tab;
      t.classList.toggle("on", on);
      sec.classList.toggle("on", on);
    });
  }));
}

// ============================================================================
// i18n — 多语言
// ============================================================================
const I18N = window.I18N || { zh: {} };
let LANG = localStorage.getItem("anyloc_lang");
if (!LANG || !I18N[LANG]) {
  // Auto-detect from the browser, default zh. Matched against whatever I18N
  // actually ships, so adding a language to i18n.js is enough to make it
  // auto-detect -- there is no second list here to forget to update.
  const nav = (navigator.language || "zh").toLowerCase();
  const base = nav.split("-")[0];
  LANG = Object.keys(I18N).find((c) => c === base) || "zh";
}
function t(key) {
  const d = I18N[LANG] || {};
  if (key in d) return d[key];
  const en = I18N.en || {};
  if (key in en) return en[key];
  const zh = I18N.zh || {};
  return key in zh ? zh[key] : key;
}
function applyI18n() {
  document.documentElement.lang = LANG === "zh" ? "zh-CN" : LANG;
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.getAttribute("data-i18n"));
  });
  document.querySelectorAll("[data-i18n-html]").forEach((el) => {
    el.innerHTML = t(el.getAttribute("data-i18n-html"));
  });
  document.querySelectorAll("[data-i18n-ph]").forEach((el) => {
    el.setAttribute("placeholder", t(el.getAttribute("data-i18n-ph")));
  });
  document.querySelectorAll("[data-i18n-title]").forEach((el) => {
    el.setAttribute("title", t(el.getAttribute("data-i18n-title")));
  });
}
function setupLangSelector() {
  const wrap = $("langWrap"), btn = $("langBtn"), menu = $("langMenu"), cur = $("langCur");
  if (!wrap || !btn || !menu) return;
  function renderMenu() {
    menu.innerHTML = "";
    Object.keys(I18N).forEach((code) => {
      const b = document.createElement("button");
      if (code === LANG) b.className = "on";
      b.innerHTML = `<span>${I18N[code]._label || code}</span><span class="tick">✓</span>`;
      b.addEventListener("click", () => {
        LANG = code;
        localStorage.setItem("anyloc_lang", LANG);
        cur.textContent = I18N[code]._label || code;
        wrap.classList.remove("open");
        applyI18n();
        refreshDynamicLabels();
        renderMenu();
      });
      menu.appendChild(b);
    });
  }
  cur.textContent = (I18N[LANG] && I18N[LANG]._label) || LANG;
  renderMenu();
  btn.addEventListener("click", (e) => { e.stopPropagation(); wrap.classList.toggle("open"); });
  document.addEventListener("click", (e) => { if (!wrap.contains(e.target)) wrap.classList.remove("open"); });
}
// re-apply strings that are generated in JS (not static HTML)
function refreshDynamicLabels() {
  try { relabelLayers(); } catch (e) {}
  try { renderFavs(); } catch (e) {}
  try { renderWaypoints(); } catch (e) {}
  try { renderIosConnection(); } catch (e) {}
  try { poll(); } catch (e) {}
}

// ---- config (amap key) ----
const AMAP_KEY = (window.ANYLOC_CONFIG && window.ANYLOC_CONFIG.amapKey || "").trim();

// ---- GCJ-02 (高德/火星坐标) -> WGS-84 (真实/GPS坐标) ----
// Amap returns GCJ-02, but the iPhone GPS + our map tiles use WGS-84. Without
// this conversion, spoofed locations from Amap search would be off by ~500m.
function outOfChina(lng, lat) {
  return !(lng > 73.66 && lng < 135.05 && lat > 3.86 && lat < 53.55);
}
function _tfLat(x, y) {
  let r = -100 + 2*x + 3*y + 0.2*y*y + 0.1*x*y + 0.2*Math.sqrt(Math.abs(x));
  r += (20*Math.sin(6*x*Math.PI) + 20*Math.sin(2*x*Math.PI)) * 2/3;
  r += (20*Math.sin(y*Math.PI) + 40*Math.sin(y/3*Math.PI)) * 2/3;
  r += (160*Math.sin(y/12*Math.PI) + 320*Math.sin(y*Math.PI/30)) * 2/3;
  return r;
}
function _tfLng(x, y) {
  let r = 300 + x + 2*y + 0.1*x*x + 0.1*x*y + 0.1*Math.sqrt(Math.abs(x));
  r += (20*Math.sin(6*x*Math.PI) + 20*Math.sin(2*x*Math.PI)) * 2/3;
  r += (20*Math.sin(x*Math.PI) + 40*Math.sin(x/3*Math.PI)) * 2/3;
  r += (150*Math.sin(x/12*Math.PI) + 300*Math.sin(x/30*Math.PI)) * 2/3;
  return r;
}
function gcj02ToWgs84(lng, lat) {
  if (outOfChina(lng, lat)) return [lng, lat];
  const a = 6378245.0, ee = 0.00669342162296594323;
  let dLat = _tfLat(lng - 105, lat - 35), dLng = _tfLng(lng - 105, lat - 35);
  const radLat = lat / 180 * Math.PI;
  let magic = Math.sin(radLat); magic = 1 - ee*magic*magic;
  const sqrtMagic = Math.sqrt(magic);
  dLat = (dLat * 180) / ((a * (1 - ee)) / (magic * sqrtMagic) * Math.PI);
  dLng = (dLng * 180) / (a / sqrtMagic * Math.cos(radLat) * Math.PI);
  return [lng - dLng, lat - dLat];
}

// ============================================================================
// EXTERNAL LINKS — never navigate the top-level frame
// ============================================================================
// This window has no address bar and no Back button, so ANY top-level
// navigation strands the user with no way home. Leaflet's attribution control
// ships an <a href="https://leafletjs.com"> with no target, and pywebview only
// intercepts window.open / target=_blank. A delegated CAPTURE-phase listener
// beats Leaflet's own handlers and also covers any anchor added later (popups,
// tile-layer attributions), which a one-time patch of the existing DOM would
// miss. The attribution TEXT stays visible — that is a licensing obligation;
// only the navigation is cancelled.
document.addEventListener("click", (e) => {
  const a = e.target.closest && e.target.closest("a[href]");
  if (!a) return;
  if (/^https?:/i.test(a.getAttribute("href") || "")) {
    e.preventDefault();
  }
}, true);

// ============================================================================
// MAP + LAYERS
// ============================================================================
// Fallback opening view (Apple Park) for when IP geolocation is unavailable --
// offline, blocked, or still in flight. See locateOnStartup() at the bottom.
const START = [37.3349, -122.0090];

// True once the user has done anything positional. IP geolocation is slow
// (a network round trip), so it can land AFTER the user has already clicked
// the map -- and yanking the view away at that point would be worse than
// opening on the default. Any real interaction cancels it.
let userMovedMap = false;

// Leaflet is vendored locally, but a missing/corrupt file would otherwise throw
// here and abort the whole script — which used to kill the INIT block below and
// leave a half-rendered panel with an empty language menu and no explanation.
// INIT now runs FIRST (see the bottom of this file), and the map bootstrap is
// wrapped so a failure degrades to a translated banner with everything else
// still working.
let map = null;
function mapAlive() { return map !== null; }
function showMapFailure() {
  const el = $("mapFail");
  if (el) el.classList.add("show");
  const mapDiv = $("map");
  if (mapDiv) mapDiv.style.display = "none";
}

// Everything below that the rest of the app reaches into lives at module scope
// and is ASSIGNED by bootMap(); only the statements that evaluate `L.*` right
// away (the Control subclasses, the layer/marker construction) sit inside it.
let marker = null;
let tileLayers = null;
let layerSwitcherEl = null;
let compassEl = null;
let currentLayer = "standard";

const LAYER_I18N = { standard: "layer.standard", dark: "layer.dark", light: "layer.light", satellite: "layer.satellite" };
// a static thumbnail per layer (a single tile of a recognizable area)
const LAYER_THUMB = {
  standard: "https://tile.openstreetmap.org/6/51/24.png",
  dark: "https://a.basemaps.cartocdn.com/dark_all/6/51/24.png",
  light: "https://a.basemaps.cartocdn.com/light_all/6/51/24.png",
  satellite: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/6/24/51",
};

function selectLayer(id) {
  if (!map || id === currentLayer) return;
  map.removeLayer(tileLayers[currentLayer]);
  tileLayers[id].addTo(map);
  currentLayer = id;
  renderLayerSwitcher();
}
function renderLayerSwitcher() {
  const el = layerSwitcherEl;
  if (!el) return;   // no map booted -> no switcher to relabel
  const ids = Object.keys(tileLayers);
  el.innerHTML =
    `<button class="gm-layers-chip" title="${t("layer.switch") || ""}">
       <span class="gm-chip-thumb" style="background-image:url('${LAYER_THUMB[currentLayer]}')"></span>
       <span class="gm-chip-label">${t("layer.switch")}</span>
     </button>
     <div class="gm-layers-panel">
       ${ids.map((id) => `
         <button class="gm-layer-opt ${id === currentLayer ? "on" : ""}" data-layer="${id}">
           <span class="gm-opt-thumb" style="background-image:url('${LAYER_THUMB[id]}')"></span>
           <span class="gm-opt-label">${t(LAYER_I18N[id])}</span>
         </button>`).join("")}
     </div>`;
  // stop map from receiving these events (Leaflet reacts on mousedown/pointerdown too)
  L.DomEvent.disableClickPropagation(el);
  L.DomEvent.disableScrollPropagation(el);
  el.querySelectorAll(".gm-layer-opt").forEach((b) => {
    b.addEventListener("click", (e) => {
      e.preventDefault(); e.stopPropagation();
      selectLayer(b.dataset.layer); collapseLayers();
    });
  });
  const chip = el.querySelector(".gm-layers-chip");
  chip.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); el.classList.toggle("open"); });
}
function relabelLayers() { renderLayerSwitcher(); }
function collapseLayers() { if (layerSwitcherEl) layerSwitcherEl.classList.remove("open"); }
document.addEventListener("click", (e) => { if (layerSwitcherEl && !layerSwitcherEl.contains(e.target)) collapseLayers(); });
function updateCompass() {
  if (!compassEl || !map) return;
  const g = compassEl.querySelector(".cmp-rot");
  if (g) g.setAttribute("transform", `rotate(${map.getBearing ? map.getBearing() : 0} 20 20)`);
}

function bootMap() {
  map = L.map("map", {
    zoomControl: false, worldCopyJump: true,   // custom zoom buttons (see below)
    rotate: true, touchRotate: true, bearing: 0,
    rotateControl: false,   // we render our own styled compass
  }).setView(START, 13);

  // ---- custom zoom +/- buttons (fully self-styled, no Leaflet divider quirks) ----
  const ZoomControl = L.Control.extend({
    options: { position: "topleft" },
    onAdd() {
      const el = L.DomUtil.create("div", "zoom-ctl");
      el.innerHTML =
        '<button class="zoom-btn" id="zoomIn" title="放大" aria-label="zoom in">' +
        '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg></button>' +
        '<button class="zoom-btn" id="zoomOut" title="缩小" aria-label="zoom out">' +
        '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 12h14"/></svg></button>';
      L.DomEvent.disableClickPropagation(el);
      el.querySelector("#zoomIn").addEventListener("click", (e) => { e.preventDefault(); map.zoomIn(); });
      el.querySelector("#zoomOut").addEventListener("click", (e) => { e.preventDefault(); map.zoomOut(); });
      return el;
    },
  });
  map.addControl(new ZoomControl());

  // tile layers keyed by a stable id; display names come from i18n
  tileLayers = {
    standard: L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      { maxZoom: 19, attribution: "© OpenStreetMap" }),
    dark: L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
      { maxZoom: 20, subdomains: "abcd", attribution: "© OpenStreetMap © CARTO" }),
    light: L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
      { maxZoom: 20, subdomains: "abcd", attribution: "© OpenStreetMap © CARTO" }),
    satellite: L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { maxZoom: 19, attribution: "© Esri" }),
  };
  tileLayers[currentLayer].addTo(map);

  // Google-Maps-style thumbnail layer switcher (custom Leaflet control)
  const LayerSwitcher = L.Control.extend({
    options: { position: "bottomleft" },
    onAdd() {
      const el = L.DomUtil.create("div", "gm-layers");
      L.DomEvent.disableClickPropagation(el);
      L.DomEvent.disableScrollPropagation(el);
      layerSwitcherEl = el;
      renderLayerSwitcher();
      return el;
    },
  });
  map.addControl(new LayerSwitcher());

  // ---- Recenter button: fly back to the current spoofed marker ----
  const RecenterControl = L.Control.extend({
    options: { position: "bottomleft" },
    onAdd() {
      const el = L.DomUtil.create("div", "map-recenter");
      el.title = "回到当前位置";
      // navigation-style triangle/arrow icon
      el.innerHTML =
        '<svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor" stroke="currentColor" stroke-width="1" stroke-linejoin="round"><path d="M12 3l7.5 16.5a.6.6 0 0 1-.8.8L12 17l-6.7 3.3a.6.6 0 0 1-.8-.8z"/></svg>';
      L.DomEvent.disableClickPropagation(el);
      el.addEventListener("click", (e) => {
        e.preventDefault();
        map.setView([pos.lat, pos.lon], Math.max(map.getZoom(), 16), { animate: true });
      });
      return el;
    },
  });
  map.addControl(new RecenterControl());

  // ---- Compass: shows bearing, drag-free reset-to-north; map is rotatable ----
  const CompassControl = L.Control.extend({
    options: { position: "topleft" },
    onAdd() {
      const el = L.DomUtil.create("div", "map-compass");
      el.title = "指南针（点击回正北，按住拖动旋转地图）";
      el.innerHTML =
        '<svg viewBox="0 0 40 40" width="44" height="44">' +
        '<g class="cmp-rot">' +
        '<polygon points="20,5 24,20 20,17 16,20" fill="#ea4335"/>' +   // N (red)
        '<polygon points="20,35 16,20 20,23 24,20" fill="#9aa0a6"/>' +   // S (grey)
        '<text x="20" y="13" text-anchor="middle" font-size="7" font-weight="700" fill="#ea4335" font-family="sans-serif">N</text>' +
        '</g></svg>';
      compassEl = el;
      L.DomEvent.disableClickPropagation(el);
      // click = reset bearing to north
      el.addEventListener("click", (e) => {
        e.preventDefault();
        map.setBearing(0);
        updateCompass();
      });
      // drag to rotate
      let dragging = false, startX = 0, startBearing = 0;
      el.addEventListener("pointerdown", (e) => {
        dragging = true; startX = e.clientX; startBearing = map.getBearing();
        el.setPointerCapture(e.pointerId);
      });
      el.addEventListener("pointermove", (e) => {
        if (!dragging) return;
        map.setBearing(startBearing + (e.clientX - startX));
        updateCompass();
      });
      el.addEventListener("pointerup", () => { dragging = false; });
      updateCompass();
      return el;
    },
  });
  map.addControl(new CompassControl());
  map.on("rotate", updateCompass);

  // "me" marker (current spoofed position)
  const meIcon = L.divIcon({ className: "", html: '<div class="me-dot"></div>', iconSize: [18, 18], iconAnchor: [9, 9] });
  marker = L.marker(START, { draggable: true, icon: meIcon, zIndexOffset: 1000 }).addTo(map);

  // ---- map click — behaves differently per mode ----
  map.on("click", (e) => {
    if (mode === "route") addWaypoint(e.latlng.lat, e.latlng.lng);
    else teleport(e.latlng.lat, e.latlng.lng, false);
  });
  marker.on("drag", (e) => { const ll = e.target.getLatLng(); setMarker(ll.lat, ll.lng); });
  marker.on("dragend", () => { resumeSpoof(); sendLocation(pos.lat, pos.lon); });
}

// ============================================================================
// STATE
// ============================================================================
let pos = { lat: START[0], lon: START[1] };
let speedMps = 4;
let mode = "free";           // "free" | "route"
let curState = "idle";       // last known backend state (for button logic)
let currentPlatform = null;
let iosTransport = "usb", iosInitialized = false, iosInitialScan = false;
const iosDevices = { usb: null, wifi: null };
const iosSelection = { usb: "", wifi: "" };
let wifiSetupSelection = "";
let connectionBusy = "", clearBusy = false, connectHttpPending = false;
let awaitingConnection = null, connectionGate = false, statusGeneration = 0;
let connectionNotice = null;

// ---------- position send (coalesced, latest-wins) ----------
let pending = null, sending = false, lastSent = 0, sendInFlight = null;
// Set by "restore real GPS". The route walk streams ~10 POSTs/second, and the
// backend treats ANY /api/set as "cancel the pending clear" -- so without this
// gate the clear reached the phone and the very next frame of the walk put it
// straight back on the fake coordinate. The button looked dead because the
// spoof resumed within ~100ms. Cleared again the moment the user deliberately
// asks for a new position (map click, drag, joystick, walk).
let spoofSuspended = false;
function movementBlocked() {
  return Boolean(connectionGate || connectionBusy || awaitingConnection || clearBusy || curState === "connecting");
}
async function flush() {
  if (spoofSuspended || movementBlocked()) { pending = null; return; }
  if (sending) return;
  if (pending == null) return;
  const now = performance.now();
  if (now - lastSent < 90) { setTimeout(flush, 90 - (now - lastSent)); return; }
  const payload = pending; pending = null; sending = true; lastSent = now;
  try {
    sendInFlight = fetch("/api/set", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await sendInFlight;
  } catch (e) {}
  sendInFlight = null;
  sending = false;
  if (pending != null) flush();
}
function sendLocation(lat, lon) {
  if (spoofSuspended || movementBlocked()) return;
  pending = { lat, lon }; flush();
}
// Every deliberate "put the device HERE" action calls this first, so spoofing
// resumes only on user intent -- never on a stray in-flight frame.
function resumeSpoof() {
  if (movementBlocked()) return false;
  spoofSuspended = false;
  return true;
}

function setMarker(lat, lon, pan = false) {
  pos.lat = lat; pos.lon = clampLon(lon);
  // the map may not exist (Leaflet failed to load) — the coordinate readout and
  // the position sender still work, which is what actually spoofs the device
  if (marker) marker.setLatLng([pos.lat, pos.lon]);
  if (pan && map) map.panTo([pos.lat, pos.lon], { animate: true });
  const t = `${fmt(pos.lat)}, ${fmt(pos.lon)}`;
  $("curCoord").textContent = t;
  $("curCoordMini").textContent = "";
}
function teleport(lat, lon, pan = true) { userMovedMap = true; resumeSpoof(); setMarker(lat, lon, pan); sendLocation(lat, clampLon(lon)); }
// centre the map if there is one; a no-op when Leaflet failed to load
function focusMap(lat, lon, zoom = 17) { if (map) map.setView([lat, lon], zoom); }

// ============================================================================
// CONNECT / CLEAR
// ============================================================================
function suspendMovement() {
  stopWalk();
  releaseAllKeys();
  joyEnd();
  spoofSuspended = true;
  pending = null;
}
function setConnectionNotice(message, kind = "error", isKey = false) {
  connectionNotice = message ? { message, kind, isKey } : null;
  const el = $("connectionNotice");
  el.textContent = message ? (isKey ? t(message) : message) : "";
  el.className = "dm-banner inline" + (message ? " show " + kind : "");
}
async function connectionApi(url, options = {}, timeout = 60000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    const data = await response.json();
    if (!response.ok || data.ok === false) throw new Error(data.error || t("ios.requestFailed"));
    return data;
  } catch (error) {
    if (error.name === "AbortError") throw new Error(t("ios.requestTimeout"));
    throw error;
  } finally { clearTimeout(timer); }
}
function fillDeviceSelect(id, devices, selected) {
  const select = $(id);
  select.replaceChildren();
  const placeholder = devices === null ? "ios.scanFirst" : devices.length ? "ios.chooseDevice" : "ios.noDevices";
  select.add(new Option(t(placeholder), ""));
  (devices || []).forEach((device) => {
    const version = device.os_version ? " · iOS " + device.os_version : "";
    const shortId = device.id.length > 14 ? device.id.slice(0, 12) + "…" : device.id;
    const label = device.name ? device.name + version + " · " + shortId : device.id + version;
    select.add(new Option(label, device.id));
  });
  if ((devices || []).some((device) => device.id === selected)) select.value = selected;
  else if (!selected && devices && devices.length === 1) select.value = devices[0].id;
  return select.value;
}
function renderIosConnection() {
  $("iosTransport").value = iosTransport;
  $("wifiConnectionHelp").classList.toggle("hidden", iosTransport !== "wifi");
  $("iosDeviceLabel").textContent = t(iosTransport === "wifi" ? "ios.wifiDevice" : "ios.device");
  const previous = iosSelection[iosTransport];
  iosSelection[iosTransport] = fillDeviceSelect("iosDevice", iosDevices[iosTransport], previous);
  if (previous && previous !== iosSelection[iosTransport]) {
    connectionGate = true;
    suspendMovement();
  }
  wifiSetupSelection = fillDeviceSelect("wifiSetupDevice", iosDevices.usb, wifiSetupSelection || iosSelection.usb);
  if (connectionNotice) setConnectionNotice(connectionNotice.message, connectionNotice.kind, connectionNotice.isKey);
  updateConnectionControls();
}
function updateConnectionControls() {
  const isIos = currentPlatform === "ios";
  const busy = Boolean(connectionBusy || awaitingConnection || clearBusy || curState === "connecting");
  $("iosConnectionControls").classList.toggle("hidden", !isIos);
  ["iosTransport", "iosDevice", "wifiSetupDevice", "btnRefreshDevices", "btnRefreshUsbDevices"].forEach((id) => { $(id).disabled = busy; });
  $("btnEnableWifi").disabled = busy || !wifiSetupSelection;
  $("btnEnableWifi").textContent = t(connectionBusy === "enable" ? "ios.enablingWifi" : "ios.enableWifi");
  $("btnRefreshDevices").textContent = t(connectionBusy === "scan" ? "ios.scanning" : "ios.refresh");
  $("btnRefreshUsbDevices").textContent = t(connectionBusy === "scan-usb" ? "ios.scanning" : "ios.refresh");
  $("btnConnect").disabled = busy || (isIos && !iosSelection[iosTransport]);
  $("btnConnect").textContent = t(connectionBusy === "connect" || awaitingConnection || curState === "connecting" ? "btn.connecting" : curState === "connected" ? "btn.reconnect" : "btn.connect");
  $("btnClear").disabled = busy;
  $("btnClear").textContent = t(clearBusy ? "btn.resetting" : "btn.reset");
}
async function refreshIosDevices(forSetup = false) {
  if (connectionBusy || awaitingConnection || clearBusy || curState === "connecting") return;
  const transport = forSetup ? "usb" : iosTransport;
  connectionBusy = forSetup ? "scan-usb" : "scan";
  setConnectionNotice(null);
  updateConnectionControls();
  try {
    const data = await connectionApi("/api/ios/devices?transport=" + transport);
    iosDevices[transport] = (Array.isArray(data.devices) ? data.devices : []).filter((device) => device && typeof device.id === "string" && device.id);
    renderIosConnection();
    if (!iosDevices[transport].length) {
      setConnectionNotice(transport === "wifi" ? "ios.noWifiHint" : "ios.noUsbHint", "waiting", true);
      if (transport === "wifi") $("wifiSetup").open = true;
    }
  } catch (error) { setConnectionNotice(error.message || t("ios.requestFailed")); }
  finally { connectionBusy = ""; updateConnectionControls(); }
}
$("iosTransport").addEventListener("change", () => {
  // A selection is only applied by Connect. Keep movement paused meanwhile,
  // so a changed selector cannot accidentally move the previously-connected phone.
  iosTransport = $("iosTransport").value === "wifi" ? "wifi" : "usb";
  connectionGate = true;
  suspendMovement();
  renderIosConnection();
  if (iosTransport === "wifi" && !(iosDevices.wifi || []).length) $("wifiSetup").open = true;
  refreshIosDevices();
});
$("iosDevice").addEventListener("change", () => {
  iosSelection[iosTransport] = $("iosDevice").value;
  connectionGate = true;
  suspendMovement();
  setConnectionNotice("ios.ready", "waiting", true);
  updateConnectionControls();
});
$("wifiSetupDevice").addEventListener("change", () => {
  wifiSetupSelection = $("wifiSetupDevice").value;
  updateConnectionControls();
});
$("btnRefreshDevices").addEventListener("click", () => refreshIosDevices());
$("btnRefreshUsbDevices").addEventListener("click", () => refreshIosDevices(true));
$("btnEnableWifi").addEventListener("click", async () => {
  if ($("btnEnableWifi").disabled) return;
  connectionBusy = "enable";
  setConnectionNotice(null);
  updateConnectionControls();
  try {
    await connectionApi("/api/ios/wifi/enable", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_id: wifiSetupSelection || null }),
    });
    setConnectionNotice("ios.wifiEnabled", "enabled", true);
    $("wifiSetup").open = false;
  } catch (error) { setConnectionNotice(error.message || t("ios.requestFailed")); }
  finally { connectionBusy = ""; updateConnectionControls(); }
});
$("btnConnect").addEventListener("click", async () => {
  if ($("btnConnect").disabled) return;
  connectionBusy = "connect";
  connectionGate = true;
  suspendMovement();
  setConnectionNotice("ios.connectionPending", "waiting", true);
  updateConnectionControls();
  let movementTimer;
  try {
    // Wait for the last coordinate POST to finish before switching devices.
    // Aborting fetch would not guarantee that the server stopped processing it.
    if (sendInFlight) await Promise.race([
      sendInFlight.catch(() => {}),
      new Promise((_, reject) => { movementTimer = setTimeout(() => reject(new Error(t("ios.waitForMovement"))), 10000); }),
    ]);
    const choice = currentPlatform === "ios" ? { transport: iosTransport, device_id: iosSelection[iosTransport] || null } : {};
    connectHttpPending = true;
    statusGeneration++;
    await connectionApi("/api/connect", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...choice, reconnect: true }),
    });
    awaitingConnection = choice;
  } catch (error) { setConnectionNotice(error.message || t("ios.requestFailed")); }
  finally {
    clearTimeout(movementTimer);
    connectHttpPending = false;
    statusGeneration++;
    connectionBusy = "";
    updateConnectionControls();
    poll();
  }
});
$("btnClear").addEventListener("click", async () => {
  const btn = $("btnClear");
  if (btn.disabled) return;
  // Order matters. Stop the route walk and gate the sender BEFORE the clear
  // request goes out: the walk's next frame would otherwise re-spoof the device
  // the instant the clear landed.
  suspendMovement();
  clearBusy = true;
  updateConnectionControls();
  try {
    // If the session isn't live (idle/error/never connected), reconnect first
    // so the clear command actually reaches the device.
    if (curState !== "connected") {
      await fetch("/api/connect", { method: "POST" });
      // give the session a moment to come up before clearing
      for (let i = 0; i < 20; i++) {
        await new Promise((r) => setTimeout(r, 300));
        try {
          const s = await (await fetch("/api/status")).json();
          curState = s.state;
          if (s.state === "connected" || s.state === "error") break;
        } catch (e) {}
      }
    }
    await fetch("/api/clear", { method: "POST" });
  } catch (e) {}
  clearBusy = false;
  updateConnectionControls();
  setTimeout(poll, 300);
});

// ============================================================================
// SPEED
// ============================================================================
$("speeds").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-mps]");
  if (!b) return;
  speedMps = parseFloat(b.dataset.mps);
  [...$("speeds").children].forEach((x) => x.classList.toggle("on", x === b));
});

// ============================================================================
// MODE TABS
// ============================================================================
$("tabFree").addEventListener("click", () => switchMode("free"));
$("tabRoute").addEventListener("click", () => switchMode("route"));
function switchMode(m) {
  mode = m;
  $("tabFree").classList.toggle("on", m === "free");
  $("tabRoute").classList.toggle("on", m === "route");
  $("freePanel").classList.toggle("hidden", m !== "free");
  $("routePanel").classList.toggle("hidden", m !== "route");
  if (m === "free") { dir.x = 0; dir.y = 0; }
}

// ============================================================================
// SEARCH — dual engine + history/favorites
// ============================================================================
const searchBox = $("search");
const results = $("searchResults");
let searchTimer = null, activeIdx = -1, currentResults = [];

const LS = {
  get(k, d) { try { return JSON.parse(localStorage.getItem("anyloc_" + k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("anyloc_" + k, JSON.stringify(v)); } catch {} },
};
let history = LS.get("history", []);   // [{name, lat, lon}]
let favs = LS.get("favs", []);         // [{name, lat, lon}]

function parseLatLon(s) {
  const m = s.match(/^\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)\s*$/);
  if (!m) return null;
  const lat = parseFloat(m[1]), lon = parseFloat(m[2]);
  if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return null;
  return { lat, lon };
}

async function searchNominatim(q) {
  // Nominatim's accept-language takes any BCP-47 tag and simply falls back to
  // the local name when it has no translation -- so the UI language can go
  // straight through. Asking in the language you are reading the app in is the
  // whole point: a French user searching "Munich" wants "Munich", not "München".
  // CJK queries keep their script preference even in a non-CJK UI, since a
  // Chinese place name is most useful rendered in Chinese.
  const lang = hasCJK(q) ? (CJK_LANGS.has(LANG) ? LANG : "zh") : LANG;
  const url = `https://nominatim.openstreetmap.org/search?format=json&limit=6&accept-language=${lang}&q=${encodeURIComponent(q)}`;
  const r = await fetch(url, { headers: { "Accept-Language": lang } });
  const list = await r.json();
  return list.map((it) => ({
    name: (it.display_name || "").split(",")[0],
    sub: it.display_name || "",
    lat: +it.lat, lon: +it.lon,
  }));
}
// Photon accepts ONLY these. Sending anything else does not degrade to English
// -- it returns a validation error object with no `features` array, i.e. search
// silently finds nothing. Verified against the live API: lang=es/ru/ko/pt all
// answer {"lang":[{"message":"Language is not supported..."}]}.
const PHOTON_LANGS = new Set(["de", "en", "fr"]);

async function searchPhoton(q) {
  // "default" asks Photon for each place's own local name, which is a better
  // answer for an unsupported UI language than forcing everything to English.
  const lang = PHOTON_LANGS.has(LANG) ? LANG : "default";
  const url = `https://photon.komoot.io/api/?q=${encodeURIComponent(q)}&limit=6&lang=${lang}`;
  const r = await fetch(url);
  const d = await r.json();
  // An error object (bad lang, rate limit) has no `features`; treat it as
  // "no results" rather than letting .map throw and kill the whole search.
  return (Array.isArray(d && d.features) ? d.features : []).map((f) => {
    const p = f.properties || {}, c = f.geometry.coordinates;
    const parts = [p.street, p.city, p.state, p.country].filter(Boolean);
    return { name: p.name || parts[0] || "?", sub: parts.join(", "), lat: c[1], lon: c[0] };
  });
}

// Amap (高德): best China coverage + fuzzy autocomplete. Needs a Web-service key.
// Uses inputtips (fuzzy suggest) first, falls back to place/text (keyword search).
// Coordinates are GCJ-02 -> converted to WGS-84 before use.
async function searchAmap(q) {
  // 1) inputtips — fuzzy, autocomplete-style, great for partial/misspelled input
  const tipUrl = `https://restapi.amap.com/v3/assistant/inputtips?keywords=${encodeURIComponent(q)}&datatype=all&key=${AMAP_KEY}`;
  let out = [];
  try {
    const r = await fetch(tipUrl);
    const d = await r.json();
    if (d.status === "1" && Array.isArray(d.tips)) {
      out = d.tips
        .filter((t) => t.location && typeof t.location === "string")
        .map((t) => {
          const [lng, lat] = t.location.split(",").map(Number);
          const [wlng, wlat] = gcj02ToWgs84(lng, lat);
          const district = (t.district || "") + (t.address && typeof t.address === "string" ? " " + t.address : "");
          return { name: t.name, sub: district.trim(), lat: wlat, lon: wlng };
        });
    }
  } catch (e) {}
  if (out.length) return out;

  // 2) fallback — place/text keyword search (POI database)
  const poiUrl = `https://restapi.amap.com/v3/place/text?keywords=${encodeURIComponent(q)}&offset=6&key=${AMAP_KEY}`;
  try {
    const r = await fetch(poiUrl);
    const d = await r.json();
    if (d.status === "1" && Array.isArray(d.pois)) {
      return d.pois
        .filter((p) => p.location)
        .map((p) => {
          const [lng, lat] = p.location.split(",").map(Number);
          const [wlng, wlat] = gcj02ToWgs84(lng, lat);
          const sub = [p.pname, p.cityname, p.adname, p.address].filter((x) => x && typeof x === "string").join(" ");
          return { name: p.name, sub, lat: wlat, lon: wlng };
        });
    }
  } catch (e) {}
  return [];
}

async function doSearch(q, enter) {
  q = q.trim();
  if (!q) { renderSuggestions(); return; }
  const ll = parseLatLon(q);
  if (ll) {
    hideResults();
    focusMap(ll.lat, ll.lon);
    teleport(ll.lat, ll.lon);
    pushHistory({ name: `${fmt(ll.lat, 4)}, ${fmt(ll.lon, 4)}`, lat: ll.lat, lon: ll.lon });
    return;
  }
  let list = [];
  try {
    if (AMAP_KEY) {
      // Amap: best China coverage + fuzzy. Works great for CJK AND latin.
      list = await searchAmap(q);
      // If Amap somehow returns nothing (e.g. a foreign place), fall back to OSM.
      if (!list.length) {
        list = hasCJK(q) ? await searchNominatim(q) : await searchPhoton(q);
      }
    } else if (hasCJK(q)) {
      // No key: CJK -> Nominatim (Photon weak on CJK).
      list = await searchNominatim(q);
    } else {
      // No key: latin -> Photon (fuzzy) then Nominatim fallback.
      list = await searchPhoton(q);
      if (list.length < 2) {
        const nn = await searchNominatim(q);
        const seen = new Set(list.map((x) => x.name));
        nn.forEach((x) => { if (!seen.has(x.name)) list.push(x); });
      }
    }
  } catch (e) { list = []; }
  currentResults = list;
  activeIdx = -1;
  if (!list.length) { results.innerHTML = '<li class="empty">' + t("search.noResult") + '</li>'; results.classList.add("show"); return; }
  if (enter) { pick(list[0]); return; }
  renderResults(list);
}

function renderResults(list) {
  results.innerHTML = "";
  list.forEach((it, i) => {
    const li = document.createElement("li");
    li.dataset.i = i;
    li.innerHTML = `<div class="r-name"><span class="r-ico">📍</span>${escapeHtml(it.name)}</div>` +
      (it.sub ? `<div class="r-sub">${escapeHtml(it.sub)}</div>` : "");
    li.addEventListener("click", () => pick(it));
    results.appendChild(li);
  });
  results.classList.add("show");
}
function renderSuggestions() {
  // shown when box focused & empty: recent history
  if (!history.length) { hideResults(); return; }
  currentResults = history.slice(0, 6);
  activeIdx = -1;
  results.innerHTML = '<li class="empty" style="cursor:default">' + t("search.recent") + '</li>';
  currentResults.forEach((it, i) => {
    const li = document.createElement("li");
    li.dataset.i = i;
    li.innerHTML = `<div class="r-name"><span class="r-ico">🕘</span>${escapeHtml(it.name)}</div>`;
    li.addEventListener("click", () => pick(it));
    results.appendChild(li);
  });
  results.classList.add("show");
}
function hideResults() { results.classList.remove("show"); activeIdx = -1; }

function pick(it) {
  hideResults();
  searchBox.value = "";
  focusMap(it.lat, it.lon);
  teleport(it.lat, it.lon);
  pushHistory(it);
}

function pushHistory(it) {
  history = history.filter((h) => !(Math.abs(h.lat - it.lat) < 1e-6 && Math.abs(h.lon - it.lon) < 1e-6));
  history.unshift({ name: it.name, lat: it.lat, lon: it.lon });
  history = history.slice(0, 12);
  LS.set("history", history);
}

searchBox.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = searchBox.value.trim();
  if (!q) { renderSuggestions(); return; }
  if (q.length < 2) return;
  searchTimer = setTimeout(() => doSearch(q, false), 350);
});
searchBox.addEventListener("focus", () => { if (!searchBox.value.trim()) renderSuggestions(); });
searchBox.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    if (activeIdx >= 0 && currentResults[activeIdx]) pick(currentResults[activeIdx]);
    else doSearch(searchBox.value, true);
  } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const n = currentResults.length; if (!n) return;
    activeIdx = e.key === "ArrowDown" ? (activeIdx + 1) % n : (activeIdx - 1 + n) % n;
    [...results.querySelectorAll("li[data-i]")].forEach((li) =>
      li.classList.toggle("active", +li.dataset.i === activeIdx));
  } else if (e.key === "Escape") { hideResults(); }
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".search-wrap")) hideResults();
});

// ---------- favorites chips ----------
function renderFavs() {
  const box = $("favChips");
  box.innerHTML = "";
  favs.forEach((f, i) => {
    const c = document.createElement("span");
    c.className = "chip";
    c.innerHTML = `<span>⭐ ${escapeHtml(f.name)}</span> <span class="x" title="${t("fav.delete")}">×</span>`;
    c.querySelector("span:first-child").addEventListener("click", () => {
      focusMap(f.lat, f.lon); teleport(f.lat, f.lon);
    });
    c.querySelector(".x").addEventListener("click", (e) => {
      e.stopPropagation(); favs.splice(i, 1); LS.set("favs", favs); renderFavs();
    });
    box.appendChild(c);
  });
  const add = document.createElement("span");
  add.className = "chip add";
  add.innerHTML = t("fav.add");
  add.addEventListener("click", () => beginFavEdit(add));
  box.appendChild(add);
}

// Naming a favorite used to call prompt(). In WebView2 prompt() NEVER returns —
// no dialog, no exception, the JS thread just hangs and the app is dead until
// killed. So the add-chip morphs into a text input in place: Enter commits,
// Escape or blur cancels. Same chip geometry, so the panel never reflows.
function beginFavEdit(chip) {
  const input = document.createElement("input");
  input.type = "text";
  input.className = "chip-input";
  input.placeholder = t("fav.prompt");
  input.maxLength = 40;
  chip.classList.remove("add");
  chip.classList.add("editing");
  chip.innerHTML = "";
  chip.appendChild(input);

  let done = false;                      // blur fires after Enter too — commit once
  const finish = (commit) => {
    if (done) return;
    done = true;
    const name = input.value.trim();
    if (commit) {
      favs.unshift({
        name: name || `${fmt(pos.lat, 4)},${fmt(pos.lon, 4)}`,
        lat: pos.lat, lon: pos.lon,
      });
      favs = favs.slice(0, 12);
      LS.set("favs", favs);
    }
    renderFavs();                        // rebuilds the chip row, editor included
  };
  input.addEventListener("keydown", (e) => {
    // keep WASD/Shift out of the movement handler while typing
    e.stopPropagation();
    if (e.key === "Enter") { e.preventDefault(); finish(true); }
    else if (e.key === "Escape") { e.preventDefault(); finish(false); }
  });
  input.addEventListener("blur", () => finish(false));
  input.focus();
}
function escapeHtml(s) { return String(s).replace(/[&<>"']/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m])); }

// ============================================================================
// FREE MOVE — joystick + keyboard (core preserved)
// ============================================================================
let dir = { x: 0, y: 0 };
let shiftHeld = false;

function moveByMeters(dEast, dNorth) {
  if (!resumeSpoof()) return;
  userMovedMap = true;
  const dLat = dNorth / 111320;
  const dLon = dEast / (111320 * Math.cos(pos.lat * Math.PI / 180) || 1e-6);
  const lat = Math.max(-90, Math.min(90, pos.lat + dLat));
  const lon = clampLon(pos.lon + dLon);
  setMarker(lat, lon);
  sendLocation(lat, lon);
}

let lastTick = performance.now();
function tick() {
  const now = performance.now();
  const dt = (now - lastTick) / 1000;
  lastTick = now;
  if (mode === "free") {
    const mag = Math.hypot(dir.x, dir.y);
    if (mag > 0.02) {
      const spd = speedMps * (shiftHeld ? 3 : 1);
      const nx = dir.x / (mag || 1), ny = dir.y / (mag || 1);
      const dist = spd * dt * Math.min(1, mag);
      moveByMeters(nx * dist, ny * dist);
      keepInView();
    }
  }
  requestAnimationFrame(tick);
}
requestAnimationFrame(tick);
function keepInView() {
  if (!map || !marker) return;
  if (map.getBounds && !map.getBounds().pad(-0.2).contains(marker.getLatLng()))
    map.panTo(marker.getLatLng(), { animate: false });
}

const keys = new Set();
function recomputeKeyDir() {
  let x = 0, y = 0;
  if (keys.has("w") || keys.has("arrowup")) y += 1;
  if (keys.has("s") || keys.has("arrowdown")) y -= 1;
  if (keys.has("d") || keys.has("arrowright")) x += 1;
  if (keys.has("a") || keys.has("arrowleft")) x -= 1;
  dir.x = x; dir.y = y;
}
window.addEventListener("keydown", (e) => {
  if (["INPUT", "SELECT", "TEXTAREA"].includes(e.target.tagName) || e.target.isContentEditable) return;
  if (movementBlocked()) return;
  const k = e.key.toLowerCase();
  if (k === "shift") shiftHeld = true;
  if (["w", "a", "s", "d", "arrowup", "arrowdown", "arrowleft", "arrowright"].includes(k)) {
    keys.add(k); recomputeKeyDir(); e.preventDefault();
  }
});
window.addEventListener("keyup", (e) => {
  const k = e.key.toLowerCase();
  if (k === "shift") shiftHeld = false;
  keys.delete(k); recomputeKeyDir();
});
// Alt+Tab (or minimizing, or clicking the phone) while holding a key means the
// keyup lands in the other window and never reaches us — the avatar would walk
// forever, at 3x if Shift was down. In a windowed app switching away mid-move
// is normal, so drop all held keys whenever we lose focus or visibility.
function releaseAllKeys() {
  if (!keys.size && !shiftHeld) return;
  keys.clear();
  shiftHeld = false;
  recomputeKeyDir();
}
window.addEventListener("blur", releaseAllKeys);
document.addEventListener("visibilitychange", () => { if (document.hidden) releaseAllKeys(); });

// joystick
const joy = $("joy"), nub = $("joyNub");
const JOY_R = 75, NUB_R = 27, MAXOFF = JOY_R - NUB_R;
let joyActive = false;
function setNub(dx, dy) { nub.style.left = (JOY_R - NUB_R + dx) + "px"; nub.style.top = (JOY_R - NUB_R + dy) + "px"; }
function joyMove(cx, cy) {
  const r = joy.getBoundingClientRect();
  let dx = cx - (r.left + JOY_R), dy = cy - (r.top + JOY_R);
  const d = Math.hypot(dx, dy);
  if (d > MAXOFF) { dx = dx / d * MAXOFF; dy = dy / d * MAXOFF; }
  setNub(dx, dy); dir.x = dx / MAXOFF; dir.y = -dy / MAXOFF;
}
function joyEnd() { joyActive = false; dir.x = 0; dir.y = 0; setNub(0, 0); }
joy.addEventListener("pointerdown", (e) => {
  if (movementBlocked()) return;
  joyActive = true; joy.setPointerCapture(e.pointerId); joyMove(e.clientX, e.clientY);
});
joy.addEventListener("pointermove", (e) => { if (joyActive) joyMove(e.clientX, e.clientY); });
joy.addEventListener("pointerup", joyEnd);
joy.addEventListener("pointercancel", joyEnd);
setNub(0, 0);

// ============================================================================
// ROUTE MODE — waypoints + OSRM road-following + auto-walk
// ============================================================================
let waypoints = [];        // [{lat, lon, marker}]
let routeLine = null;      // L.polyline of the planned route
let routeCoords = [];      // [[lat,lon], ...] densified path to walk
let walking = false, walkAbort = false;

function wpIcon(i) {
  const label = String.fromCharCode(65 + i); // A, B, C...
  return L.divIcon({ className: "", html: `<div class="wp-pin"><span>${label}</span></div>`, iconSize: [26, 26], iconAnchor: [13, 24] });
}
function addWaypoint(lat, lon) {
  if (!map) return;   // waypoints are placed by clicking the map; no map, no waypoints
  const m = L.marker([lat, lon], { icon: wpIcon(waypoints.length), draggable: true }).addTo(map);
  const wp = { lat, lon, marker: m };
  m.on("dragend", () => { const ll = m.getLatLng(); wp.lat = ll.lat; wp.lon = ll.lng; renderWaypoints(); clearRouteLine(); });
  // click marker -> small popup with a delete button
  m.bindPopup(`<button class="wp-pop-del">🗑 <span>${t("wp.delete")}</span></button>`, {
    closeButton: false, className: "wp-popup", offset: [0, -18],
  });
  m.on("popupopen", (e) => {
    const btn = e.popup.getElement().querySelector(".wp-pop-del");
    if (btn) btn.addEventListener("click", () => { m.closePopup(); removeWaypoint(wp); });
  });
  waypoints.push(wp);
  renderWaypoints();
}
function removeWaypoint(wp) {
  const i = waypoints.indexOf(wp);
  if (i < 0) return;
  if (map) map.removeLayer(wp.marker);
  waypoints.splice(i, 1);
  renderWaypoints();
  clearRouteLine();
}

let dragFromIdx = -1;
function renderWaypoints() {
  const list = $("wpList");
  list.innerHTML = "";
  if (!waypoints.length) { list.innerHTML = '<li class="empty" id="wpEmpty">' + t("route.empty") + '</li>'; }
  waypoints.forEach((wp, i) => {
    if (wp.marker) wp.marker.setIcon(wpIcon(i));
    const li = document.createElement("li");
    li.draggable = true;
    li.dataset.i = i;
    li.title = t("wp.reorder");
    li.innerHTML =
      `<span class="wp-grip" aria-hidden="true">⋮⋮</span>` +
      `<span class="wp-badge">${String.fromCharCode(65 + i)}</span>` +
      `<span class="wp-co">${fmt(wp.lat, 5)}, ${fmt(wp.lon, 5)}</span>` +
      `<span class="wp-x" title="${t("fav.delete")}">×</span>`;
    li.querySelector(".wp-x").addEventListener("click", () => removeWaypoint(wp));
    // ---- drag-to-reorder (native HTML5 DnD) ----
    li.addEventListener("dragstart", (e) => {
      dragFromIdx = i; li.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      try { e.dataTransfer.setData("text/plain", String(i)); } catch (err) {}
    });
    li.addEventListener("dragend", () => {
      dragFromIdx = -1; li.classList.remove("dragging");
      list.querySelectorAll("li").forEach((x) => x.classList.remove("drop-target"));
    });
    li.addEventListener("dragover", (e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move";
      if (+li.dataset.i !== dragFromIdx) li.classList.add("drop-target"); });
    li.addEventListener("dragleave", () => li.classList.remove("drop-target"));
    li.addEventListener("drop", (e) => {
      e.preventDefault();
      const to = +li.dataset.i;
      if (dragFromIdx < 0 || dragFromIdx === to) return;
      const [moved] = waypoints.splice(dragFromIdx, 1);
      waypoints.splice(to, 0, moved);
      renderWaypoints();
      clearRouteLine();   // order changed -> old route invalid, re-plan
    });
    list.appendChild(li);
  });
  $("btnWalk").disabled = routeCoords.length < 2;
}
function clearRouteLine() {
  if (routeLine && map) { map.removeLayer(routeLine); routeLine = null; }
  routeCoords = [];
  $("routeInfo").classList.add("hidden");
  $("btnWalk").disabled = true;
}

$("btnClearRoute").addEventListener("click", () => {
  stopWalk();
  if (map) waypoints.forEach((wp) => map.removeLayer(wp.marker));
  waypoints = [];
  clearRouteLine();
  renderWaypoints();
});

// ============================================================================
// IN-PAGE NOTICES — never alert()
// ============================================================================
// alert() pops a bare untitled Win32 box AND hard-blocks every bit of JS until
// it is dismissed — including the position sender and the status poll. Route
// planning hits the public OSRM rate limit routinely, so this fired in normal
// use. Notices now render into the panel using the .dm-banner styling.
let noticeTimer = null;
function showNotice(msg, kind = "error", ms = 7000) {
  const el = $("routeNotice");
  if (!el) return;
  clearTimeout(noticeTimer);
  el.textContent = (kind === "error" ? "⚠️ " : "ℹ️ ") + msg;
  el.className = "dm-banner inline show " + kind;
  // auto-dismiss so a stale error doesn't sit there forever
  noticeTimer = setTimeout(() => {
    el.classList.add("fading");
    noticeTimer = setTimeout(() => { el.className = "dm-banner inline"; el.textContent = ""; }, 300);
  }, ms);
}
function clearNotice() {
  clearTimeout(noticeTimer);
  const el = $("routeNotice");
  if (el) { el.className = "dm-banner inline"; el.textContent = ""; }
}

$("btnPlan").addEventListener("click", planRoute);
async function planRoute() {
  if (waypoints.length < 2) { showNotice(t("route.needTwo")); return; }
  clearNotice();
  // The route-type selector was removed: OSRM's public demo server only has
  // the driving graph loaded, so "walking"/"cycling" returned the SAME road
  // geometry as driving -- a choice that looked meaningful and never was.
  // Walking speed stays user-controlled via the speed buttons.
  const profile = "driving";
  const coordStr = waypoints.map((w) => `${w.lon},${w.lat}`).join(";");
  const url = `https://router.project-osrm.org/route/v1/${profile}/${coordStr}?overview=full&geometries=geojson`;
  $("btnPlan").textContent = t("btn.planning"); $("btnPlan").disabled = true;
  try {
    const r = await fetch(url);
    const d = await r.json();
    if (d.code !== "Ok" || !d.routes || !d.routes.length) throw new Error(d.message || "no route");
    const route = d.routes[0];
    const latlngs = route.geometry.coordinates.map((c) => [c[1], c[0]]);
    clearRouteLine();
    if (map) {
      routeLine = L.polyline(latlngs, { color: "#388bfd", weight: 5, opacity: 0.85 }).addTo(map);
      map.fitBounds(routeLine.getBounds().pad(0.15));
    }
    routeCoords = densify(latlngs, 5); // ~5m spacing for smooth walking
    $("riDist").textContent = (route.distance / 1000).toFixed(2) + " km";
    $("riTime").textContent = fmtDur(route.duration);
    $("routeInfo").classList.remove("hidden");
    $("btnWalk").disabled = false;
  } catch (e) {
    showNotice(t("route.planFail") + e.message + " " + t("route.osrmNote"));
  } finally {
    // was hardcoded zh — reset from the dictionary so en/ja don't get 规划路线
    $("btnPlan").textContent = t("btn.plan"); $("btnPlan").disabled = false;
  }
}
function fmtDur(sec) {
  const m = Math.round(sec / 60);
  if (m < 60) return m + " " + t("time.min");
  return Math.floor(m / 60) + " " + t("time.hr") + " " + (m % 60) + " " + t("time.minShort");
}

// distance between two [lat,lon] in meters (haversine)
function distM(a, b) {
  const R = 6371000, toR = Math.PI / 180;
  const dLat = (b[0] - a[0]) * toR, dLon = (b[1] - a[1]) * toR;
  const la1 = a[0] * toR, la2 = b[0] * toR;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}
// insert intermediate points so consecutive points are <= step meters apart
function densify(latlngs, step) {
  const out = [];
  for (let i = 0; i < latlngs.length - 1; i++) {
    const a = latlngs[i], b = latlngs[i + 1];
    const d = distM(a, b);
    out.push(a);
    if (d > step) {
      const n = Math.floor(d / step);
      for (let k = 1; k <= n; k++) {
        const t = k / (n + 1);
        out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]);
      }
    }
  }
  out.push(latlngs[latlngs.length - 1]);
  return out;
}

$("btnWalk").addEventListener("click", startWalk);
$("btnStop").addEventListener("click", stopWalk);
async function startWalk() {
  if (routeCoords.length < 2 || walking) return;
  if (!resumeSpoof()) return;
  walking = true; walkAbort = false;
  $("btnWalk").disabled = true; $("btnStop").disabled = false;
  $("walkProgress").classList.remove("hidden");
  const bar = $("walkProgress").firstElementChild;
  const loop = $("loopChk").checked;

  // teleport to start
  setMarker(routeCoords[0][0], routeCoords[0][1], true);
  sendLocation(pos.lat, pos.lon);

  do {
    for (let i = 1; i < routeCoords.length; i++) {
      if (walkAbort) break;
      const from = [pos.lat, pos.lon];
      const to = routeCoords[i];
      const segLen = distM(from, to);
      const segTime = (segLen / speedMps) * 1000; // ms
      await animateSeg(from, to, segTime);
      bar.style.width = ((i / (routeCoords.length - 1)) * 100).toFixed(1) + "%";
    }
    if (walkAbort) break;
  } while (loop && !walkAbort);

  walking = false;
  $("btnWalk").disabled = false; $("btnStop").disabled = true;
  if (!walkAbort) setTimeout(() => $("walkProgress").classList.add("hidden"), 800);
}
function animateSeg(from, to, ms) {
  return new Promise((resolve) => {
    if (ms < 16) { setMarker(to[0], to[1]); sendLocation(to[0], to[1]); keepInView(); return resolve(); }
    const t0 = performance.now();
    function step() {
      if (walkAbort) return resolve();
      const t = Math.min(1, (performance.now() - t0) / ms);
      const lat = from[0] + (to[0] - from[0]) * t;
      const lon = from[1] + (to[1] - from[1]) * t;
      setMarker(lat, lon);
      sendLocation(lat, lon);
      keepInView();
      if (t < 1) requestAnimationFrame(step); else resolve();
    }
    requestAnimationFrame(step);
  });
}
function stopWalk() {
  walkAbort = true; walking = false;
  $("btnWalk").disabled = routeCoords.length < 2; $("btnStop").disabled = true;
}

// ============================================================================
// STATUS POLLING
// ============================================================================
async function poll() {
  const generation = statusGeneration;
  try {
    const r = await fetch("/api/status");
    const status = await r.json();
    if (generation === statusGeneration && !connectHttpPending) applyStatus(status);
  } catch (e) {
    if (generation === statusGeneration && !connectHttpPending) applyStatus({ state: "error", error: t("status.backendDown") });
  }
}
function applyStatus(s) {
  // The old session may still be connected while the worker clears it. Do not
  // mistake that old connection for completion of a same-device reconnect.
  curState = s.connection_pending ? "connecting" : s.state || "idle";
  if (s.connection_pending) s = { ...s, state: curState };
  currentPlatform = s.platform || currentPlatform;
  if (awaitingConnection && curState !== "connecting") {
    const configured = s.connection || {};
    const matches = currentPlatform !== "ios" ||
      (configured.transport === awaitingConnection.transport &&
       (!awaitingConnection.device_id || configured.device_id === awaitingConnection.device_id));
    if (curState === "error" || (["connected", "idle"].includes(curState) && matches)) {
      awaitingConnection = null;
      if (curState === "connected") {
        connectionGate = false;
        setConnectionNotice("ios.connectedHint", "enabled", true);
      } else if (curState === "idle") {
        setConnectionNotice("ios.connectionEnded", "waiting", true);
      } else setConnectionNotice(null);  // The concrete backend error is shown below.
    }
  }
  if (currentPlatform === "ios" && !iosInitialized) {
    iosInitialized = true;
    iosTransport = (s.connection || {}).transport === "wifi" || s.transport === "wifi" ? "wifi" : "usb";
    const device = s.device || {};
    iosSelection[iosTransport] = (s.connection || {}).device_id || device.id || "";
    if (device.id) iosDevices[iosTransport] = [{ id: device.id, name: device.name || device.model || "", os_version: device.os_version }];
    renderIosConnection();
  }
  if (currentPlatform === "ios" && !iosInitialScan && curState !== "connecting" && !connectionBusy && !awaitingConnection) {
    iosInitialScan = true;
    setTimeout(() => refreshIosDevices(), 0);
  }
  const pill = $("statePill");
  pill.className = "pill " + (s.state || "idle");
  pill.textContent = t("state." + s.state) || s.state;
  $("dot").style.background = { idle: "#8b949e", connecting: "#d29922", connected: "#2ea043", error: "#da3633" }[s.state] || "#8b949e";
  $("dot").style.boxShadow = s.state === "connected" ? "0 0 0 4px rgba(46,160,67,.25)" : "none";

  updateConnectionControls();

  const d = s.device || {};
  const dm = s.devmode || {};
  // The device line: only show concrete device info when actually connected.
  // Otherwise defer to the (real-time) developer-mode banner below — we never
  // dump raw internal errors like "set failed: Channel is closed" to the user.
  if (s.state === "connected" && d.model) {
    // Neutral device schema (platform/id/model/os_version/name) — branch label
    // on platform so iPhone shows "iOS", Android shows "Android".
    const osLabel = d.platform === "android" ? "Android" : "iOS";
    const heading = document.createElement("b");
    heading.textContent = d.name || d.model;
    const identity = document.createElement("span");
    identity.className = "coord";
    identity.textContent = (d.id || "").slice(0, 12) + "…";
    const transport = s.transport || (s.connection || {}).transport;
    const connectionLabel = d.platform === "ios" && ["usb", "wifi"].includes(transport) ? " · " + t("ios." + transport) : "";
    $("deviceInfo").replaceChildren(heading, document.createTextNode(` · ${osLabel} ${d.os_version || "?"}${connectionLabel}`), document.createElement("br"), identity);
    $("deviceInfo").style.display = "";
  } else if (s.state === "connecting") {
    $("deviceInfo").textContent = t("device.connecting");
    $("deviceInfo").style.display = "";
  } else if (s.error || (dm.state && dm.state !== "idle" && !(currentPlatform === "ios" && iosTransport === "wifi"))) {
    // a live devmode banner will speak for us — hide the redundant line
    $("deviceInfo").style.display = "none";
  } else {
    $("deviceInfo").textContent = t(currentPlatform === "ios" ? "ios.ready" : "device.notConnected2");
    $("deviceInfo").style.display = "";
  }

  // developer-mode / device status banner — real-time, translated by state.
  // This is the single source of truth for "what's going on with the device".
  // Covers both iOS Developer-Mode states and Android adb/USB-debugging states.
  const banner = $("dmBanner");
  const DM_KEY = { waiting: "dm.waiting", trust: "dm.trust", revealing: "dm.revealing",
                   reveal_done: "dm.revealDone", enabled: "dm.enabled", error: "dm.error",
                   // Android (adb) setup states:
                   adb_waiting: "adb.waiting", adb_unauthorized: "adb.unauthorized",
                   adb_ready: "adb.ready", adb_error: "adb.error" };
  if (s.error) {
    banner.className = "dm-banner show error";
    banner.textContent = "⚠️ " + s.error;
  } else if (dm.state && dm.state !== "idle" && DM_KEY[dm.state] && !(s.state === "connected") && !(currentPlatform === "ios" && iosTransport === "wifi")) {
    banner.className = "dm-banner show " + dm.state;
    const icon = { waiting: "🔌", trust: "📱", revealing: "⚙️", reveal_done: "👉", enabled: "✅", error: "⚠️",
                   adb_waiting: "🔌", adb_unauthorized: "📱", adb_ready: "✅", adb_error: "⚠️" }[dm.state] || "ℹ️";
    banner.textContent = `${icon} ${t(DM_KEY[dm.state])}`;
  } else {
    banner.className = "dm-banner";
  }
}

// ============================================================================
// INIT
// ============================================================================
// ORDER MATTERS. The map bootstrap used to run at the top of this file, so any
// failure there (Leaflet not loaded) threw before this block and left the user
// with a half-rendered panel: no theme button, an EMPTY language menu, no
// favorites and not one word of explanation. The panel is now wired FIRST and
// the map is booted last, inside a try, so a dead map costs you the map only.
setupThemeToggle();
setupLangSelector();
setupHelp();
applyI18n();
renderFavs();
setMarker(START[0], START[1]);

try {
  if (typeof L === "undefined") throw new Error("Leaflet (vendor/leaflet.js) did not load");
  bootMap();
  relabelLayers();     // apply translated layer names
} catch (e) {
  // No console in a WebView2 window, so say it on the page instead.
  showMapFailure();
  applyI18n();         // fill in the failure banner's translated strings
}

// ---- opening map view from a coarse IP lookup ------------------------------
// Why not navigator.geolocation? It throws a permission prompt at the user
// before they have done anything, and in a WebView the prompt is easy to miss
// and impossible to re-trigger. This is silent, needs no permission, and is
// only ever used to CHOOSE WHERE TO LOOK.
//
// It deliberately does NOT spoof: setMarker() moves the pin and the readout
// without POSTing /api/set, so the device stays on its real GPS until the user
// actually asks for something. Opening the app must never move your phone.
async function locateOnStartup() {
  let d;
  try {
    const r = await fetch("/api/iploc");
    if (!r.ok) return;
    d = await r.json();
  } catch (e) { return; }                 // offline / blocked -> keep START
  if (!d || typeof d.lat !== "number" || typeof d.lon !== "number") return;
  if (userMovedMap) return;               // the user got there first; leave them alone
  setMarker(d.lat, d.lon, false);         // NOT teleport(): view only, no spoof
  if (map) map.setView([d.lat, d.lon], 12);
}
locateOnStartup();

// status polling — starts last so a map failure can't stop the device UI
setInterval(poll, 1500);
poll();

