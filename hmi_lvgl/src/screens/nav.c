#include "screens.h"
#include "ws_client.h"
#include "map/lane_layout.h"
#if DOS_HAVE_MAP
#include "map/map_view.h"
#include "map/route_identity.h"
#endif
#include <stdio.h>
#include <string.h>
#include <strings.h>

#define RADAR_BG   lv_color_hex(0x070a0e)
#define ROUTE_CASE lv_color_hex(0x050608)
#define ROUTE_DONE lv_color_hex(0x2a313c)
#define PUCK       lv_color_hex(0xf4f6f9)

static lv_obj_t *s_radar, *s_route_case, *s_route, *s_dest, *s_dest_name, *s_puck_icon;
static lv_obj_t *s_man, *s_man_icon, *s_man_text;
/* lane guidance under the maneuver card: one cell per lane, up to three overlaid arrows in each */
#define LANE_CELL 52
#define LANE_GAP 2
#define LANE_ICON 36
#define LANE_ARROWS 3
#define LANE_PAD 8
static lv_obj_t *s_lanes, *s_lane_cell[UI_LANES_MAX], *s_lane_arrow[UI_LANES_MAX][LANE_ARROWS];
static lv_obj_t *s_rail_idle, *s_rail_active, *s_rail_dest, *s_rail_eta, *s_rail_state, *s_stop, *s_done;
static lv_point_precise_t s_pts[5];
#if DOS_HAVE_MAP
static lv_obj_t *s_map, *s_recenter;
static dos_route_seen_t s_route_seen;
static char s_cong[UI_CONGESTION_MAX], s_map_dest[64];
static bool s_visible;
#endif

static void cmd_stop(lv_event_t *e) { (void)e; link_cmd("stop_nav", NULL); }
static void cmd_back(lv_event_t *e) { (void)e; link_cmd("back", NULL); }

#if DOS_HAVE_MAP
static void recenter_click(lv_event_t *e)
{
    (void)e;
    map_view_recenter(s_map);
}

/* The driver's drag turns follow mode off inside the map; the recenter button mirrors that. */
static void map_poll(lv_timer_t *t)
{
    (void)t;
    bool failed = map_view_failed(s_map);
    dos_show(s_map, !failed);
    dos_show(s_radar, failed);
    dos_show(s_recenter, s_visible && !failed && !map_view_following(s_map));
}

void nav_set_visible(bool on)
{
    s_visible = on;
    map_view_set_active(s_map, on);
}

static void map_sync(const ui_state_t *s, bool route, bool navigating)
{
    bool have = s->vehicle.lat != 0 || s->vehicle.lon != 0;
    map_view_set_nav(s_map, navigating);
    if(have) map_view_set_car(s_map, s->vehicle.lat, s->vehicle.lon, s->vehicle.heading, s->vehicle.t_ms);
    bool geometry = route && s->nav.route_n >= 2;
    map_view_set_maneuver(s_map, navigating && s->nav.step_icon[0] ? s->nav.step_dist_m : -1);
    if(dos_route_needs_update(&s_route_seen, s->nav.route_ver, s->nav.route_id, s->nav.congestion, geometry)) {
        snprintf(s_cong, sizeof s_cong, "%s", s->nav.congestion);
        map_view_set_route(s_map, geometry ? (const double (*)[2])s->nav.route_ll : NULL, geometry ? s->nav.route_n : 0,
                           s_cong[0] ? s_cong : NULL, s->nav.route_id);
        snprintf(s_map_dest, sizeof s_map_dest, "%s", geometry ? (s->nav.dest[0] ? s->nav.dest : "Destination") : "");
        if(geometry) {
            const double *end = s->nav.route_ll[s->nav.route_n - 1];
            route_stop_t d = { s_map_dest, end[1], end[0] };
            map_view_set_stops(s_map, NULL, 0, &d);
        }
        else map_view_set_stops(s_map, NULL, 0, NULL);
    }
}
#else
void nav_set_visible(bool on) { (void)on; }
#endif

static void ring(lv_obj_t *parent, int32_t cx, int32_t cy, int32_t r)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_set_clickable(o, false);
    lv_obj_set_size(o, 2 * r, 2 * r);
    lv_obj_set_pos(o, cx - r, cy - r);
    lv_obj_set_style_radius(o, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_border_width(o, 1, 0);
    lv_obj_set_style_border_color(o, DOS_ACCENT, 0);
    lv_obj_set_style_border_opa(o, 26, 0);
}

static lv_obj_t *mk_line(lv_obj_t *parent, int32_t w, lv_color_t c)
{
    lv_obj_t *l = lv_line_create(parent);
    lv_obj_remove_style_all(l);
    lv_obj_set_style_line_width(l, w, 0);
    lv_obj_set_style_line_color(l, c, 0);
    lv_obj_set_style_line_rounded(l, true, 0);
    lv_obj_set_pos(l, 0, 0);
    return l;
}

void nav_build(lv_obj_t *slot)
{
    lv_obj_t *top = lv_obj_create(slot);
    lv_obj_remove_style_all(top);
    lv_obj_set_size(top, lv_pct(100), lv_pct(100));
    lv_obj_set_scrollable(top, false);
    lv_obj_update_layout(slot);
    int32_t W = lv_obj_get_width(slot), H = lv_obj_get_height(slot);
    /* the schematic radar is the fallback layer: it shows when the map is not built or cannot start */
    lv_obj_t *root = lv_obj_create(top);
    lv_obj_remove_style_all(root);
    lv_obj_set_size(root, lv_pct(100), lv_pct(100));
    lv_obj_set_scrollable(root, false);
    s_radar = root;
    lv_obj_set_style_bg_color(root, RADAR_BG, 0);
    lv_obj_set_style_bg_opa(root, LV_OPA_COVER, 0);

    int32_t cx = W * 62 / 100, cy = H * 62 / 100;
    /* dotted grid: sparse dots, enough to read as a map */
    for(int32_t gx = cx % 24; gx < W; gx += 24)
        for(int32_t gy = cy % 24; gy < H; gy += 24) {
            lv_obj_t *d = lv_obj_create(root);
            lv_obj_remove_style_all(d);
            lv_obj_set_clickable(d, false);
            lv_obj_set_size(d, 2, 2);
            lv_obj_set_pos(d, gx, gy);
            lv_obj_set_style_bg_color(d, DOS_WHITE, 0);
            lv_obj_set_style_bg_opa(d, 13, 0);
        }
    static const int32_t radii[] = { 130, 260, 520 };
    static const char *const names[] = { "¼ mi", "½ mi", "1 mi" };
    for(int i = 0; i < 3; i++) {
        ring(root, cx, cy, radii[i]);
        lv_obj_t *l = dos_text(root, DOS_F11, DOS_DIM, names[i]);
        lv_obj_set_style_text_opa(l, 140, 0);
        lv_obj_set_pos(l, cx + radii[i] * 707 / 1000 + 4, cy - radii[i] * 707 / 1000 - 16);
    }
    /* schematic route: the contract has no geometry, so this only says "there is a route" */
    s_pts[0] = (lv_point_precise_t){ cx, cy };
    s_pts[1] = (lv_point_precise_t){ cx, cy - 110 };
    s_pts[2] = (lv_point_precise_t){ cx - 120, cy - 190 };
    s_pts[3] = (lv_point_precise_t){ cx - 260, cy - 190 };
    s_pts[4] = (lv_point_precise_t){ cx - 330, cy - 250 };
    s_route_case = mk_line(root, 13, ROUTE_CASE);
    lv_line_set_points(s_route_case, s_pts, 5);
    s_route = mk_line(root, 7, DOS_LIVE);
    lv_line_set_points(s_route, s_pts, 5);
    s_dest = lv_obj_create(root);
    lv_obj_remove_style_all(s_dest);
    lv_obj_set_size(s_dest, 24, 24);
    lv_obj_set_pos(s_dest, (int32_t)s_pts[4].x - 12, (int32_t)s_pts[4].y - 12);
    lv_obj_set_style_radius(s_dest, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(s_dest, DOS_LIVE, 0);
    lv_obj_set_style_bg_opa(s_dest, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(s_dest, 3, 0);
    lv_obj_set_style_border_color(s_dest, DOS_BG0, 0);
    lv_obj_t *nm = dos_glass(root, 13);
    lv_obj_set_size(nm, LV_SIZE_CONTENT, 26);
    lv_obj_set_style_pad_hor(nm, 10, 0);
    lv_obj_set_flex_flow(nm, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(nm, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    s_dest_name = dos_text(nm, DOS_F13, DOS_INK, "");
    lv_obj_set_pos(nm, (int32_t)s_pts[4].x - 50, (int32_t)s_pts[4].y - 48);
    lv_obj_set_user_data(s_dest, nm);

    lv_obj_t *halo = lv_obj_create(root);
    lv_obj_remove_style_all(halo);
    lv_obj_set_size(halo, 56, 56);
    lv_obj_set_pos(halo, cx - 28, cy - 28);
    lv_obj_set_style_radius(halo, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(halo, DOS_LIVE, 0);
    lv_obj_set_style_bg_opa(halo, DOS_OPA_SOFT, 0);
    s_puck_icon = dos_icon(root, DOS_IC_NAVIGATION, 24, PUCK);
    lv_obj_set_pos(s_puck_icon, cx - 12, cy - 12);
    lv_obj_set_style_transform_pivot_x(s_puck_icon, 12, 0);
    lv_obj_set_style_transform_pivot_y(s_puck_icon, 12, 0);

    lv_obj_t *tag = dos_chip(root, DOS_CHIP_QUIET, -1, "Schematic map · no street data", false);
    lv_obj_set_style_bg_color(tag, DOS_GLASS, 0);
    lv_obj_set_style_bg_opa(tag, DOS_OPA_GLASS, 0);
    lv_obj_align(tag, LV_ALIGN_TOP_RIGHT, -14, 14);

#if DOS_HAVE_MAP
    {
        const ui_state_t *st = ui_state();
        bool have = st->vehicle.lat != 0 || st->vehicle.lon != 0;
        s_map = map_view_create(top, &(map_view_cfg_t){ .lat = have ? st->vehicle.lat : 37.2358,
                                                          .lon = have ? st->vehicle.lon : -121.9746, .zoom = 16 });
        lv_obj_set_size(s_map, lv_pct(100), lv_pct(100));
        /* the page sits in slot > stage; the dock's fade lies over the stage's bottom, so the wordmark and credits are
           drawn on the stage (still at the map's bottom corners) to stay clear of it */
        lv_obj_t *stage = lv_obj_get_parent(lv_obj_get_parent(lv_obj_get_parent(top)));
        if(stage) map_view_lift_footer(s_map, stage);
        s_recenter = dos_round_btn(top, 56, DOS_IC_CROSSHAIR, 24);
        lv_obj_align(s_recenter, LV_ALIGN_BOTTOM_RIGHT, -DOS_PAD, -(DOS_DOCK_H + 64));
        lv_obj_add_event_cb(s_recenter, recenter_click, LV_EVENT_CLICKED, NULL);
        lv_obj_set_hidden(s_recenter, true);
        lv_timer_create(map_poll, 200, NULL);
    }
#endif
    root = top;

    /* maneuver card */
    s_man = dos_glass(root, 20);
    lv_obj_set_size(s_man, 460, 86);
    lv_obj_set_style_max_width(s_man, lv_pct(94), 0);
    lv_obj_set_style_border_color(s_man, DOS_LIVE, 0);
    lv_obj_set_style_border_opa(s_man, DOS_OPA_LINE_C, 0);
    lv_obj_set_pos(s_man, DOS_PAD, 14);
    lv_obj_t *tile = dos_box(s_man, LV_FLEX_FLOW_ROW);
    lv_obj_set_size(tile, 58, 58);
    lv_obj_align(tile, LV_ALIGN_LEFT_MID, 14, 0);
    lv_obj_set_style_radius(tile, 15, 0);
    lv_obj_set_style_bg_color(tile, DOS_LIVE, 0);
    lv_obj_set_style_bg_opa(tile, DOS_OPA_SOFT, 0);
    lv_obj_set_flex_align(tile, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    s_man_icon = dos_icon(tile, DOS_IC_ARROW_UP, 30, DOS_LIVE);
    s_man_text = dos_text(s_man, DOS_F17, DOS_INK, "");
    lv_obj_set_width(s_man_text, 340);
    lv_label_set_long_mode(s_man_text, LV_LABEL_LONG_MODE_WRAP);
    lv_obj_align(s_man_text, LV_ALIGN_LEFT_MID, 86, 0);

    s_lanes = dos_glass(root, 16);
    lv_obj_set_size(s_lanes, LANE_CELL + 2 * LANE_PAD, LANE_CELL + 2 * LANE_PAD);
    lv_obj_set_scrollable(s_lanes, false);
    lv_obj_set_pos(s_lanes, DOS_PAD, 14 + 86 + 8);
    for(int i = 0; i < UI_LANES_MAX; i++) {
        lv_obj_t *c = lv_obj_create(s_lanes);
        lv_obj_remove_style_all(c);
        lv_obj_set_clickable(c, false);
        lv_obj_set_size(c, LANE_CELL, LANE_CELL);
        lv_obj_set_style_radius(c, 10, 0);
        lv_obj_set_style_bg_color(c, DOS_LIVE, 0);
        s_lane_cell[i] = c;
        for(int k = 0; k < LANE_ARROWS; k++) {
            lv_obj_t *a = dos_icon(c, DOS_IC_ARROW_UP, LANE_ICON, DOS_INK);
            lv_obj_align(a, LV_ALIGN_CENTER, 0, 0);
            lv_obj_set_style_transform_pivot_x(a, LANE_ICON / 2, 0);
            lv_obj_set_style_transform_pivot_y(a, LANE_ICON / 2, 0);
            s_lane_arrow[i][k] = a;
        }
    }
    lv_obj_set_hidden(s_lanes, true);

    /* left rail above the dock */
    lv_obj_t *rail = dos_box(root, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_size(rail, 380, LV_SIZE_CONTENT);
    lv_obj_set_style_pad_row(rail, 8, 0);
    lv_obj_align(rail, LV_ALIGN_BOTTOM_LEFT, DOS_PAD, -190);

    s_rail_idle = dos_glass(rail, DOS_RADIUS);
    lv_obj_set_size(s_rail_idle, lv_pct(100), 120);
    lv_obj_set_style_pad_all(s_rail_idle, 14, 0);
    lv_obj_set_flex_flow(s_rail_idle, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(s_rail_idle, 8, 0);
    dos_section_label(s_rail_idle, "Navigation");
    lv_obj_t *hint = dos_text(s_rail_idle, DOS_F15, DOS_INK, "Say “take me home”, or find a place and say “take me there”.");
    lv_obj_set_width(hint, lv_pct(100));

    s_rail_active = dos_glass(rail, DOS_RADIUS);
    lv_obj_set_size(s_rail_active, lv_pct(100), 132);
    lv_obj_set_style_pad_all(s_rail_active, 0, 0);
    s_rail_state = dos_section_label(s_rail_active, "Navigating");
    lv_obj_set_pos(s_rail_state, 14, 14);
    s_rail_dest = dos_text(s_rail_active, DOS_F17, DOS_INK, "");
    lv_obj_set_width(s_rail_dest, 350);
    lv_label_set_long_mode(s_rail_dest, LV_LABEL_LONG_MODE_DOTS);
    lv_obj_set_pos(s_rail_dest, 14, 34);
    s_rail_eta = dos_text(s_rail_active, DOS_F30, DOS_LIVE, "");
    lv_obj_set_style_text_letter_space(s_rail_eta, -1, 0);
    lv_obj_set_pos(s_rail_eta, 14, 66);

    s_stop = dos_btn(rail, DOS_BTN_DANGER, false, DOS_IC_SQUARE, "Stop guidance");
    lv_obj_set_width(s_stop, lv_pct(100));
    lv_obj_add_event_cb(s_stop, cmd_stop, LV_EVENT_CLICKED, NULL);
    s_done = dos_btn(rail, DOS_BTN_DEFAULT, false, DOS_IC_CHECK, "Done");
    lv_obj_set_width(s_done, lv_pct(100));
    lv_obj_add_event_cb(s_done, cmd_back, LV_EVENT_CLICKED, NULL);
}

static dos_icon_t man_icon(const char *t)
{
    if(strcasestr(t, "arriv") || strcasestr(t, "destination")) return DOS_IC_FLAG;
    if(strcasestr(t, "u-turn") || strcasestr(t, "uturn")) return DOS_IC_UNDO_2;
    if(strcasestr(t, "slight right")) return DOS_IC_ARROW_UP_RIGHT;
    if(strcasestr(t, "slight left")) return DOS_IC_ARROW_UP_LEFT;
    if(strcasestr(t, "right")) return DOS_IC_CORNER_UP_RIGHT;
    if(strcasestr(t, "left")) return DOS_IC_CORNER_UP_LEFT;
    return DOS_IC_ARROW_UP;
}

/* Icon from the back-end's nav.step.icon key (nav/guidance.py maneuver_icon). */
static dos_icon_t step_icon(const char *k)
{
    if(!strcmp(k, "arrive")) return DOS_IC_FLAG;
    if(!strcmp(k, "uturn")) return DOS_IC_UNDO_2;
    if(!strcmp(k, "slight_right") || !strcmp(k, "fork_right") || !strcmp(k, "ramp_right")) return DOS_IC_ARROW_UP_RIGHT;
    if(!strcmp(k, "slight_left") || !strcmp(k, "fork_left") || !strcmp(k, "ramp_left")) return DOS_IC_ARROW_UP_LEFT;
    if(!strcmp(k, "turn_right") || !strcmp(k, "sharp_right")) return DOS_IC_CORNER_UP_RIGHT;
    if(!strcmp(k, "turn_left") || !strcmp(k, "sharp_left")) return DOS_IC_CORNER_UP_LEFT;
    return DOS_IC_ARROW_UP;
}

/* The lanes of the upcoming maneuver: valid lanes lit and tinted, the arrow the route takes brightest. */
static void lanes_update(const ui_state_t *s, bool show)
{
    int n = show ? s->nav.n_lanes : 0;
    dos_show(s_lanes, n > 0);
    if(n <= 0) return;
    dos_lane_row_t row;
    dos_lane_row(&row, n, UI_LANES_MAX, 460 - 2 * LANE_PAD, LANE_CELL, 30, LANE_GAP);
    lv_obj_set_size(s_lanes, row.total_w + 2 * LANE_PAD, LANE_CELL + 2 * LANE_PAD);
    for(int i = 0; i < UI_LANES_MAX; i++) {
        bool on = i < row.n;
        dos_show(s_lane_cell[i], on);
        if(!on) continue;
        bool valid = s->nav.lanes[i].valid;
        lv_obj_set_size(s_lane_cell[i], row.cell_w, LANE_CELL);
        lv_obj_set_pos(s_lane_cell[i], LANE_PAD + dos_lane_cell_x(&row, i), LANE_PAD);
        lv_obj_set_style_bg_opa(s_lane_cell[i], valid ? DOS_OPA_SOFT : LV_OPA_TRANSP, 0);
        int order[DOS_LANE_DIR_COUNT];
        int m = dos_lane_draw_order(s->nav.lanes[i].ind, s->nav.lanes[i].active, order);
        for(int k = 0; k < LANE_ARROWS; k++) {
            lv_obj_t *a = s_lane_arrow[i][k];
            dos_show(a, k < m);
            if(k >= m) continue;
            bool taken = valid && (s->nav.lanes[i].active < 0 || order[k] == s->nav.lanes[i].active);
            dos_icon_set(a, DOS_IC_ARROW_UP, LANE_ICON, valid ? (taken ? DOS_INK : DOS_DIM) : DOS_DIM);
            lv_obj_set_style_opa(a, valid ? LV_OPA_COVER : LV_OPA_50, 0);
            lv_obj_set_style_transform_rotation(a, dos_lane_dir_deg(order[k]) * 10, 0);
        }
    }
}

void nav_update(const ui_state_t *s)
{
    bool idle = !strcmp(s->nav.mode, "idle") || !s->nav.mode[0];
    bool navigating = !strcmp(s->nav.mode, "navigating");
    bool arrived = !strcmp(s->nav.mode, "arrived");
    bool route = !idle;
#if DOS_HAVE_MAP
    map_sync(s, route, navigating);
#endif
    dos_show(s_route_case, route);
    dos_show(s_route, route);
    dos_show(s_dest, route);
    dos_show(lv_obj_get_user_data(s_dest), route);
    dos_label_set(s_dest_name, s->nav.dest);
    lv_obj_set_style_transform_rotation(s_puck_icon, ((int32_t)s->vehicle.heading - 45) * 10, 0);

    bool has_step = s->nav.step_icon[0] && navigating;
    bool man = (s->nav.maneuver[0] || has_step) && !idle;
    dos_show(s_man, man);
    if(man && has_step) {
        char t[128];
        double d = s->nav.step_dist_m;
        if(d < 0) snprintf(t, sizeof t, "%s", s->nav.step_text);
        else if(d < 1000) snprintf(t, sizeof t, "%d m  %s", (int)(d / 10 + 0.5) * 10, s->nav.step_text);
        else snprintf(t, sizeof t, "%.1f km  %s", d / 1000.0, s->nav.step_text);
        dos_icon_set(s_man_icon, step_icon(s->nav.step_icon), 30, DOS_LIVE);
        dos_label_set(s_man_text, t);
    } else if(man) {
        dos_icon_set(s_man_icon, man_icon(s->nav.maneuver), 30, DOS_LIVE);
        dos_label_set(s_man_text, s->nav.maneuver);
    }
    lanes_update(s, has_step);
    dos_show(s_rail_idle, idle);
    dos_show(s_rail_active, !idle);
    dos_show(s_stop, navigating);
    dos_show(s_done, arrived);
    if(!idle) {
        dos_text_set_upper(s_rail_state, arrived ? "Arrived" : navigating ? "Navigating" : "Preview");
        dos_label_set(s_rail_dest, s->nav.dest);
        char b[32] = "";
        if(!arrived && s->nav.eta_min >= 0) snprintf(b, sizeof b, "%d min", (int)(s->nav.eta_min + 0.5));
        dos_label_set(s_rail_eta, b);
    }
}
