#include "app.h"
#include "screens/screens.h"
#include "ui_state.h"
#include "ws_client.h"
#include <stdio.h>
#include <string.h>
#include <time.h>

static const char *const TAB_NAMES[] = { "home", "agent", "shop", "nav", "about", "enroll" };

static struct {
    lv_obj_t *root, *stage, *slot, *screens[SCR_COUNT];
    screen_id_t cur;
    uint32_t rev;
    char seen_tab[12];
    bool quit, fixture;
    /* top bar */
    lv_obj_t *clock, *voice_chip, *loc_chip, *speed_chip, *nav_chip, *upd_chip, *cart_chip, *offline_chip;
    /* dock */
    lv_obj_t *mic, *mic_icon, *tabs[4], *tab_icons[4], *tab_labels[4], *badges[4], *settings;
    /* voice float */
    lv_obj_t *vfloat, *vf_dot, *vf_label, *vf_text, *vf_heard_row, *vf_heard;
    lv_obj_t *overlay, *overlay_sub, *toast;
    lv_obj_t *phone, *phone_code, *phone_text;
    bool phone_shown;
} A;

static bool s_quit;
void app_request_quit(void) { s_quit = true; }
bool app_quit_requested(void) { return s_quit; }

int app_screen_from_name(const char *name)
{
    for(int i = 0; i < SCR_COUNT; i++)
        if(!strcmp(name, TAB_NAMES[i])) return i;
    return -1;
}

void app_show(screen_id_t id)
{
    if(id == SCR_ENROLL && A.cur != SCR_ENROLL) enroll_reset();
    A.cur = id;
    for(int i = 0; i < SCR_COUNT; i++) dos_show(A.screens[i], i == (int)id);
    nav_set_visible(id == SCR_NAV);
    app_refresh();
}

/* ---------- top bar ---------- */

static void build_topbar(lv_obj_t *stage)
{
    lv_obj_t *bar = dos_box(stage, LV_FLEX_FLOW_ROW);
    lv_obj_set_size(bar, lv_pct(100), DOS_TOPBAR_H);
    lv_obj_set_style_bg_color(bar, DOS_BG0, 0);
    lv_obj_set_style_bg_opa(bar, 220, 0);
    lv_obj_set_style_border_width(bar, 1, 0);
    lv_obj_set_style_border_side(bar, LV_BORDER_SIDE_BOTTOM, 0);
    lv_obj_set_style_border_color(bar, DOS_WHITE, 0);
    lv_obj_set_style_border_opa(bar, DOS_OPA_LINE, 0);
    lv_obj_set_style_pad_hor(bar, 14, 0);
    lv_obj_set_flex_align(bar, LV_FLEX_ALIGN_SPACE_BETWEEN, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);

    lv_obj_t *left = dos_box(bar, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(left, 10, 0);
    lv_obj_set_flex_align(left, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_t *logo = dos_box(left, LV_FLEX_FLOW_ROW);
    lv_obj_set_size(logo, 30, 30);
    lv_obj_set_style_radius(logo, 9, 0);
    lv_obj_set_style_bg_opa(logo, LV_OPA_COVER, 0);
    static lv_grad_dsc_t logo_g;
    dos_bg_grad(logo, &logo_g, true, DOS_ACCENT, DOS_LIVE);
    lv_obj_set_flex_align(logo, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    dos_icon(logo, DOS_IC_CAR, 16, DOS_ACCENT_INK);
    dos_text(left, DOS_F15, DOS_INK, "DriveOS");
    A.clock = dos_text(left, DOS_F13, DOS_DIM, "");
    A.voice_chip = dos_chip(left, DOS_CHIP_LIVE, -1, "Ready", true);

    lv_obj_t *right = dos_box(bar, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(right, 8, 0);
    lv_obj_set_flex_align(right, LV_FLEX_ALIGN_END, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    A.offline_chip = dos_chip(right, DOS_CHIP_WARN, -1, "Offline · basic commands", false);
    lv_obj_set_hidden(A.offline_chip, true);
    A.upd_chip = dos_chip(right, DOS_CHIP_ACCENT, DOS_IC_REFRESH_CW, "Update", false);
    A.nav_chip = dos_chip(right, DOS_CHIP_LIVE, DOS_IC_ROUTE, "", false);
    A.cart_chip = dos_chip(right, DOS_CHIP_DEFAULT, DOS_IC_SHOPPING_BASKET, "0", false);
    A.loc_chip = dos_chip(right, DOS_CHIP_DEFAULT, DOS_IC_MAP_PIN, "Set location", false);
    A.speed_chip = dos_chip(right, DOS_CHIP_QUIET, DOS_IC_GAUGE, "Parked", false);
}

static void refresh_topbar(const ui_state_t *s)
{
    char b[160];
    time_t t = time(NULL);
    struct tm tm;
    localtime_r(&t, &tm);
    strftime(b, sizeof b, "%-I:%M %p", &tm);
    dos_label_set(A.clock, b);

    const char *st = s->voice.state;
    if(!s->connected) dos_chip_set(A.voice_chip, DOS_CHIP_QUIET, "Offline");
    else if(!strcmp(st, "listening")) dos_chip_set(A.voice_chip, DOS_CHIP_LIVE, "Listening");
    else if(!strcmp(st, "thinking")) dos_chip_set(A.voice_chip, DOS_CHIP_WARN, "Thinking");
    else if(!strcmp(st, "speaking")) dos_chip_set(A.voice_chip, DOS_CHIP_ACCENT, "Speaking");
    else dos_chip_set(A.voice_chip, DOS_CHIP_LIVE, "Ready");

    dos_show(A.offline_chip, s->voice.agent_offline);
    dos_show(A.upd_chip, s->update.available);
    if(s->update.available) {
        snprintf(b, sizeof b, "Update v%s", s->update.version);
        dos_chip_set(A.upd_chip, DOS_CHIP_ACCENT, b);
    }
    dos_show(A.nav_chip, s->nav.active);
    if(s->nav.active) {
        snprintf(b, sizeof b, "%s · %d min", s->nav.dest, (int)(s->nav.eta_min + 0.5));
        dos_chip_set(A.nav_chip, DOS_CHIP_LIVE, b);
    }
    dos_show(A.cart_chip, s->shop.cart_count > 0);
    snprintf(b, sizeof b, "%d", s->shop.cart_count);
    dos_chip_set(A.cart_chip, DOS_CHIP_DEFAULT, b);
    dos_chip_set(A.loc_chip, DOS_CHIP_DEFAULT, s->vehicle.label[0] ? s->vehicle.label : "Set location");
    if(s->vehicle.parked && s->vehicle.speed_mps < 0.3f) dos_chip_set(A.speed_chip, DOS_CHIP_QUIET, "Parked");
    else {
        snprintf(b, sizeof b, "%d mph", (int)(s->vehicle.speed_mps * 2.23694f + 0.5f));
        dos_chip_set(A.speed_chip, DOS_CHIP_LIVE, b);
    }
}

/* ---------- dock ---------- */

static void tab_cb(lv_event_t *e)
{
    intptr_t id = (intptr_t)lv_event_get_user_data(e);
    app_show((screen_id_t)id);
    if(id < SCR_ABOUT) {
        ui_state_set_tab(TAB_NAMES[id]);
        link_cmd_str("show_tab", "tab", TAB_NAMES[id]);
        snprintf(A.seen_tab, sizeof A.seen_tab, "%s", TAB_NAMES[id]);
    }
}

static void mic_cb(lv_event_t *e)
{
    lv_event_code_t c = lv_event_get_code(e);
    if(c == LV_EVENT_PRESSED) link_cmd_bool("ptt", "down", true);
    else if(c == LV_EVENT_RELEASED || c == LV_EVENT_PRESS_LOST) link_cmd_bool("ptt", "down", false);
}

static void build_dock(lv_obj_t *stage)
{
    /* fade the content under the dock */
    lv_obj_t *fade = lv_obj_create(stage);
    lv_obj_remove_style_all(fade);
    lv_obj_set_ignore_layout(fade, true);
    lv_obj_set_clickable(fade, false);
    lv_obj_set_size(fade, lv_pct(100), 130);
    lv_obj_align(fade, LV_ALIGN_BOTTOM_MID, 0, 0);
    static lv_grad_dsc_t fade_g;
    lv_color_t fc[2] = { DOS_BG0, DOS_BG0 };
    lv_opa_t fo[2] = { 0, 242 };
    lv_grad_init_stops(&fade_g, fc, fo, NULL, 2);
    lv_grad_vertical_init(&fade_g);
    lv_obj_set_style_bg_grad(fade, &fade_g, 0);
    lv_obj_set_style_bg_opa(fade, LV_OPA_COVER, 0);

    lv_obj_t *dock = lv_obj_create(stage);
    lv_obj_remove_style_all(dock);
    lv_obj_set_ignore_layout(dock, true);
    lv_obj_set_scrollable(dock, false);
    lv_obj_set_size(dock, lv_pct(100), DOS_DOCK_H);
    lv_obj_align(dock, LV_ALIGN_BOTTOM_MID, 0, 0);

    /* voice float */
    lv_obj_t *f = dos_glass(dock, 20);
    A.vfloat = f;
    lv_obj_set_width(f, 560);
    lv_obj_set_style_max_width(f, lv_pct(94), 0);
    lv_obj_set_height(f, LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(f, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_hor(f, 18, 0);
    lv_obj_set_style_pad_ver(f, 14, 0);
    lv_obj_set_style_pad_row(f, 6, 0);
    lv_obj_align(f, LV_ALIGN_BOTTOM_MID, 0, -82);
    lv_obj_t *h = dos_box(f, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(h, 8, 0);
    lv_obj_set_flex_align(h, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    A.vf_dot = lv_obj_create(h);
    lv_obj_remove_style_all(A.vf_dot);
    lv_obj_set_size(A.vf_dot, 8, 8);
    lv_obj_set_style_radius(A.vf_dot, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_opa(A.vf_dot, LV_OPA_COVER, 0);
    A.vf_label = dos_text(h, DOS_F11, DOS_LIVE, "READY");
    lv_obj_set_style_text_letter_space(A.vf_label, 1, 0);
    A.vf_text = dos_text(f, DOS_F17, DOS_INK, "");
    lv_obj_set_width(A.vf_text, lv_pct(100));
    lv_label_set_long_mode(A.vf_text, LV_LABEL_LONG_MODE_DOTS);
    lv_obj_set_style_max_height(A.vf_text, 48, 0);
    A.vf_heard_row = dos_box(f, LV_FLEX_FLOW_ROW);
    lv_obj_set_width(A.vf_heard_row, lv_pct(100));
    lv_obj_set_style_pad_column(A.vf_heard_row, 8, 0);
    lv_obj_set_flex_align(A.vf_heard_row, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    dos_chip(A.vf_heard_row, DOS_CHIP_ACCENT, -1, "YOU", true);
    A.vf_heard = dos_text(A.vf_heard_row, DOS_F13, DOS_INK_SOFT, "");
    lv_obj_set_flex_grow(A.vf_heard, 1);
    lv_label_set_long_mode(A.vf_heard, LV_LABEL_LONG_MODE_DOTS);

    /* dock row */
    lv_obj_t *row = dos_box(dock, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(row, 10, 0);
    lv_obj_set_flex_align(row, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_align(row, LV_ALIGN_BOTTOM_MID, 0, -14);

    A.mic = lv_obj_create(row);
    lv_obj_remove_style_all(A.mic);
    lv_obj_set_scrollable(A.mic, false);
    lv_obj_set_clickable(A.mic, true);
    lv_obj_set_size(A.mic, 54, 54);
    lv_obj_set_style_radius(A.mic, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_border_width(A.mic, 1, 0);
    lv_obj_set_style_bg_opa(A.mic, LV_OPA_COVER, 0);
    A.mic_icon = dos_icon(A.mic, DOS_IC_MIC, 20, DOS_LIVE);
    lv_obj_center(A.mic_icon);
    lv_obj_add_event_cb(A.mic, mic_cb, LV_EVENT_ALL, NULL);

    lv_obj_t *pill = dos_glass(row, 27);
    lv_obj_set_size(pill, LV_SIZE_CONTENT, 54);
    lv_obj_set_flex_flow(pill, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_all(pill, 5, 0);
    lv_obj_set_style_pad_column(pill, 2, 0);
    lv_obj_set_style_border_opa(pill, DOS_OPA_LINE, 0);
    lv_obj_set_flex_align(pill, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    static const struct { const char *name; dos_icon_t ic; } T[4] = {
        { "Home", DOS_IC_HOUSE }, { "Agent", DOS_IC_BOT }, { "Shop", DOS_IC_STORE }, { "Nav", DOS_IC_NAVIGATION } };
    for(int i = 0; i < 4; i++) {
        lv_obj_t *t = dos_box(pill, LV_FLEX_FLOW_ROW);
        A.tabs[i] = t;
        lv_obj_set_clickable(t, true);
        lv_obj_set_height(t, 44);
        lv_obj_set_style_radius(t, 22, 0);
        lv_obj_set_style_pad_hor(t, 24, 0);
        lv_obj_set_style_pad_column(t, 7, 0);
        lv_obj_set_flex_align(t, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
        A.tab_icons[i] = dos_icon(t, T[i].ic, 16, DOS_DIM);
        A.tab_labels[i] = dos_text(t, DOS_F15, DOS_INK_SOFT, T[i].name);
        A.badges[i] = lv_obj_create(t);
        lv_obj_remove_style_all(A.badges[i]);
        lv_obj_set_ignore_layout(A.badges[i], true);
        lv_obj_set_size(A.badges[i], 7, 7);
        lv_obj_set_style_radius(A.badges[i], LV_RADIUS_CIRCLE, 0);
        lv_obj_set_style_bg_color(A.badges[i], DOS_LIVE, 0);
        lv_obj_set_style_bg_opa(A.badges[i], LV_OPA_COVER, 0);
        lv_obj_align(A.badges[i], LV_ALIGN_TOP_RIGHT, -12, 6);
        lv_obj_set_hidden(A.badges[i], true);
        lv_obj_add_event_cb(t, tab_cb, LV_EVENT_CLICKED, (void *)(intptr_t)i);
    }
    A.settings = dos_round_btn(row, 54, DOS_IC_SETTINGS, 20);
    lv_obj_add_event_cb(A.settings, tab_cb, LV_EVENT_CLICKED, (void *)(intptr_t)SCR_ABOUT);
}

static void refresh_dock(const ui_state_t *s)
{
    const char *st = s->voice.state;
    bool listening = !strcmp(st, "listening"), speaking = !strcmp(st, "speaking"), thinking = !strcmp(st, "thinking");

    lv_obj_set_style_bg_color(A.mic, listening ? DOS_LIVE : DOS_LIVE, 0);
    lv_obj_set_style_bg_opa(A.mic, listening ? LV_OPA_COVER : DOS_OPA_SOFT, 0);
    lv_obj_set_style_border_color(A.mic, DOS_LIVE, 0);
    lv_obj_set_style_border_opa(A.mic, DOS_OPA_LINE_C, 0);
    lv_obj_set_style_image_recolor(A.mic_icon, listening ? DOS_LIVE_INK : DOS_LIVE, 0);

    for(int i = 0; i < 4; i++) {
        bool on = (int)A.cur == i;
        lv_obj_set_style_bg_color(A.tabs[i], DOS_ACCENT, 0);
        lv_obj_set_style_bg_opa(A.tabs[i], on ? DOS_OPA_SOFT : LV_OPA_TRANSP, 0);
        lv_obj_set_style_text_color(A.tab_labels[i], on ? DOS_ACCENT : DOS_INK_SOFT, 0);
        lv_obj_set_style_image_recolor(A.tab_icons[i], on ? DOS_ACCENT : DOS_DIM, 0);
    }
    dos_show(A.badges[2], s->shop.cart_count > 0 || s->shop.checkout_pending);
    dos_show(A.badges[3], s->nav.active);
    bool about = A.cur == SCR_ABOUT || A.cur == SCR_ENROLL;
    lv_obj_set_style_bg_color(A.settings, about ? DOS_ACCENT : DOS_GLASS, 0);
    lv_obj_set_style_bg_opa(A.settings, about ? DOS_OPA_SOFT : DOS_OPA_GLASS, 0);
    lv_obj_set_style_border_color(A.settings, about ? DOS_ACCENT : DOS_WHITE, 0);
    lv_obj_set_style_border_opa(A.settings, about ? DOS_OPA_LINE_C : DOS_OPA_LINE, 0);
    lv_obj_t *si = lv_obj_get_child(A.settings, 0);
    lv_obj_set_style_image_recolor(si, about ? DOS_ACCENT : DOS_INK, 0);

    /* voice float */
    lv_color_t dot = listening ? DOS_LIVE : speaking ? DOS_ACCENT : thinking ? DOS_WARN : DOS_LIVE;
    lv_obj_set_style_bg_color(A.vf_dot, dot, 0);
    dos_text_set_upper(A.vf_label, listening ? "Listening" : speaking ? "Speaking" : thinking ? "Thinking" : "Ready");
    lv_obj_set_style_text_color(A.vf_label, speaking ? DOS_ACCENT : thinking ? DOS_WARN : DOS_LIVE, 0);
    lv_obj_set_style_border_color(A.vfloat, listening ? DOS_LIVE : speaking ? DOS_ACCENT : DOS_WHITE, 0);
    lv_obj_set_style_border_opa(A.vfloat, listening || speaking ? DOS_OPA_LINE_C : DOS_OPA_LINE_HI, 0);

    const char *txt;
    if(listening) txt = s->voice.partial[0] ? s->voice.partial : "Listening — just talk.";
    else if(thinking) txt = s->voice.partial[0] ? s->voice.partial : "Thinking…";
    else if(speaking) txt = s->voice.reply;
    else txt = "Say “Hey Nova”, or hold the voice button";
    bool agent = A.cur == SCR_AGENT;
    /* on the Agent screen the reply and what was heard already fill the stage */
    bool show_text = !(agent && speaking);
    dos_show(A.vf_text, show_text);
    dos_label_set(A.vf_text, txt);
    bool heard = s->voice.heard[0] && !agent;
    dos_show(A.vf_heard_row, heard);
    dos_label_set(A.vf_heard, s->voice.heard);
}

/* ---------- overlays ---------- */

static void build_phone(lv_obj_t *root);
static void build_overlays(lv_obj_t *root, const char *url)
{
    A.overlay = lv_obj_create(root);
    lv_obj_remove_style_all(A.overlay);
    lv_obj_set_size(A.overlay, lv_pct(100), lv_pct(100));
    lv_obj_set_style_bg_color(A.overlay, DOS_BG0, 0);
    lv_obj_set_style_bg_opa(A.overlay, 235, 0);
    lv_obj_set_flex_flow(A.overlay, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(A.overlay, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_row(A.overlay, 14, 0);
    lv_obj_t *sp = lv_spinner_create(A.overlay);
    lv_obj_set_size(sp, 48, 48);
    lv_spinner_set_anim_params(sp, 1000, 240);
    lv_obj_set_style_arc_width(sp, 4, LV_PART_MAIN);
    lv_obj_set_style_arc_width(sp, 4, LV_PART_INDICATOR);
    lv_obj_set_style_arc_color(sp, DOS_WHITE, LV_PART_MAIN);
    lv_obj_set_style_arc_opa(sp, DOS_OPA_LINE, LV_PART_MAIN);
    lv_obj_set_style_arc_color(sp, DOS_LIVE, LV_PART_INDICATOR);
    dos_text(A.overlay, DOS_F17, DOS_INK, "Connecting to DriveOS…");
    char b[160];
    snprintf(b, sizeof b, "%s", url ? url : "");
    A.overlay_sub = dos_text(A.overlay, DOS_F13, DOS_DIM, b);

    A.toast = dos_chip(root, DOS_CHIP_ACCENT, DOS_IC_INFO, "", false);
    build_phone(root);
    lv_obj_set_ignore_layout(A.toast, true); lv_obj_set_hidden(A.toast, true);
    lv_obj_align(A.toast, LV_ALIGN_TOP_MID, 0, 70);
}

static void phone_anim_x(void *o, int32_t v) { lv_obj_set_style_translate_x(o, v, 0); }

static void build_phone(lv_obj_t *root)
{
    lv_obj_t *p = lv_obj_create(root);
    A.phone = p;
    lv_obj_remove_style_all(p);
    lv_obj_set_ignore_layout(p, true);
    lv_obj_set_clickable(p, false);
    lv_obj_set_scrollable(p, false);
    lv_obj_set_size(p, 340, 104);
    lv_obj_set_style_radius(p, 22, 0);
    lv_obj_set_style_bg_color(p, lv_color_hex(0x1c2028), 0);
    lv_obj_set_style_bg_opa(p, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(p, 1, 0);
    lv_obj_set_style_border_color(p, DOS_WHITE, 0);
    lv_obj_set_style_border_opa(p, DOS_OPA_LINE_HI, 0);
    lv_obj_set_style_shadow_width(p, 24, 0);
    lv_obj_set_style_shadow_opa(p, 120, 0);
    lv_obj_set_style_shadow_color(p, lv_color_black(), 0);
    lv_obj_align(p, LV_ALIGN_TOP_RIGHT, -14, 64);
    lv_obj_t *tile = dos_box(p, LV_FLEX_FLOW_ROW);
    lv_obj_set_size(tile, 30, 30);
    lv_obj_set_pos(tile, 14, 16);
    lv_obj_set_style_radius(tile, 8, 0);
    lv_obj_set_style_bg_opa(tile, LV_OPA_COVER, 0);
    static lv_grad_dsc_t g;
    dos_bg_grad(tile, &g, true, DOS_ACCENT, DOS_LIVE);
    lv_obj_set_flex_align(tile, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    dos_icon(tile, DOS_IC_CAR, 16, DOS_ACCENT_INK);
    lv_obj_t *h = dos_text(p, DOS_F11, DOS_DIM, "DRIVEOS · YOUR PHONE");
    lv_obj_set_style_text_letter_space(h, 1, 0);
    lv_obj_set_pos(h, 54, 14);
    lv_obj_t *now = dos_text(p, DOS_F11, DOS_DIM, "NOW");
    lv_obj_align(now, LV_ALIGN_TOP_RIGHT, -16, 14);
    A.phone_code = dos_text(p, DOS_F30, DOS_INK, "");
    lv_obj_set_style_text_letter_space(A.phone_code, 3, 0);
    lv_obj_set_pos(A.phone_code, 54, 30);
    A.phone_text = dos_text(p, DOS_F13, DOS_INK_SOFT, "");
    lv_obj_set_size(A.phone_text, 270, 18);
    lv_label_set_long_mode(A.phone_text, LV_LABEL_LONG_MODE_DOTS);
    lv_obj_set_pos(A.phone_text, 54, 74);
    lv_obj_set_hidden(p, true);
}

static void refresh_phone(const ui_state_t *s)
{
    bool on = s->phone.active && s->connected;
    dos_show(A.phone, on);
    if(on) {
        dos_label_set(A.phone_code, s->phone.code[0] ? s->phone.code : s->phone.title);
        dos_label_set(A.phone_text, s->phone.text);
        if(!A.phone_shown) {   /* slide in from the right */
            lv_anim_t a;
            lv_anim_init(&a);
            lv_anim_set_var(&a, A.phone);
            lv_anim_set_exec_cb(&a, phone_anim_x);
            lv_anim_set_values(&a, 380, 0);
            lv_anim_set_duration(&a, 320);
            lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
            lv_anim_start(&a);
        }
    }
    A.phone_shown = on;
}

static void refresh_overlays(const ui_state_t *s)
{
    refresh_phone(s);
    dos_show(A.overlay, !s->connected);
    dos_show(A.toast, s->toast.active && s->connected);
    if(s->toast.active) {
        dos_chip_kind_t k = !strcmp(s->toast.kind, "ok") ? DOS_CHIP_LIVE : !strcmp(s->toast.kind, "warn") ? DOS_CHIP_WARN
                          : !strcmp(s->toast.kind, "danger") || !strcmp(s->toast.kind, "error") ? DOS_CHIP_DANGER : DOS_CHIP_ACCENT;
        dos_chip_set(A.toast, k, s->toast.text);
        lv_obj_align(A.toast, LV_ALIGN_TOP_MID, 0, 70);
    }
}

/* ---------- refresh & timers ---------- */

void app_refresh(void)
{
    const ui_state_t *s = ui_state();
    A.rev = s->rev;
    refresh_topbar(s);
    refresh_dock(s);
    home_update(s);
    agent_update(s);
    shop_update(s);
    nav_update(s);
    about_update(s);
    enroll_update(s);
    sheets_update(s);
    refresh_overlays(s);
}

static bool s_async_pending;
static void async_cb(void *u)
{
    (void)u;
    s_async_pending = false;
    app_refresh();
}

void app_refresh_async(void)
{
    if(s_async_pending) return;
    s_async_pending = true;
    lv_async_call(async_cb, NULL);
}

static void ui_timer(lv_timer_t *t)
{
    (void)t;
    const ui_state_t *s = ui_state();
    if(strcmp(s->tab, A.seen_tab)) {   /* the server moved the tab */
        snprintf(A.seen_tab, sizeof A.seen_tab, "%s", s->tab);
        int id = app_screen_from_name(s->tab);
        if(id >= 0 && id < SCR_ABOUT && (int)A.cur != id) { app_show((screen_id_t)id); return; }
    }
    if(s->rev != A.rev) app_refresh();
}

static void clock_timer(lv_timer_t *t)
{
    (void)t;
    app_refresh();   /* the minute rolls over, relative texts age */
}

static void link_timer(lv_timer_t *t)
{
    (void)t;
    link_poll();
}

void app_build(lv_obj_t *root, const app_cfg_t *cfg)
{
    memset(&A, 0, sizeof A);
    A.root = root;
    A.fixture = ui_state()->fixture;
    snprintf(A.seen_tab, sizeof A.seen_tab, "%s", ui_state()->tab);

    /* backdrop: the Qt app's near-black ground with a faint blue glow at the top */
    static lv_grad_dsc_t bg_g;
    dos_bg_grad(root, &bg_g, false, lv_color_hex(0x03050a), lv_color_hex(0x010203));
    lv_obj_set_style_bg_opa(root, LV_OPA_COVER, 0);
    lv_obj_t *glow = lv_obj_create(root);
    lv_obj_remove_style_all(glow);
    lv_obj_set_clickable(glow, false);
    lv_obj_set_size(glow, 1200, 700);
    lv_obj_set_style_radius(glow, 600, 0);
    static lv_grad_dsc_t glow_g;
    lv_color_t gc[2] = { DOS_ACCENT, DOS_ACCENT };
    lv_opa_t go[2] = { 26, 0 };
    lv_grad_init_stops(&glow_g, gc, go, NULL, 2);
    lv_grad_radial_init(&glow_g, LV_PCT(50), LV_PCT(50), LV_PCT(100), LV_PCT(50), LV_GRAD_EXTEND_PAD);
    lv_obj_set_style_bg_grad(glow, &glow_g, 0);
    lv_obj_set_style_bg_opa(glow, LV_OPA_COVER, 0);
    lv_obj_align(glow, LV_ALIGN_TOP_MID, 0, -260);

    A.stage = lv_obj_create(root);
    lv_obj_remove_style_all(A.stage);
    lv_obj_set_scrollable(A.stage, false);
    lv_obj_set_size(A.stage, lv_pct(100), lv_pct(100));
    lv_obj_set_style_max_width(A.stage, DOS_STAGE_MAX, 0);
    lv_obj_set_style_bg_color(A.stage, DOS_BG0, 0);
    lv_obj_set_style_bg_opa(A.stage, LV_OPA_COVER, 0);
    lv_obj_set_style_clip_corner(A.stage, true, 0);
    lv_obj_set_flex_flow(A.stage, LV_FLEX_FLOW_COLUMN);
    lv_obj_align(A.stage, LV_ALIGN_TOP_MID, 0, 0);

    build_topbar(A.stage);
    A.slot = lv_obj_create(A.stage);
    lv_obj_remove_style_all(A.slot);
    lv_obj_set_scrollable(A.slot, false);
    lv_obj_set_width(A.slot, lv_pct(100));
    lv_obj_set_flex_grow(A.slot, 1);
    for(int i = 0; i < SCR_COUNT; i++) {
        lv_obj_t *sc = lv_obj_create(A.slot);
        lv_obj_remove_style_all(sc);
        lv_obj_set_size(sc, lv_pct(100), lv_pct(100));
        A.screens[i] = sc;
    }
    lv_obj_update_layout(A.stage);
    home_build(A.screens[SCR_HOME]);
    agent_build(A.screens[SCR_AGENT]);
    shop_build(A.screens[SCR_SHOP]);
    nav_build(A.screens[SCR_NAV]);
    about_build(A.screens[SCR_ABOUT]);
    enroll_build(A.screens[SCR_ENROLL]);
    build_dock(A.stage);
    sheets_build(A.stage);
    build_overlays(root, cfg->url);

    int id = cfg->pin_initial ? (int)cfg->initial : app_screen_from_name(ui_state()->tab);
    if(id < 0) id = SCR_HOME;
    app_show((screen_id_t)id);

    lv_timer_create(ui_timer, 40, NULL);
    lv_timer_create(clock_timer, 1000, NULL);
    if(cfg->url) lv_timer_create(link_timer, 10, NULL);
}
