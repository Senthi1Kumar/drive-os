#include "screens.h"
#include "thumbs.h"
#include "ws_client.h"
#include <stdio.h>
#include <string.h>
#include <time.h>

static lv_obj_t *s_sub, *s_clock, *s_ampm, *s_weekday, *s_date, *s_loc;
static lv_obj_t *s_speed, *s_speed_unit, *s_drive_state, *s_coords, *s_car_icon;
static lv_obj_t *s_rows[3], *s_row_t[3], *s_row_s[3], *s_row_r[3], *s_row_dot[3], *s_empty;
static lv_obj_t *s_grid, *s_content;
static char s_places_sig[1536];
static lv_obj_t *s_src_chip;

static void glance_card(lv_obj_t *row, lv_obj_t **out, const char *label)
{
    lv_obj_t *c = dos_card(row);
    lv_obj_set_flex_grow(c, 1);
    lv_obj_set_height(c, 200);
    lv_obj_set_style_pad_all(c, 0, 0);
    lv_obj_t *l = dos_section_label(c, label);
    lv_obj_align(l, LV_ALIGN_TOP_LEFT, 16, 16);
    *out = c;
}

static void cmd_find(lv_event_t *e)
{
    link_cmd_str("find", "category", lv_event_get_user_data(e));
}

static void cmd_drive(lv_event_t *e)
{
    link_cmd_str("drive_to", "place_id", lv_event_get_user_data(e));
}

static void cmd_open(lv_event_t *e)
{
    link_cmd_str("open_place", "id", lv_event_get_user_data(e));
}

static const char *cat_label(const char *c)
{
    static const struct { const char *id, *label; } L[] = {
        { "coffee", "Coffee" }, { "food", "Restaurant" }, { "sandwich", "Sandwiches" }, { "bakery", "Bakery" },
        { "bar", "Bar" }, { "gas", "Fuel" }, { "ev", "EV charging" }, { "store", "Grocery" },
        { "pharmacy", "Pharmacy" }, { "hotel", "Hotel" } };
    for(size_t i = 0; i < sizeof L / sizeof L[0]; i++)
        if(!strcmp(c, L[i].id)) return L[i].label;
    return "Nearby";
}

void home_build(lv_obj_t *slot)
{
    s_content = screen_column(slot, 18);
    lv_obj_t *col = s_content;
    lv_obj_t *head = dos_screen_head(col, "Home", "Ready when you are.", "", NULL, &s_sub);
    (void)head;

    /* ---- glance row ---- */
    lv_obj_t *row = dos_box(col, LV_FLEX_FLOW_ROW);
    lv_obj_set_width(row, lv_pct(100));
    lv_obj_set_height(row, 200);
    lv_obj_set_style_pad_column(row, DOS_GAP, 0);

    lv_obj_t *now;
    glance_card(row, &now, "Now");
    s_clock = dos_text(now, DOS_F46, DOS_INK, "--:--");
    lv_obj_set_style_text_letter_space(s_clock, -2, 0);
    lv_obj_align(s_clock, LV_ALIGN_LEFT_MID, 16, 4);
    s_ampm = dos_text(now, DOS_F13, DOS_ACCENT, "");
    lv_obj_align_to(s_ampm, s_clock, LV_ALIGN_OUT_RIGHT_BOTTOM, 6, -8);
    s_weekday = dos_text(now, DOS_F13, DOS_INK, "");
    lv_obj_align(s_weekday, LV_ALIGN_RIGHT_MID, -16, -16);
    s_date = dos_text(now, DOS_F13, DOS_DIM, "");
    lv_obj_align(s_date, LV_ALIGN_RIGHT_MID, -16, 4);
    s_loc = dos_text(now, DOS_F13, DOS_DIM, "");
    lv_obj_align(s_loc, LV_ALIGN_BOTTOM_LEFT, 16, -16);

    lv_obj_t *veh;
    glance_card(row, &veh, "Vehicle");
    lv_obj_t *tile = dos_box(veh, LV_FLEX_FLOW_ROW);
    lv_obj_set_size(tile, 56, 56);
    lv_obj_set_style_radius(tile, 16, 0);
    lv_obj_set_style_bg_color(tile, DOS_WHITE, 0);
    lv_obj_set_style_bg_opa(tile, DOS_OPA_FILL, 0);
    lv_obj_set_flex_align(tile, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_align(tile, LV_ALIGN_LEFT_MID, 16, 0);
    s_car_icon = dos_icon(tile, DOS_IC_CAR, 30, DOS_ACCENT);
    s_speed = dos_text(veh, DOS_F46, DOS_INK, "0");
    lv_obj_set_style_text_letter_space(s_speed, -1, 0);
    lv_obj_align(s_speed, LV_ALIGN_LEFT_MID, 86, -10);
    s_speed_unit = dos_text(veh, DOS_F13, DOS_ACCENT, "mph");
    s_drive_state = dos_text(veh, DOS_F13, DOS_INK_SOFT, "");
    lv_obj_align(s_drive_state, LV_ALIGN_LEFT_MID, 86, 30);
    s_coords = dos_text(veh, DOS_F13, DOS_DIM, "");
    lv_obj_align(s_coords, LV_ALIGN_BOTTOM_LEFT, 16, -16);

    lv_obj_t *st;
    glance_card(row, &st, "Next up");
    lv_obj_t *list = dos_box(st, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_size(list, lv_pct(100), LV_SIZE_CONTENT);
    lv_obj_set_style_pad_hor(list, 14, 0);
    lv_obj_align(list, LV_ALIGN_TOP_LEFT, 0, 42);
    for(int i = 0; i < 3; i++) {
        lv_obj_t *r = dos_box(list, LV_FLEX_FLOW_ROW);
        s_rows[i] = r;
        lv_obj_set_size(r, lv_pct(100), 50);
        lv_obj_set_flex_align(r, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
        lv_obj_set_style_pad_column(r, 12, 0);
        s_row_dot[i] = lv_obj_create(r);
        lv_obj_remove_style_all(s_row_dot[i]);
        lv_obj_set_size(s_row_dot[i], 9, 9);
        lv_obj_set_style_radius(s_row_dot[i], LV_RADIUS_CIRCLE, 0);
        lv_obj_set_style_bg_color(s_row_dot[i], DOS_LIVE, 0);
        lv_obj_set_style_bg_opa(s_row_dot[i], LV_OPA_COVER, 0);
        lv_obj_t *tc = dos_box(r, LV_FLEX_FLOW_COLUMN);
        lv_obj_set_flex_grow(tc, 1);
        lv_obj_set_style_pad_row(tc, 2, 0);
        s_row_t[i] = dos_text(tc, DOS_F13, DOS_INK, "");
        lv_obj_set_width(s_row_t[i], lv_pct(100));
        lv_label_set_long_mode(s_row_t[i], LV_LABEL_LONG_MODE_DOTS);
        s_row_s[i] = dos_text(tc, DOS_F13, DOS_DIM, "");
        lv_obj_set_width(s_row_s[i], lv_pct(100));
        lv_label_set_long_mode(s_row_s[i], LV_LABEL_LONG_MODE_DOTS);
        s_row_r[i] = dos_text(r, DOS_F13, DOS_LIVE, "");
    }
    s_empty = dos_text(st, DOS_F13, DOS_DIM, "Nothing pending. Ask Nova for anything.");
    lv_obj_align(s_empty, LV_ALIGN_TOP_LEFT, 16, 50);

    /* ---- ask for anything ---- */
    lv_obj_t *ask = dos_box(col, LV_FLEX_FLOW_ROW);
    lv_obj_set_size(ask, lv_pct(100), 36);
    lv_obj_set_flex_align(ask, LV_FLEX_ALIGN_SPACE_BETWEEN, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    dos_section_label(ask, "Ask for anything");
    lv_obj_t *chips = dos_box(ask, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(chips, 8, 0);
    static const struct { const char *cat, *label; dos_icon_t ic; } K[] = {
        { "coffee", "Coffee", DOS_IC_COFFEE }, { "sandwich", "Sandwiches", DOS_IC_SANDWICH }, { "gas", "Gas", DOS_IC_FUEL },
        { "ev", "Charging", DOS_IC_ZAP }, { "bar", "Bars", DOS_IC_WINE }, { "store", "Store", DOS_IC_SHOPPING_BASKET },
        { "pharmacy", "Pharmacy", DOS_IC_PILL },
    };
    for(size_t i = 0; i < sizeof K / sizeof K[0]; i++) {
        lv_obj_t *c = dos_chip(chips, DOS_CHIP_DEFAULT, K[i].ic, K[i].label, false);
        lv_obj_set_clickable(c, true);
        lv_obj_set_style_opa(c, LV_OPA_70, LV_STATE_PRESSED);
        lv_obj_add_event_cb(c, cmd_find, LV_EVENT_CLICKED, (void *)K[i].cat);
    }

    /* ---- closest ---- */
    lv_obj_t *hdr = dos_box(col, LV_FLEX_FLOW_ROW);
    lv_obj_set_size(hdr, lv_pct(100), 40);
    lv_obj_set_flex_align(hdr, LV_FLEX_ALIGN_SPACE_BETWEEN, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    dos_section_label(hdr, "Near you");
    s_src_chip = dos_chip(hdr, DOS_CHIP_ACCENT, DOS_IC_MAP_PIN, "Simulated", true);
    lv_obj_t *all = dos_btn(hdr, DOS_BTN_GHOST, true, -1, "See all");
    lv_obj_add_event_cb(all, cmd_find, LV_EVENT_CLICKED, (void *)"food");

    s_grid = dos_box(col, LV_FLEX_FLOW_ROW_WRAP);
    lv_obj_set_width(s_grid, lv_pct(100));
    lv_obj_set_style_pad_row(s_grid, DOS_GAP, 0);
    lv_obj_set_style_pad_column(s_grid, DOS_GAP, 0);
}

static void fill_row(int i, bool on, const char *title, const char *sub, const char *right, bool lead)
{
    dos_show(s_rows[i], on);
    if(!on) return;
    dos_label_set(s_row_t[i], title);
    dos_label_set(s_row_s[i], sub);
    dos_label_set(s_row_r[i], right);
    lv_obj_set_style_bg_color(s_row_dot[i], lead ? DOS_LIVE : DOS_FAINT, 0);
    lv_obj_set_style_text_color(s_row_r[i], lead ? DOS_LIVE : DOS_INK, 0);
}

static void rebuild_places(const ui_state_t *s)
{
    char sig[sizeof s_places_sig] = "";
    for(int i = 0; i < s->home.n; i++) {
        strncat(sig, s->home.places[i].id, sizeof sig - strlen(sig) - 2);
        strncat(sig, s->home.places[i].img, sizeof sig - strlen(sig) - 2);
        strncat(sig, "|", sizeof sig - strlen(sig) - 1);
    }
    strncat(sig, s->hello.image_root, sizeof sig - strlen(sig) - 1);
    if(!strcmp(sig, s_places_sig)) return;
    snprintf(s_places_sig, sizeof s_places_sig, "%s", sig);
    lv_obj_clean(s_grid);
    int32_t w = (dos_content_w() - DOS_GAP) / 2;
    for(int i = 0; i < s->home.n; i++) {
        const ui_home_place_t *p = &s->home.places[i];
        lv_obj_t *r = dos_card(s_grid);
        lv_obj_set_size(r, w, 84);
        lv_obj_set_style_pad_all(r, 0, 0);
        lv_obj_set_clickable(r, true);
        lv_obj_set_style_bg_color(r, DOS_PANEL, LV_STATE_PRESSED);
        lv_obj_add_event_cb(r, p->orderable ? cmd_open : cmd_drive, LV_EVENT_CLICKED, (void *)p->id);
        lv_obj_t *tile = dos_thumb_img(r, 64, 64, dos_cat_icon(p->category), 26, 14, p->img, p->img_lg);
        lv_obj_align(tile, LV_ALIGN_LEFT_MID, 10, 0);
        lv_obj_set_clickable(tile, false);
        int32_t tw = w - 10 - 64 - 12 - 62;
        lv_obj_t *n = dos_text(r, DOS_F15, DOS_INK, p->name);
        lv_obj_set_width(n, tw);
        lv_label_set_long_mode(n, LV_LABEL_LONG_MODE_DOTS);
        lv_obj_align(n, LV_ALIGN_LEFT_MID, 86, -13);
        char m[96];
        snprintf(m, sizeof m, "%s%s%s%s", cat_label(p->category), p->distance_text[0] ? "  ·  " : "", p->distance_text,
                 p->sample ? "  ·  Sample Only" : (p->orderable ? "" : "  ·  Directions"));
        lv_obj_t *mm = dos_text(r, DOS_F13, DOS_DIM, m);
        lv_obj_set_width(mm, tw);
        lv_label_set_long_mode(mm, LV_LABEL_LONG_MODE_DOTS);
        lv_obj_align(mm, LV_ALIGN_LEFT_MID, 86, 13);
        lv_obj_t *go = dos_btn(r, DOS_BTN_TINT_LIVE, true, DOS_IC_NAVIGATION, NULL);
        lv_obj_align(go, LV_ALIGN_RIGHT_MID, -14, 0);
        lv_obj_add_event_cb(go, cmd_drive, LV_EVENT_CLICKED, (void *)p->id);   /* ids live in the static state mirror */
    }
}

void home_update(const ui_state_t *s)
{
    char a[160], b[160];
    time_t now = time(NULL);
    struct tm tm;
    localtime_r(&now, &tm);
    strftime(a, sizeof a, "%-I:%M", &tm);
    dos_label_set(s_clock, a);
    lv_obj_align_to(s_ampm, s_clock, LV_ALIGN_OUT_RIGHT_BOTTOM, 6, -8);
    strftime(a, sizeof a, "%p", &tm);
    dos_label_set(s_ampm, a);
    strftime(a, sizeof a, "%a", &tm);
    dos_label_set(s_weekday, a);
    strftime(a, sizeof a, "%b %-d", &tm);
    dos_label_set(s_date, a);

    const char *src = s->vehicle.source_text[0] ? s->vehicle.source_text : s->vehicle.label;
    snprintf(a, sizeof a, "Say “Hey Nova”%s%s", src[0] ? " · " : "", src);
    dos_label_set(s_sub, a);
    dos_label_set(s_loc, s->vehicle.label);
    dos_chip_set(s_src_chip, !strcmp(s->vehicle.source, "gps") ? DOS_CHIP_LIVE : DOS_CHIP_ACCENT,
                 src[0] ? src : "Simulated");

    snprintf(a, sizeof a, "%d", (int)(s->vehicle.speed_mps * 2.23694f + 0.5f));
    dos_label_set(s_speed, a);
    lv_obj_align_to(s_speed_unit, s_speed, LV_ALIGN_OUT_RIGHT_BOTTOM, 6, -8);
    dos_label_set(s_drive_state, s->vehicle.parked ? "Parked" : "Driving");
    lv_obj_set_style_image_recolor(s_car_icon, s->vehicle.parked ? DOS_ACCENT : DOS_LIVE, 0);
    snprintf(a, sizeof a, "%.4f, %.4f", s->vehicle.lat, s->vehicle.lon);
    dos_label_set(s_coords, a);

    int n = 0;
    if(s->nav.active) {
        snprintf(a, sizeof a, "Navigating to %s", s->nav.dest);
        snprintf(b, sizeof b, "%d min", (int)(s->nav.eta_min + 0.5));
        fill_row(n, true, a, s->nav.maneuver, s->nav.eta_min >= 0 ? b : "", true);
        n++;
    }
    if(s->shop.checkout_pending || s->shop.cart_count > 0) {
        snprintf(a, sizeof a, s->shop.checkout_pending ? "Checkout waiting" : "Cart · %d item%s", s->shop.cart_count,
                 s->shop.cart_count == 1 ? "" : "s");
        snprintf(b, sizeof b, "%s", s->shop.checkout_pending ? s->shop.checkout_amount_text : s->shop.cart_total_text);
        fill_row(n, true, a, s->shop.checkout_pending ? "Approve to pay" : "Open Shop to review", b, n == 0);
        n++;
    }
    if(s->update.available) {
        snprintf(b, sizeof b, "v%s", s->update.version);
        fill_row(n, true, "Update available", "Open About for details", b, n == 0);
        n++;
    }
    for(int i = n; i < 3; i++) fill_row(i, false, "", "", "", false);
    dos_show(s_empty, n == 0);

    rebuild_places(s);
}
