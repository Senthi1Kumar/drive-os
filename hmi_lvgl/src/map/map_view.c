#include "map/route_identity.h"
#include "map/route_progress.h"
#include "map/map_view.h"
#include "theme.h"
#include "map/map_gesture.h"
#include "map/map_puck.h"
#include "map/mapbox_logo.h"
#include "map/mapbox_url.h"
#include "map/maneuver_arrow.h"
#include "map/map_style.h"
#include "src/misc/cache/lv_cache.h"
#include <math.h>
#include <stdatomic.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#if DOS_HAVE_MAP
#include "map/map_worker.h"
#endif

#define DOS_BOOST_DEFAULT_MS 33   /* the map renders every 33 ms: a faster refresh only repaints the same frame */

/* Faster refresh and animation while the map moves, LV_DEF_REFR_PERIOD otherwise; calls nest. */
static uint32_t boost_period_ms(void)
{
    const char *e = getenv("DOS_BOOST_MS");   /* the display refresh and animation period while the map moves */
    long ms = e && *e ? strtol(e, NULL, 10) : 0;
    return ms >= 8 && ms <= 100 ? (uint32_t)ms : DOS_BOOST_DEFAULT_MS;
}

static void dos_frame_boost(bool on)
{
    static int held;
    bool was = held > 0;
    held += on ? 1 : -1;
    if(held < 0) held = 0;
    if((held > 0) == was) return;
    uint32_t ms = held > 0 ? boost_period_ms() : LV_DEF_REFR_PERIOD;
    lv_display_t *d = lv_display_get_default();
    if(d && lv_display_get_refr_timer(d)) lv_timer_set_period(lv_display_get_refr_timer(d), ms);
    if(lv_anim_get_timer()) lv_timer_set_period(lv_anim_get_timer(), ms);
}

#define MAN_ARROW_PX 56
#define MAN_SHOW_M 250.0   /* the next turn is marked on the map from this far */
#define MAN_LEAD_M 38.0
#define MAN_TAIL_M 30.0
#define FAST_MS 33     /* render pace while touched, gliding, zooming or following a moving car */
#define IDLE_MS 100    /* nothing moving: tiles and fades still land */
#define UI_MS 16       /* the UI picks up finished frames this often */

#define NAV_ZOOM 18.0        /* recenter while navigating: street level, a few blocks ahead */
#define NAV_PITCH 45.0
#define NAV_LOOKAHEAD 0.38   /* top padding as a share of the height: the car sits low, the road ahead shows */
#define FOLLOW_ZOOM 16.0     /* recenter without a route */
#define CAR_MOVING_MS 700    /* a car update keeps the map at the fast pace this long */
#define OVERVIEW_MS 3500     /* a new route is shown whole this long, then the camera returns to the car */
#define RESULTS_MS 6000      /* new search results while navigating: shown this long, then back to the car */
#define OVERVIEW_PAD 90
#define TRIM_MIN_M 4.0       /* the drawn route starts at the car: redrawn once it has moved this far ... */
#define TRIM_MIN_MS 300      /* ... and no more often than this */
#define TRIM_OFF_M 60.0      /* farther than this from the line the car is off route: the line stays as it is */

typedef struct {
    lv_obj_t *obj, *placeholder, *logo, *attrib, *puck;
    lv_timer_t *timer;
    map_view_cfg_t cfg;
    char style[256], token[256];
#if DOS_HAVE_MAP
    dos_mapw_t *mw;
#endif
    lv_image_dsc_t img;
    bool have_frame, visible, follow, touching, failed, have_car, night, fixed_style;
    bool nav, north_up, have_bbox, boost;
    uint32_t motion_until, overview_until, route_key, car_moving_until, tag_seq, results_key;
    int32_t travel;
    lv_obj_t *footer_layer;   /* where the wordmark and credits live when lifted above the dock fade, else the map */
    dos_drag_t drag;
    double car_lat, car_lon, car_heading, car_t_ms;
    double bbox[4];   /* south, west, north, east of the route */
    char *route_json, *stops_json, *results_json;
    double (*rt_ll)[2], (*rt_tmp)[2];   /* the whole route and a scratch for the part ahead of the car */
    char *rt_cong;
    int rt_n, rt_seg;                   /* point count; the segment the car was last on */
    double rt_up_lat, rt_up_lon;        /* where the drawn line last started */
    uint32_t rt_up_at;
    double rbox[4];   /* south, west, north, east of the search results */
    bool have_rbox;
    int man_key;       /* what the maneuver arrow was last drawn for; -1 none */
    bool man_on;
} mv_t;

static mv_t *get(lv_obj_t *o) { return lv_obj_get_user_data(o); }

/* DOS_CAM_LOG=1: one stdout line per camera decision, for watching a run. */
static void camlog(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void camlog(const char *fmt, ...)
{
    static int on = -1;
    if(on < 0) {
        const char *e = getenv("DOS_CAM_LOG");
        on = e && *e && strcmp(e, "0") != 0;
    }
    if(!on) return;
    printf("[cam %8u] ", (unsigned)lv_tick_get());
    va_list ap;
    va_start(ap, fmt);
    vprintf(fmt, ap);
    va_end(ap);
    putchar('\n');
    fflush(stdout);
}

#define STYLE_NIGHT "mapbox://styles/mapbox/navigation-night-v1"
#define STYLE_DAY "mapbox://styles/mapbox/streets-v12"
#define STYLE_NIGHT_FREE "https://tiles.openfreemap.org/styles/dark"
#define STYLE_DAY_FREE "https://tiles.openfreemap.org/styles/liberty"

/* Read on the map worker when a style loads. */
static _Atomic bool s_add_pois;   /* a style without its own poi-label layer: add place names */
static _Atomic bool s_mapbox;     /* a Mapbox style we may edit (3D buildings, label density) */
static _Atomic bool s_3d;         /* extruded buildings (DOS_MAP_3D) */
static _Atomic bool s_labels;     /* denser places and neighbourhood names at street zoom (DOS_MAP_LABELS) */
static _Atomic int s_3d_minzoom_x100;
static _Atomic bool s_night;
static _Atomic bool s_free;       /* an OpenFreeMap style: its own fonts */

/* DOS_MAP_3D=0/1 switches the extruded buildings; DOS_MAP_3D_MINZOOM is the zoom they start at. */
#define MAP_3D_DEFAULT 1
#define MAP_3D_MINZOOM_DEFAULT 15.0
static void map_3d_config(void)
{
    const char *e = getenv("DOS_MAP_3D"), *z = getenv("DOS_MAP_3D_MINZOOM");
    s_3d = e && *e ? strcmp(e, "0") != 0 : MAP_3D_DEFAULT;
    const char *l = getenv("DOS_MAP_LABELS");
    s_labels = !(l && !strcmp(l, "0"));
    double mz = z && *z ? atof(z) : MAP_3D_MINZOOM_DEFAULT;
    s_3d_minzoom_x100 = (int)lround((mz >= 12 && mz <= 20 ? mz : MAP_3D_MINZOOM_DEFAULT) * 100.0);
}

static const char *style_for(const mv_t *v, bool night)
{
    if(v->token[0]) return night ? STYLE_NIGHT : STYLE_DAY;
    return night ? STYLE_NIGHT_FREE : STYLE_DAY_FREE;
}

static bool mapbox_style(const mv_t *v) { return !strncmp(v->style, "mapbox://", 9); }

static void motion(mv_t *v, uint32_t ms) { v->motion_until = lv_tick_get() + ms; }

#if DOS_HAVE_MAP

static bool due(uint32_t now, uint32_t t) { return (int32_t)(now - t) >= 0; }

static const char *EMPTY = "{\"type\":\"FeatureCollection\",\"features\":[]}";

static uint8_t s_arrow_px[MAN_ARROW_PX * MAN_ARROW_PX * 4];

/* Mapbox basemap edits: extruded buildings below the labels, more places, neighbourhood names at street zoom. */
static void tune_basemap(dos_map_t *m, char *b, size_t cap)
{
    if(atomic_load(&s_labels) && dos_map_layer_exists(m, "poi-label") && dos_style_poi_filter_json(b, cap))
        dos_map_set_filter(m, "poi-label", b);
    if(atomic_load(&s_labels) && dos_map_layer_exists(m, "settlement-subdivision-label"))
        dos_map_set_layer_max_zoom(m, "settlement-subdivision-label", 19);   /* the style stops them at z15 */
    char first_label[160];
    if(atomic_load(&s_3d) && dos_map_layer_exists(m, "building") &&
       dos_map_first_layer_of_type(m, "symbol", first_label, sizeof first_label) &&
       dos_style_buildings_json(b, cap, s_night, atomic_load(&s_3d_minzoom_x100) / 100.0) &&
       dos_map_add_layer_json_before(m, b, first_label)) {
        dos_map_set_layer_property(m, "building", "visibility", "\"none\"");   /* the flat footprints are underneath */
        if(dos_map_layer_exists(m, "building-outline")) dos_map_set_layer_property(m, "building-outline", "visibility", "\"none\"");
    }
}

/* Runs on the map worker once the style is loaded: our sources and layers on top of the basemap. */
static void add_layers(dos_map_t *m)
{
    const char *font = s_free ? "\"Noto Sans Regular\"" : "\"DIN Pro Medium\",\"Arial Unicode MS Regular\"";
    const char *text = s_night ? "#e8eef7" : "#1d2430", *halo = s_night ? "#10141c" : "#ffffff";
    char b[2304];
    /* The navigation style streams live-traffic and incident tiles. Those two vector sources have failed on the Pi
       with "gzip decompression failed: unexpected end of file" while the same tiles fetch intact from the same host,
       and the route's congestion colours come from Directions, not from them: leave them out unless asked for. */
    const char *tp = getenv("DOS_MAP_TRAFFIC_TILES");
    if(!(tp && *tp && strcmp(tp, "0"))) {
        static const char *const live[] = { "mapbox-traffic", "mapbox-incidents" };
        dos_map_drop_sources(m, live, 2);
    }
    if(s_mapbox) tune_basemap(m, b, sizeof b);
    if(s_add_pois && !dos_map_layer_exists(m, "poi-label")) {   /* Streets v8 poi_label, text only */
        snprintf(b, sizeof b,
                 "{\"id\":\"dos-poi\",\"type\":\"symbol\",\"source\":\"composite\",\"source-layer\":\"poi_label\","
                 "\"minzoom\":15,\"filter\":[\"<=\",[\"get\",\"filterrank\"],3],"
                 "\"layout\":{\"text-field\":[\"coalesce\",[\"get\",\"name_en\"],[\"get\",\"name\"]],\"text-size\":12,"
                 "\"text-max-width\":8,\"text-font\":[%s]},"
                 "\"paint\":{\"text-color\":\"#c9d3e3\",\"text-halo-color\":\"#10141c\",\"text-halo-width\":1.2}}",
                 font);
        dos_map_add_layer_json(m, b);
    }
    dos_map_set_geojson(m, "dos-route", EMPTY);
    dos_map_set_geojson(m, "dos-stops", EMPTY);
    dos_map_set_geojson(m, "dos-results", EMPTY);

    /* the route in blue like Mapbox navigation; moderate, heavy and severe traffic turn it yellow, red, dark red */
    dos_map_add_layer_json(m, "{\"id\":\"dos-route-casing\",\"type\":\"line\",\"source\":\"dos-route\","
                             "\"layout\":{\"line-join\":\"round\",\"line-cap\":\"round\"},"
                             "\"paint\":{\"line-color\":\"#0a1a45\",\"line-width\":" DOS_STYLE_ROUTE_CASING_WIDTH "}}");
    dos_map_add_layer_json(m, "{\"id\":\"dos-route-line\",\"type\":\"line\",\"source\":\"dos-route\","
                             "\"layout\":{\"line-join\":\"round\",\"line-cap\":\"round\"},"
                             "\"paint\":{\"line-width\":" DOS_STYLE_ROUTE_LINE_WIDTH ",\"line-color\":[\"match\",[\"get\",\"c\"],"
                             "\"l\",\"#4a86ff\",\"m\",\"#f4b63f\",\"h\",\"#e8553d\",\"s\",\"#9b1c2e\",\"#4a86ff\"]}}");

    /* the next turn: the route lit up around the maneuver and an arrowhead lying on the road, pointing out of the turn */
    dos_map_set_geojson(m, "dos-man", EMPTY);
    static bool arrow_drawn;
    if(!arrow_drawn) {
        dos_maneuver_arrow_render(s_arrow_px, MAN_ARROW_PX);
        arrow_drawn = true;
    }
    dos_map_set_image(m, "dos-arrow", s_arrow_px, MAN_ARROW_PX, MAN_ARROW_PX);
    dos_map_add_layer_json(m, "{\"id\":\"dos-man-casing\",\"type\":\"line\",\"source\":\"dos-man\","
                             "\"filter\":[\"==\",[\"get\",\"k\"],\"seg\"],"
                             "\"layout\":{\"line-join\":\"round\",\"line-cap\":\"butt\"},"
                             "\"paint\":{\"line-color\":\"#0a1a45\",\"line-width\":[\"interpolate\",[\"linear\"],[\"zoom\"],"
                             "14,8,16,16,19,28]}}");
    dos_map_add_layer_json(m, "{\"id\":\"dos-man-line\",\"type\":\"line\",\"source\":\"dos-man\","
                             "\"filter\":[\"==\",[\"get\",\"k\"],\"seg\"],"
                             "\"layout\":{\"line-join\":\"round\",\"line-cap\":\"butt\"},"
                             "\"paint\":{\"line-color\":\"#ffffff\",\"line-width\":[\"interpolate\",[\"linear\"],[\"zoom\"],"
                             "14,4,16,9,19,16]}}");
    dos_map_add_layer_json(m, "{\"id\":\"dos-man-head\",\"type\":\"symbol\",\"source\":\"dos-man\","
                             "\"filter\":[\"==\",[\"get\",\"k\"],\"head\"],"
                             "\"layout\":{\"icon-image\":\"dos-arrow\",\"icon-rotate\":[\"get\",\"b\"],"
                             "\"icon-rotation-alignment\":\"map\",\"icon-pitch-alignment\":\"map\","
                             "\"icon-allow-overlap\":true,\"icon-ignore-placement\":true,"
                             "\"icon-size\":[\"interpolate\",[\"linear\"],[\"zoom\"],14,0.45,19,1.1]}}");

    /* stops in yellow, the destination in red, both named */
    dos_map_add_layer_json(m, "{\"id\":\"dos-stop-dot\",\"type\":\"circle\",\"source\":\"dos-stops\","
                             "\"paint\":{\"circle-radius\":[\"case\",[\"get\",\"dest\"],10,8],"
                             "\"circle-color\":[\"case\",[\"get\",\"dest\"],\"#e8553d\",\"#f5c84c\"],"
                             "\"circle-stroke-color\":\"#ffffff\",\"circle-stroke-width\":2.5}}");
    snprintf(b, sizeof b,
             "{\"id\":\"dos-stop-name\",\"type\":\"symbol\",\"source\":\"dos-stops\","
             "\"layout\":{\"text-field\":[\"get\",\"name\"],\"text-size\":13,\"text-max-width\":10,"
             "\"text-anchor\":\"top\",\"text-offset\":[0,1.1],\"text-font\":[%s]},"
             "\"paint\":{\"text-color\":\"%s\",\"text-halo-color\":\"%s\",\"text-halo-width\":1.5}}",
             font, text, halo);
    dos_map_add_layer_json(m, b);

    /* the last place search: violet numbered pins, named below, so "the second one" can be seen as well as said */
    dos_map_add_layer_json(m, "{\"id\":\"dos-result-dot\",\"type\":\"circle\",\"source\":\"dos-results\","
                             "\"paint\":{\"circle-radius\":12,\"circle-color\":\"#8b5cf6\","
                             "\"circle-stroke-color\":\"#ffffff\",\"circle-stroke-width\":2}}");
    snprintf(b, sizeof b,
             "{\"id\":\"dos-result-num\",\"type\":\"symbol\",\"source\":\"dos-results\","
             "\"layout\":{\"text-field\":[\"to-string\",[\"get\",\"n\"]],\"text-size\":13,\"text-font\":[%s],"
             "\"text-allow-overlap\":true,\"text-ignore-placement\":true},"
             "\"paint\":{\"text-color\":\"#ffffff\"}}",
             font);
    dos_map_add_layer_json(m, b);
    snprintf(b, sizeof b,
             "{\"id\":\"dos-result-name\",\"type\":\"symbol\",\"source\":\"dos-results\","
             "\"layout\":{\"text-field\":[\"get\",\"name\"],\"text-size\":13,\"text-max-width\":10,"
             "\"text-anchor\":\"top\",\"text-offset\":[0,1.3],\"text-font\":[%s]},"
             "\"paint\":{\"text-color\":\"%s\",\"text-halo-color\":\"%s\",\"text-halo-width\":1.5}}",
             font, text, halo);
    dos_map_add_layer_json(m, b);
    /* the car is not a map layer: LVGL draws the puck at the point each frame carries (see take_frame) */
}

static void place_puck(mv_t *v, const dos_map_tag_t *t)
{
    lv_obj_set_hidden(v->puck, !t->car_on || !v->have_frame);
    if(!t->car_on) return;
    lv_obj_set_pos(v->puck, lroundf(t->car_x) - MAP_PUCK_PX / 2, lroundf(t->car_y) - MAP_PUCK_PX / 2);
    lv_image_set_rotation(v->puck, (int32_t)lroundf(t->car_rot * 10.0f));
}

static void take_frame(mv_t *v)
{
    dos_mapw_frame_t f;
    const uint8_t *px = dos_mapw_take(v->mw, &f);
    if(px) {
        if(v->have_frame) lv_image_cache_drop(&v->img);
        v->img.header.magic = LV_IMAGE_HEADER_MAGIC;
        v->img.header.cf = LV_COLOR_FORMAT_XRGB8888;
        v->img.header.w = f.w;
        v->img.header.h = f.h;
        v->img.header.stride = f.stride;
        v->img.data = px;
        v->img.data_size = f.stride * f.h;
        v->have_frame = true;
        lv_obj_set_hidden(v->placeholder, true);
        lv_obj_invalidate(v->obj);
    }
    if(px || f.tag_seq != v->tag_seq) {   /* the puck moves with the frame it belongs to, or alone when idle */
        v->tag_seq = f.tag_seq;
        place_puck(v, &f.tag);
    }
}

static void push_car(mv_t *v)
{
    if(v->have_car) dos_mapw_set_car(v->mw, v->car_lat, v->car_lon, v->car_heading, v->car_t_ms);
}

static void recenter_camera(mv_t *v, uint32_t ms);
static void route_trim(mv_t *v, bool force);

static bool ensure_map(mv_t *v)
{
    if(v->mw) return true;
    int32_t w = lv_obj_get_width(v->obj), h = lv_obj_get_height(v->obj);
    if(w < 16 || h < 16) return false;
    dos_map_cfg_t c = { .width = (uint32_t)w, .height = (uint32_t)h, .lat = v->have_car ? v->car_lat : v->cfg.lat,
                       .lon = v->have_car ? v->car_lon : v->cfg.lon, .zoom = v->cfg.zoom, .style_url = v->style,
                       .access_token = v->token };
    v->mw = dos_mapw_create(&c, add_layers);
    if(!v->mw) return false;
    push_car(v);   /* whatever arrived before the worker existed */
    if(v->follow) recenter_camera(v, 0);
    if(v->route_json) dos_mapw_set_geojson(v->mw, "dos-route", v->route_json);
    if(v->stops_json) dos_mapw_set_geojson(v->mw, "dos-stops", v->stops_json);
    if(v->results_json) dos_mapw_set_geojson(v->mw, "dos-results", v->results_json);
    return true;
}

static double pad_top(const mv_t *v) { return NAV_LOOKAHEAD * (double)lv_obj_get_height(v->obj); }

/* Follow at street level: heading-up and tilted with a look-ahead while navigating, else flat and north up. */
static void recenter_camera(mv_t *v, uint32_t ms)
{
    if(!v->mw) return;
    camlog("RECENTER ms=%u nav=%d north_up=%d pitch=%s", (unsigned)ms, v->nav, v->north_up, v->nav && !v->north_up ? "45" : "0");
    const char *ez = getenv("DOS_NAV_ZOOM"), *ep = getenv("DOS_NAV_PITCH");   /* look experiments */
    double nz = ez && *ez ? atof(ez) : NAV_ZOOM, np = ep && *ep ? atof(ep) : NAV_PITCH;
    if(v->nav && !v->north_up) dos_mapw_follow(v->mw, true, true, nz, np, pad_top(v), ms);
    else dos_mapw_follow(v->mw, true, false, v->nav ? nz : FOLLOW_ZOOM, 0, 0, ms);
    motion(v, ms + 400);
}

static void stop_following(mv_t *v)
{
    camlog("STOP_FOLLOW");
    v->follow = false;
    if(v->mw) dos_mapw_follow(v->mw, false, false, NAN, NAN, NAN, 0);
}

static void overview_camera(mv_t *v)
{
    if(!v->mw || !v->have_bbox) return;
    double s = v->bbox[0], w = v->bbox[1], n = v->bbox[2], e = v->bbox[3];
    if(v->have_car) {
        s = fmin(s, v->car_lat), n = fmax(n, v->car_lat);
        w = fmin(w, v->car_lon), e = fmax(e, v->car_lon);
    }
    camlog("OVERVIEW_FIT bbox=%.5f,%.5f,%.5f,%.5f", s, w, n, e);
    dos_mapw_follow(v->mw, false, false, NAN, NAN, NAN, 0);   /* the fit owns the camera until the next recenter */
    dos_mapw_fit(v->mw, s, w, n, e, OVERVIEW_PAD, 900);
    motion(v, 1500);
}

static void tick(lv_timer_t *t)
{
    mv_t *v = lv_timer_get_user_data(t);
    if(v->failed || !ensure_map(v)) return;
    char why[160];
    if(dos_mapw_failed(v->mw, why, sizeof why)) {
        v->failed = true;
        lv_label_set_text_fmt(v->placeholder, "Map unavailable: %s", why);
        lv_timer_set_period(t, 1000);
        return;
    }
    uint32_t now = lv_tick_get();
    if(v->overview_until && due(now, v->overview_until) && !v->touching) {   /* the overview has been seen */
        v->overview_until = 0;
        camlog("OVERVIEW_EXPIRED follow=%d", v->follow);
        if(v->follow) recenter_camera(v, 1000);
    }
    bool fast = v->touching || (int32_t)(v->motion_until - now) > 0 || (int32_t)(v->car_moving_until - now) > 0;
    dos_mapw_set_period(v->mw, !v->visible ? 0 : fast ? FAST_MS : IDLE_MS);
    bool boost = v->visible && fast;   /* 60 Hz refresh and input while the map moves: frames land on time */
    if(boost != v->boost) dos_frame_boost(v->boost = boost);
    lv_timer_set_period(t, v->visible ? UI_MS : 250);
    if(v->visible) take_frame(v);
}

#endif

/* ---------- attribution: the Mapbox wordmark and the data credits Mapbox's terms and OSM's ODbL ask for ---------- */

static void improve_click(lv_event_t *e)
{
    LV_UNUSED(e);
    pid_t child = fork();
    if(child < 0) return;
    if(child == 0) {
        if(fork() == 0) {
            execlp("xdg-open", "xdg-open", dos_mapbox_feedback_url(), (char *)NULL);
            _exit(127);
        }
        _exit(0);
    }
    waitpid(child, NULL, 0);
}

static void footer_update(mv_t *v)
{
    bool mb = mapbox_style(v);
    bool away = v->footer_layer && !v->visible;   /* lifted out of the map: hide with it */
    lv_obj_set_hidden(v->logo, !mb || away);
    lv_obj_set_hidden(v->attrib, away);
    lv_obj_set_clickable(v->attrib, mb && !away);
    lv_label_set_text(v->attrib, mb ? "\xC2\xA9 Mapbox \xC2\xA9 OpenStreetMap  \xC2\xB7  Improve this map"
                                    : "\xC2\xA9 OpenFreeMap  \xC2\xA9 OpenMapTiles  \xC2\xA9 OpenStreetMap");
    lv_obj_set_style_bg_color(v->attrib, v->night ? lv_color_hex(0x0e1117) : lv_color_white(), 0);
    lv_obj_set_style_bg_opa(v->attrib, v->night ? 170 : 200, 0);
    lv_obj_set_style_text_color(v->attrib, v->night ? lv_color_hex(0xb9c3d2) : lv_color_hex(0x333a45), 0);
}

/* The wordmark at the bottom left and the credits at the bottom right, on the map's own edge: the dock is centred,
   so both corners stay clear of it. */
static void footer_place(mv_t *v)
{
    if(v->footer_layer) {
        lv_obj_update_layout(v->footer_layer);
        lv_obj_align_to(v->attrib, v->obj, LV_ALIGN_BOTTOM_RIGHT, -6, -6);
        lv_obj_align_to(v->logo, v->obj, LV_ALIGN_BOTTOM_LEFT, 10, -8);
        lv_obj_move_foreground(v->attrib);
        lv_obj_move_foreground(v->logo);
        return;
    }
    lv_obj_align(v->attrib, LV_ALIGN_BOTTOM_RIGHT, -6, -6);
    lv_obj_align(v->logo, LV_ALIGN_BOTTOM_LEFT, 10, -8);
}

void map_view_lift_footer(lv_obj_t *mv, lv_obj_t *layer)
{
    mv_t *v = get(mv);
    v->footer_layer = layer;
    lv_obj_t *both[2] = { v->logo, v->attrib };
    for(int i = 0; i < 2; i++) {
        lv_obj_set_parent(both[i], layer);
        lv_obj_set_ignore_layout(both[i], true);
    }
    footer_update(v);
    footer_place(v);
}

static void footer_create(mv_t *v)
{
    v->logo = lv_image_create(v->obj);
    lv_image_set_src(v->logo, &dos_mapbox_logo);
    lv_obj_set_clickable(v->logo, false);
    v->attrib = lv_label_create(v->obj);
    lv_obj_add_event_cb(v->attrib, improve_click, LV_EVENT_CLICKED, NULL);
    lv_obj_set_style_text_font(v->attrib, dos_font(DOS_F11), 0);
    lv_obj_set_style_pad_hor(v->attrib, 6, 0);
    lv_obj_set_style_pad_ver(v->attrib, 2, 0);
    lv_obj_set_style_radius(v->attrib, 4, 0);
    footer_update(v);
    footer_place(v);
}

/* ---------- the car's puck, drawn by LVGL at the point each frame carries ---------- */

static uint8_t s_puck_px[MAP_PUCK_PX * MAP_PUCK_PX * 4];
static lv_image_dsc_t s_puck_img;

static void puck_create(mv_t *v)
{
    if(!s_puck_img.data) {   /* the shared art, once: premultiplied RGBA to LVGL's straight B, G, R, A */
        map_puck_render(s_puck_px, MAP_PUCK_PX);
        for(int i = 0; i < MAP_PUCK_PX * MAP_PUCK_PX; i++) {
            uint8_t *p = s_puck_px + i * 4, r = p[0], g = p[1], b = p[2], a = p[3];
            p[0] = a ? (uint8_t)(b * 255 / a) : 0;
            p[1] = a ? (uint8_t)(g * 255 / a) : 0;
            p[2] = a ? (uint8_t)(r * 255 / a) : 0;
        }
        s_puck_img.header.magic = LV_IMAGE_HEADER_MAGIC;
        s_puck_img.header.cf = LV_COLOR_FORMAT_ARGB8888;
        s_puck_img.header.w = MAP_PUCK_PX;
        s_puck_img.header.h = MAP_PUCK_PX;
        s_puck_img.header.stride = MAP_PUCK_PX * 4;
        s_puck_img.data_size = sizeof s_puck_px;
        s_puck_img.data = s_puck_px;
    }
    v->puck = lv_image_create(v->obj);
    lv_image_set_src(v->puck, &s_puck_img);
    lv_image_set_pivot(v->puck, MAP_PUCK_PX / 2, MAP_PUCK_PX / 2);
    lv_image_set_antialias(v->puck, true);
    lv_obj_set_clickable(v->puck, false);
    lv_obj_set_hidden(v->puck, true);
}

/* ---------- LVGL object ---------- */

static void draw_cb(lv_event_t *e)
{
    mv_t *v = get(lv_event_get_target_obj(e));
    if(!v->have_frame) return;
    lv_area_t a;
    lv_obj_get_coords(v->obj, &a);
    a.x2 = a.x1 + (int32_t)v->img.header.w - 1;
    a.y2 = a.y1 + (int32_t)v->img.header.h - 1;
    lv_draw_image_dsc_t d;
    lv_draw_image_dsc_init(&d);
    d.src = &v->img;
    lv_draw_image(lv_event_get_layer(e), &d, &a);
}

static void size_cb(lv_event_t *e)
{
#if DOS_HAVE_MAP
    mv_t *v = get(lv_event_get_target_obj(e));
    if(v->mw) {
        dos_mapw_resize(v->mw, (uint32_t)lv_obj_get_width(v->obj), (uint32_t)lv_obj_get_height(v->obj));
        motion(v, 500);
    }
#else
    LV_UNUSED(e);
#endif
}

static void local_point(mv_t *v, int32_t *x, int32_t *y)
{
    lv_point_t p;
    lv_indev_get_point(lv_indev_active(), &p);
    lv_area_t a;
    lv_obj_get_coords(v->obj, &a);
    *x = p.x - a.x1;
    *y = p.y - a.y1;
}

static void input_cb(lv_event_t *e)
{
    mv_t *v = get(lv_event_get_target_obj(e));
#if DOS_HAVE_MAP
    if(!v->mw || v->failed || !lv_indev_active()) return;
    lv_event_code_t code = lv_event_get_code(e);
    uint32_t now = lv_tick_get();
    int32_t x, y;
    local_point(v, &x, &y);
    if(code == LV_EVENT_PRESSED) {
        v->touching = true;
        v->travel = 0;
        v->overview_until = 0;   /* the driver took the map: no automatic return */
        dos_drag_begin(&v->drag, x, y, now);
        dos_mapw_gesture(v->mw, true);
        motion(v, 400);
    }
    else if(code == LV_EVENT_PRESSING && v->touching) {
        int32_t dx, dy;
        dos_drag_move(&v->drag, x, y, now, &dx, &dy);
        if(dx || dy) {
            v->travel += abs(dx) + abs(dy);
            if(v->travel > 8 && v->follow) stop_following(v);   /* a drag takes the map; a tap does not */
            dos_mapw_move_by(v->mw, dx, dy);
        }
    }
    else if(v->touching) {   /* released or lost */
        v->touching = false;
        double fx, fy, ms;
        if(v->travel > 8 && dos_drag_end(&v->drag, now, &fx, &fy, &ms)) {
            dos_mapw_glide(v->mw, fx, fy, ms);
            motion(v, (uint32_t)ms + 200);
        }
        else motion(v, 400);
        dos_mapw_gesture(v->mw, false);
        if(v->travel <= 8 && code == LV_EVENT_RELEASED && dos_tap_is_double(&v->drag, x, y, now)) {
            if(v->follow) dos_mapw_follow_zoom(v->mw, 1.0);   /* zoom in on the car, still following */
            else dos_mapw_scale_by(v->mw, 2.0, x, y, 300);
            motion(v, 900);
        }
    }
#else
    LV_UNUSED(v);
#endif
}

static void delete_cb(lv_event_t *e)
{
    mv_t *v = get(lv_event_get_target_obj(e));
    if(v->timer) lv_timer_delete(v->timer);
    if(v->boost) dos_frame_boost(false);
#if DOS_HAVE_MAP
    dos_mapw_destroy(v->mw);
#endif
    free(v->route_json);
    free(v->stops_json);
    free(v->results_json);
    free(v->rt_ll);
    free(v->rt_tmp);
    free(v->rt_cong);
    free(v);
}

lv_obj_t *map_view_create(lv_obj_t *parent, const map_view_cfg_t *cfg)
{
    mv_t *v = calloc(1, sizeof *v);
    v->cfg = *cfg;
    const char *token = cfg->access_token ? cfg->access_token : getenv("DRIVEOS_MAPBOX__TOKEN");
    if(!token || !*token) token = getenv("MAPBOX_API_KEY");
    snprintf(v->token, sizeof v->token, "%s", token ? token : "");
    const char *style = cfg->style_url ? cfg->style_url : getenv("DOS_MAP_STYLE");
    v->night = true;
    v->fixed_style = style && *style;
    if(!v->fixed_style) style = style_for(v, true);
    s_add_pois = !v->fixed_style && v->token[0];
    s_mapbox = s_add_pois;
    map_3d_config();
    s_night = true;
    s_free = !v->token[0] && !v->fixed_style;
    snprintf(v->style, sizeof v->style, "%s", style);
    v->follow = true;
    v->visible = true;

    v->obj = lv_obj_create(parent);
    lv_obj_remove_style_all(v->obj);
    lv_obj_set_user_data(v->obj, v);
    lv_obj_set_style_bg_color(v->obj, lv_color_hex(0x15181d), 0);
    lv_obj_set_style_bg_opa(v->obj, LV_OPA_COVER, 0);
    lv_obj_set_scrollable(v->obj, false);
    lv_obj_set_clickable(v->obj, true);
    /* the map owns its drags: they never reach the page swiper */
    lv_obj_set_scroll_chain(v->obj, false);
    lv_obj_set_gesture_bubble(v->obj, false);
    lv_obj_add_event_cb(v->obj, draw_cb, LV_EVENT_DRAW_MAIN, NULL);
    lv_obj_add_event_cb(v->obj, size_cb, LV_EVENT_SIZE_CHANGED, NULL);
    lv_obj_add_event_cb(v->obj, input_cb, LV_EVENT_PRESSED, NULL);
    lv_obj_add_event_cb(v->obj, input_cb, LV_EVENT_PRESSING, NULL);
    lv_obj_add_event_cb(v->obj, input_cb, LV_EVENT_RELEASED, NULL);
    lv_obj_add_event_cb(v->obj, input_cb, LV_EVENT_PRESS_LOST, NULL);
    lv_obj_add_event_cb(v->obj, delete_cb, LV_EVENT_DELETE, NULL);

    v->placeholder = lv_label_create(v->obj);
    lv_obj_set_style_text_font(v->placeholder, dos_font(DOS_F15), 0);
    lv_obj_set_style_text_color(v->placeholder, DOS_DIM, 0);
    lv_obj_center(v->placeholder);
#if DOS_HAVE_MAP
    lv_label_set_text(v->placeholder, "Loading map\xE2\x80\xA6");
    v->timer = lv_timer_create(tick, UI_MS, v);
#else
    lv_label_set_text(v->placeholder, "Map not built (MapLibre Native package missing)");
#endif
    puck_create(v);
    footer_create(v);
    return v->obj;
}

void map_view_set_active(lv_obj_t *mv, bool visible)
{
    mv_t *v = get(mv);
    v->visible = visible;
    if(v->footer_layer) {
        footer_update(v);
        if(visible) footer_place(v);
    }
    if(visible) motion(v, 600);
    if(v->timer) lv_timer_ready(v->timer);
}

void map_view_set_car(lv_obj_t *mv, double lat, double lon, double heading, double t_ms)
{
    mv_t *v = get(mv);
    bool moved = !v->have_car || lat != v->car_lat || lon != v->car_lon || heading != v->car_heading;
    bool fresh = t_ms > 0 ? t_ms != v->car_t_ms : moved;   /* a snapshot repeating the last fix is not a sample */
    if(moved) v->car_moving_until = lv_tick_get() + CAR_MOVING_MS;
    v->car_lat = lat;
    v->car_lon = lon;
    v->car_heading = heading;
    v->car_t_ms = t_ms;
    v->have_car = true;
    if(moved) route_trim(v, false);
#if DOS_HAVE_MAP
    if(v->mw && fresh) push_car(v);   /* the worker moves the car, and the camera when following, every frame */
#endif
}

/* The drawn route is the part ahead of the car: the back-end re-plans from the car only every refresh, so between
   those the line is cut here as the car advances. `force` draws it whatever the car has done (a new route). */
static void route_trim(mv_t *v, bool force)
{
    if(!v->rt_n) return;
    dos_route_fix_t fix;
    bool at = v->have_car && dos_route_locate(v->rt_ll, v->rt_n, v->car_lat, v->car_lon, v->rt_seg, TRIM_OFF_M, &fix);
    if(!at) {
        if(!force) return;
        fix = (dos_route_fix_t){ .seg = 0, .lon = v->rt_ll[0][0], .lat = v->rt_ll[0][1] };
    }
    uint32_t now = lv_tick_get();
    if(!force) {
        double dx = (fix.lon - v->rt_up_lon) * cos(fix.lat * M_PI / 180.0), dy = fix.lat - v->rt_up_lat;
        if(hypot(dx, dy) * 111320.0 < TRIM_MIN_M || lv_tick_elaps(v->rt_up_at) < TRIM_MIN_MS) return;
    }
    int m = dos_route_remaining(v->rt_tmp, v->rt_ll, v->rt_n, &fix);
    if(m < 2) return;
    size_t off = (size_t)(v->rt_n - m), clen = v->rt_cong ? strlen(v->rt_cong) : 0;
    size_t cap = 512 + (size_t)m * 48;
    char *b = malloc(cap);
    if(!b) return;
    if(!route_geojson_traffic(b, cap, v->rt_tmp, m, off < clen ? v->rt_cong + off : NULL)) {
        free(b);
        return;
    }
    free(v->route_json);
    v->route_json = b;
    v->rt_seg = fix.seg;
    v->rt_up_lat = fix.lat;
    v->rt_up_lon = fix.lon;
    v->rt_up_at = now;
#if DOS_HAVE_MAP
    if(v->mw) dos_mapw_set_geojson(v->mw, "dos-route", v->route_json);
#endif
}

void map_view_set_maneuver(lv_obj_t *mv, double dist_m)
{
#if DOS_HAVE_MAP
    mv_t *v = get(mv);
    if(!v->mw) return;
    dos_route_fix_t fix;
    bool show = dist_m >= 0 && dist_m <= MAN_SHOW_M && v->rt_n >= 2 && v->have_car &&
                dos_route_locate(v->rt_ll, v->rt_n, v->car_lat, v->car_lon, v->rt_seg, TRIM_OFF_M, &fix);
    if(!show) {
        if(v->man_on) dos_mapw_set_geojson(v->mw, "dos-man", EMPTY);
        v->man_on = false;
        v->man_key = -1;
        return;
    }
    int key = (int)(dist_m / 3.0);   /* the arrow is redrawn when the turn has come 3 m closer */
    if(v->man_on && key == v->man_key) return;
    size_t cap = 4096;
    char *b = malloc(cap);
    if(!b) return;
    if(dos_maneuver_geojson(b, cap, (const double (*)[2])v->rt_ll, v->rt_n, &fix, dist_m, MAN_LEAD_M, MAN_TAIL_M)) {
        dos_mapw_set_geojson(v->mw, "dos-man", b);
        v->man_on = true;
        v->man_key = key;
    }
    free(b);
#else
    LV_UNUSED(mv);
    LV_UNUSED(dist_m);
#endif
}

/* Fallback identity when the back-end sends no route id: the end points. */
static uint32_t route_key(const double (*ll)[2], int n)
{
    if(n < 2) return 0;
    uint32_t h = 2166136261u ^ (uint32_t)n;
    const double pts[4] = { ll[0][0], ll[0][1], ll[n - 1][0], ll[n - 1][1] };
    for(int i = 0; i < 4; i++) h = (h ^ (uint32_t)(int64_t)llround(pts[i] * 1e5)) * 16777619u;
    return h ? h : 1;
}

void map_view_set_route(lv_obj_t *mv, const double (*ll)[2], int n, const char *congestion, uint32_t route_id)
{
    mv_t *v = get(mv);
    free(v->route_json);
    v->route_json = NULL;
    v->have_bbox = false;
    free(v->rt_ll);
    free(v->rt_tmp);
    free(v->rt_cong);
    v->rt_ll = v->rt_tmp = NULL;
    v->rt_cong = NULL;
    v->rt_n = v->rt_seg = 0;
    v->man_key = -1;
    if(n >= 2) {
        v->rt_ll = malloc(sizeof *v->rt_ll * (size_t)n);
        v->rt_tmp = malloc(sizeof *v->rt_tmp * (size_t)n);
        if(v->rt_ll && v->rt_tmp) {
            memcpy(v->rt_ll, ll, sizeof *v->rt_ll * (size_t)n);
            v->rt_n = n;
            if(congestion) v->rt_cong = strdup(congestion);
        }
        v->bbox[0] = v->bbox[2] = ll[0][1];
        v->bbox[1] = v->bbox[3] = ll[0][0];
        for(int i = 1; i < n; i++) {
            v->bbox[0] = fmin(v->bbox[0], ll[i][1]);
            v->bbox[2] = fmax(v->bbox[2], ll[i][1]);
            v->bbox[1] = fmin(v->bbox[1], ll[i][0]);
            v->bbox[3] = fmax(v->bbox[3], ll[i][0]);
        }
        v->have_bbox = true;
    }
    if(v->rt_n) route_trim(v, true);   /* the line starts at the car */
    bool fresh = dos_route_is_new(&v->route_key, n >= 2 ? route_id : 0, route_key(ll, n));
    camlog("SET_ROUTE id=%u n=%d fresh=%d nav=%d follow=%d first=%.5f,%.5f car=%.5f,%.5f", (unsigned)route_id, n, fresh,
           v->nav, v->follow, n ? ll[0][1] : 0.0, n ? ll[0][0] : 0.0, v->car_lat, v->car_lon);
#if DOS_HAVE_MAP
    if(!v->rt_n && v->mw) dos_mapw_set_geojson(v->mw, "dos-route", EMPTY);
    if(fresh && v->nav && v->follow && v->mw) {   /* a new route: show it whole, then return to the car */
        overview_camera(v);
        v->overview_until = lv_tick_get() + OVERVIEW_MS;
    }
#else
    LV_UNUSED(fresh);
#endif
    motion(v, 300);
}

void map_view_set_stops(lv_obj_t *mv, const route_stop_t *stops, int n, const route_stop_t *dest)
{
    mv_t *v = get(mv);
    free(v->stops_json);
    v->stops_json = NULL;
    if(dest || n > 0) {
        size_t cap = 256 + (size_t)(n + 1) * 320;
        char *b = malloc(cap);
        if(b && route_geojson_stops(b, cap, stops, n, dest)) v->stops_json = b;
        else free(b);
    }
#if DOS_HAVE_MAP
    if(v->mw) dos_mapw_set_geojson(v->mw, "dos-stops", v->stops_json ? v->stops_json : EMPTY);
#endif
    motion(v, 300);
}

void map_view_set_results(lv_obj_t *mv, const route_stop_t *places, int n)
{
    mv_t *v = get(mv);
    free(v->results_json);
    v->results_json = NULL;
    v->have_rbox = false;
    uint32_t key = 0;
    if(n > 0) {
        size_t cap = 256 + (size_t)n * 320;
        char *b = malloc(cap);
        if(b && route_geojson_stops(b, cap, places, n, NULL)) v->results_json = b;
        else free(b);
        key = 2166136261u ^ (uint32_t)n;   /* changes with the set, so only a new search is framed */
        for(int i = 0; i < n; i++) {
            key = (key ^ (uint32_t)(int64_t)llround(places[i].lat * 1e5)) * 16777619u;
            key = (key ^ (uint32_t)(int64_t)llround(places[i].lon * 1e5)) * 16777619u;
        }
        key = key ? key : 1;
        v->rbox[0] = v->rbox[2] = places[0].lat;
        v->rbox[1] = v->rbox[3] = places[0].lon;
        for(int i = 1; i < n; i++) {
            v->rbox[0] = fmin(v->rbox[0], places[i].lat);
            v->rbox[2] = fmax(v->rbox[2], places[i].lat);
            v->rbox[1] = fmin(v->rbox[1], places[i].lon);
            v->rbox[3] = fmax(v->rbox[3], places[i].lon);
        }
        v->have_rbox = true;
    }
    bool fresh = key && key != v->results_key;
    camlog("SET_RESULTS n=%d fresh=%d", n, fresh);
    v->results_key = key;
#if DOS_HAVE_MAP
    if(!v->mw) return;
    dos_mapw_set_geojson(v->mw, "dos-results", v->results_json ? v->results_json : EMPTY);
    if(fresh && !v->touching) {   /* frame the pins with the car */
        double s = v->rbox[0], w = v->rbox[1], nn = v->rbox[2], e = v->rbox[3];
        if(v->have_car) {
            s = fmin(s, v->car_lat), nn = fmax(nn, v->car_lat);
            w = fmin(w, v->car_lon), e = fmax(e, v->car_lon);
        }
        dos_mapw_follow(v->mw, false, false, NAN, NAN, NAN, 0);
        dos_mapw_fit(v->mw, s, w, nn, e, OVERVIEW_PAD, 900);
        if(v->nav && v->follow) v->overview_until = lv_tick_get() + RESULTS_MS;   /* driving: back to the car */
        else v->follow = false;   /* parked or browsing: the pins stay until the driver recenters */
        motion(v, 1500);
    }
#else
    LV_UNUSED(fresh);
#endif
    motion(v, 300);
}

void map_view_set_follow(lv_obj_t *mv, bool follow)
{
    mv_t *v = get(mv);
#if DOS_HAVE_MAP
    if(!follow) {
        stop_following(v);
        return;
    }
    camlog("SET_FOLLOW true");
    v->follow = true;
    if(v->mw) recenter_camera(v, 600);
#else
    v->follow = follow;
#endif
}

bool map_view_following(lv_obj_t *mv) { return get(mv)->follow; }

void map_view_set_nav(lv_obj_t *mv, bool navigating)
{
    mv_t *v = get(mv);
    if(v->nav == navigating) return;
    camlog("SET_NAV %d follow=%d overview_until=%u", navigating, v->follow, (unsigned)v->overview_until);
    v->nav = navigating;
    if(!navigating) {
        v->north_up = false;
        v->overview_until = 0;
    }
#if DOS_HAVE_MAP
    /* starting: heading-up behind the car (a new route's overview takes over when it arrives);
       arrived or cancelled: back to a flat, north-up map around the car */
    if(v->mw && v->follow && !v->overview_until) recenter_camera(v, navigating ? 800 : 700);
#endif
}

void map_view_recenter(lv_obj_t *mv)
{
    mv_t *v = get(mv);
    camlog("RECENTER_BUTTON");
    v->follow = true;
    v->north_up = false;
    v->overview_until = 0;
#if DOS_HAVE_MAP
    recenter_camera(v, 800);
#endif
}

void map_view_north_up(lv_obj_t *mv)
{
    mv_t *v = get(mv);
    v->north_up = true;
#if DOS_HAVE_MAP
    if(!v->mw) return;
    if(v->follow) dos_mapw_follow(v->mw, true, false, NAN, 0, 0, 500);   /* still on the car, now flat and north up */
    else dos_mapw_camera_to(v->mw, NAN, NAN, NAN, 0, 0, 0, 500);
    motion(v, 900);
#endif
}

void map_view_overview(lv_obj_t *mv)
{
    mv_t *v = get(mv);
    if(!v->have_bbox) return;
    camlog("OVERVIEW_BUTTON");
    v->follow = false;
    v->overview_until = 0;
#if DOS_HAVE_MAP
    overview_camera(v);   /* stops the follow camera first */
#endif
}

double map_view_bearing(lv_obj_t *mv)
{
#if DOS_HAVE_MAP
    mv_t *v = get(mv);
    return v->mw ? dos_mapw_bearing(v->mw) : 0.0;
#else
    LV_UNUSED(mv);
    return 0.0;
#endif
}

void map_view_zoom(lv_obj_t *mv, double factor)
{
#if DOS_HAVE_MAP
    mv_t *v = get(mv);
    if(!v->mw) return;
    if(v->follow) dos_mapw_follow_zoom(v->mw, log2(factor));   /* the follow camera keeps the new zoom */
    else dos_mapw_scale_by(v->mw, factor, lv_obj_get_width(v->obj) / 2.0, lv_obj_get_height(v->obj) / 2.0, 280);
    motion(v, 700);
#else
    LV_UNUSED(mv);
    LV_UNUSED(factor);
#endif
}

void map_view_set_night(lv_obj_t *mv, bool night)
{
    mv_t *v = get(mv);
    if(v->night == night) return;
    v->night = night;
    snprintf(v->style, sizeof v->style, "%s", style_for(v, night));
    s_add_pois = night && v->token[0];
    s_mapbox = !v->fixed_style && v->token[0];
    s_night = night;
    v->fixed_style = false;
    footer_update(v);
#if DOS_HAVE_MAP
    if(v->mw) {
        dos_mapw_set_style(v->mw, v->style);
        motion(v, 3000);   /* tiles, glyphs and sprites of the new style arrive over the next seconds */
    }
#endif
}

bool map_view_night(lv_obj_t *mv) { return get(mv)->night; }

void map_view_wheel(lv_obj_t *mv, int32_t x, int32_t y, float notches)
{
    mv_t *v = get(mv);
    lv_area_t a;
    lv_obj_get_coords(v->obj, &a);
    if(!v->visible || notches == 0 || x < a.x1 || x > a.x2 || y < a.y1 || y > a.y2) return;
#if DOS_HAVE_MAP
    if(!v->mw || v->failed) return;
    if(v->follow) dos_mapw_follow_zoom(v->mw, notches * 0.5);   /* a notch = half a zoom level */
    else dos_mapw_scale_by(v->mw, pow(2.0, notches * 0.5), x - a.x1, y - a.y1, 140);
    motion(v, 500);
#endif
}

bool map_view_failed(lv_obj_t *mv)
{
#if DOS_HAVE_MAP
    return get(mv)->failed;
#else
    LV_UNUSED(mv);
    return true;
#endif
}

bool map_view_live(lv_obj_t *mv)
{
#if DOS_HAVE_MAP
    mv_t *v = get(mv);
    return v->mw != NULL && !v->failed;
#else
    LV_UNUSED(mv);
    return false;
#endif
}
