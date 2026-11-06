/* DriveOS navigation map — Mapbox GL JS v3, Standard style (night / monochrome) restyled to the HMI.
   Driven entirely from the host via window.cw.* calls. */
(function () {
  "use strict";
  const C = { cyan: "#66edc5", ground: "#050608", done: "#2a313c" };
  // the HMI overlays: maneuver card (top-left), nav rail (bottom-left), control dock (bottom)
  const PAD_FOLLOW = { top: 260, bottom: 140, left: 380, right: 60 };
  const PAD_OVERVIEW = { top: 110, bottom: 200, left: 440, right: 90 };
  let map, ready = false, camera = "follow", vehicle = null, routeCoords = [], bounds = null;
  let markers = [], puck = null, pending = [];

  function call(fn) { if (ready) fn(); else pending.push(fn); }

  function init(cfg) {
    mapboxgl.accessToken = cfg.token;
    map = new mapboxgl.Map({
      container: "map",
      style: cfg.style || "mapbox://styles/mapbox/standard",
      config: { basemap: { lightPreset: cfg.lightPreset || "night", theme: cfg.theme || "monochrome",
                           showPointOfInterestLabels: false, show3dObjects: true } },
      center: cfg.center || [-121.99, 37.54], zoom: 15.2, pitch: 50, bearing: 0,
      attributionControl: true, logoPosition: "bottom-left", antialias: true,
      language: cfg.language || "en",
    });
    map.on("style.load", () => {
      map.addSource("route", { type: "geojson", lineMetrics: true, data: line([]) });
      map.addLayer({ id: "route-casing", type: "line", source: "route", slot: "middle",
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": C.ground, "line-width": ["interpolate", ["linear"], ["zoom"], 12, 8, 16, 17, 19, 26],
                 "line-emissive-strength": 1 } });
      map.addLayer({ id: "route-done", type: "line", source: "route", slot: "middle",
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": C.done, "line-width": ["interpolate", ["linear"], ["zoom"], 12, 4, 16, 9, 19, 14],
                 "line-emissive-strength": 1 } });
      map.addLayer({ id: "route", type: "line", source: "route", slot: "middle",
        layout: { "line-join": "round", "line-cap": "round" },
        paint: { "line-color": C.cyan, "line-width": ["interpolate", ["linear"], ["zoom"], 12, 4, 16, 9, 19, 14],
                 "line-trim-offset": [0, 0], "line-emissive-strength": 1 } });
      ready = true;
      pending.splice(0).forEach((f) => f());
    });
  }

  function line(coords) { return { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: coords } }; }

  function setRoute(r) {
    routeCoords = r.coords || []; bounds = r.bounds;
    call(() => {
      map.getSource("route").setData(line(routeCoords));
      if (camera === "overview") overview();
    });
  }

  function el(cls, html) { const d = document.createElement("div"); d.className = cls; d.innerHTML = html; return d; }
  function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

  function setMarkers(list) {
    call(() => {
      markers.forEach((m) => m.remove());
      markers = (list || []).map((m) => {
        const node = m.kind === "option"
          ? el("cw-stop", `<div class="dot">${esc(m.label)}</div><div class="lbl">${esc(m.name)}</div>`)
          : el("cw-dest", `<div class="lbl">${esc(m.name || m.label)}</div>`);
        return new mapboxgl.Marker({ element: node, anchor: "center" }).setLngLat([m.lon, m.lat]).addTo(map);
      });
    });
  }

  function setVehicle(v) {
    if (v.lon === undefined) return;
    vehicle = v;
    call(() => {
      if (!puck) {
        const node = el("cw-puck", '<svg viewBox="0 0 30 37"><path d="M15 0L30 37L15 28L0 37Z" fill="#f4f6f9" stroke="#07080b" stroke-width="2" stroke-linejoin="round"/></svg>');
        puck = new mapboxgl.Marker({ element: node, rotationAlignment: "map", pitchAlignment: "map" }).setLngLat([v.lon, v.lat]).addTo(map);
      }
      puck.setLngLat([v.lon, v.lat]);
      puck.setRotation(v.heading || 0);
      map.setPaintProperty("route", "line-trim-offset", [0, Math.max(0, Math.min(1, v.frac || 0))]);
      if (camera === "follow")
        map.easeTo({ center: [v.lon, v.lat], bearing: v.heading || 0, pitch: 55, zoom: 16.2, padding: PAD_FOLLOW,
                     duration: 300, easing: (t) => t, essential: true });
    });
  }

  function overview() {
    if (!bounds) return;
    map.fitBounds([[bounds[0], bounds[1]], [bounds[2], bounds[3]]], { padding: PAD_OVERVIEW, pitch: 0, bearing: 0, duration: 900 });
  }

  function setCamera(mode) {
    camera = mode || "follow";
    call(() => { if (camera === "overview") overview(); else if (vehicle) setVehicle(vehicle); });
  }

  window.cw = { init, setRoute, setMarkers, setVehicle, setCamera };
})();
