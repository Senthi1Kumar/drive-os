#include "screens.h"
#include "preview.h"
#include "thumbs.h"
#include "ws_client.h"
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---------- item sheet ---------- */

static lv_obj_t *s_item_layer, *s_item_box, *s_add_label;
static uint32_t s_item_built, s_item_gen = 1, s_item_built_gen;
static bool s_item_valid;
static char s_item_id[96];
static int s_qty = 1;

static void cmd_back(lv_event_t *e) { (void)e; link_cmd("back", NULL); }

static double money_num(const char *t)
{
    while(*t && !isdigit((unsigned char)*t)) t++;
    return atof(t);
}

static void qty_changed(int v, void *ud)
{
    (void)ud;
    s_qty = v;
    const ui_state_t *s = ui_state();
    char m[24], b[48];
    dos_fmt_money(m, sizeof m, money_num(s->shop.item.price_text) * v);
    snprintf(b, sizeof b, "Add · %s", m);
    if(s_add_label) lv_label_set_text(s_add_label, b);
}

static void add_clicked(lv_event_t *e)
{
    (void)e;
    link_cmd_str_int("add_item", "id", ui_state()->shop.item.id, "qty", s_qty);
}

/* Tap on an option chip: the back-end picks (or toggles) it and sends the item back with the matching photo. */
static void option_clicked(lv_event_t *e)
{
    int g = (int)((intptr_t)lv_event_get_user_data(e) / 256), o = (int)((intptr_t)lv_event_get_user_data(e) % 256);
    const ui_state_t *s = ui_state();
    if(g < 0 || g >= s->shop.item.n_groups || o < 0 || o >= s->shop.item.groups[g].n) return;
    char args[160];
    snprintf(args, sizeof args, "{\"group\":\"%s\",\"option\":\"%s\"}", s->shop.item.groups[g].id,
             s->shop.item.groups[g].options[o].id);
    link_cmd("set_option", args);
}

static lv_obj_t *scrim(lv_obj_t *parent, lv_opa_t opa)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_set_ignore_layout(o, true);
    lv_obj_set_size(o, lv_pct(100), lv_pct(100));
    lv_obj_set_pos(o, 0, 0);
    lv_obj_set_style_bg_color(o, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(o, opa, 0);
    lv_obj_set_clickable(o, true);
    lv_obj_set_scrollable(o, false);
    return o;
}

static lv_obj_t *panel_box(lv_obj_t *layer, int32_t w, int32_t h, int32_t dy)
{
    lv_obj_t *b = lv_obj_create(layer);
    lv_obj_remove_style_all(b);
    lv_obj_set_scrollable(b, false);
    lv_obj_set_clickable(b, true);
    lv_obj_set_size(b, w, h);
    lv_obj_set_style_max_width(b, lv_pct(96), 0);
    lv_obj_set_style_radius(b, 26, 0);
    lv_obj_set_style_bg_color(b, DOS_PANEL, 0);
    lv_obj_set_style_bg_opa(b, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(b, 1, 0);
    lv_obj_set_style_border_color(b, DOS_WHITE, 0);
    lv_obj_set_style_border_opa(b, DOS_OPA_LINE_HI, 0);
    lv_obj_set_style_clip_corner(b, true, 0);
    lv_obj_align(b, LV_ALIGN_CENTER, 0, dy);
    return b;
}

static void fill_item(const ui_state_t *s)
{
    lv_obj_clean(s_item_box);
    const typeof(s->shop.item) *it = &s->shop.item;
    int32_t bh = lv_obj_get_height(s_item_box);
    int32_t bw = lv_obj_get_width(s_item_box);
    lv_obj_t *media = dos_thumb_img(s_item_box, bw, 230, DOS_IC_UTENSILS, 30, 0, it->img_lg, it->img_lg);
    lv_obj_set_pos(media, 0, 0);
    lv_obj_t *x = dos_box(s_item_box, LV_FLEX_FLOW_ROW);
    lv_obj_set_size(x, 40, 40);
    lv_obj_align(x, LV_ALIGN_TOP_RIGHT, -14, 14);
    lv_obj_set_style_radius(x, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(x, lv_color_black(), 0);
    lv_obj_set_style_bg_opa(x, 140, 0);
    lv_obj_set_style_border_width(x, 1, 0);
    lv_obj_set_style_border_color(x, DOS_WHITE, 0);
    lv_obj_set_style_border_opa(x, DOS_OPA_LINE_HI, 0);
    lv_obj_set_flex_align(x, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_clickable(x, true);
    dos_icon(x, DOS_IC_X, 16, DOS_INK);
    lv_obj_add_event_cb(x, cmd_back, LV_EVENT_CLICKED, NULL);

    lv_obj_t *body = dos_box(s_item_box, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_pos(body, 20, 246);
    lv_obj_set_size(body, bw - 40, bh - 246 - 132);
    lv_obj_set_scrollable(body, true);
    lv_obj_set_style_pad_row(body, 8, 0);
    lv_obj_set_scrollbar_mode(body, LV_SCROLLBAR_MODE_OFF);
    dos_section_label(body, s->shop.place_name[0] ? s->shop.place_name : "Item");
    lv_obj_t *n = dos_text(body, DOS_F17, DOS_INK, it->name);
    lv_obj_set_width(n, lv_pct(100));
    dos_text(body, DOS_F17, DOS_INK, it->price_text);
    if(it->desc[0]) {
        lv_obj_t *d = dos_text(body, DOS_F13, DOS_INK_SOFT, it->desc);
        lv_obj_set_width(d, lv_pct(100));
    }
    for(int g = 0; g < it->n_groups; g++) {
        lv_obj_t *l = dos_section_label(body, it->groups[g].name);
        lv_obj_set_style_pad_top(l, 6, 0);
        lv_obj_t *flow = dos_box(body, LV_FLEX_FLOW_ROW_WRAP);
        lv_obj_set_width(flow, lv_pct(100));
        lv_obj_set_style_pad_column(flow, 8, 0);
        lv_obj_set_style_pad_row(flow, 8, 0);
        for(int o = 0; o < it->groups[g].n; o++) {
            const ui_option_t *op = &it->groups[g].options[o];
            char b[80];
            snprintf(b, sizeof b, "%s%s%s", op->name, op->delta[0] ? "  " : "", op->delta);
            lv_obj_t *chip = dos_chip(flow, op->on ? DOS_CHIP_LIVE : DOS_CHIP_DEFAULT, op->on ? DOS_IC_CHECK : -1, b, false);
            lv_obj_set_clickable(chip, true);
            lv_obj_add_event_cb(chip, option_clicked, LV_EVENT_CLICKED, (void *)(intptr_t)(g * 256 + o));
        }
    }

    lv_obj_t *act = lv_obj_create(s_item_box);
    lv_obj_remove_style_all(act);
    lv_obj_set_scrollable(act, false);
    lv_obj_set_size(act, bw, 132);
    lv_obj_align(act, LV_ALIGN_BOTTOM_MID, 0, 0);
    lv_obj_set_style_bg_color(act, DOS_PANEL, 0);
    lv_obj_set_style_bg_opa(act, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(act, 1, 0);
    lv_obj_set_style_border_side(act, LV_BORDER_SIDE_TOP, 0);
    lv_obj_set_style_border_color(act, DOS_WHITE, 0);
    lv_obj_set_style_border_opa(act, DOS_OPA_LINE, 0);
    lv_obj_t *ql = dos_section_label(act, "Quantity");
    lv_obj_set_pos(ql, 20, 26);
    lv_obj_t *q = dos_qty(act, s_qty, 1, 20, qty_changed, NULL);
    lv_obj_align(q, LV_ALIGN_TOP_RIGHT, -20, 14);
    lv_obj_t *add = dos_btn(act, DOS_BTN_PRIMARY, false, DOS_IC_PLUS, "");
    lv_obj_set_width(add, bw - 40);
    lv_obj_align(add, LV_ALIGN_BOTTOM_MID, 0, -16);
    s_add_label = dos_text(add, DOS_F15, DOS_ACCENT_INK, "");
    lv_obj_add_event_cb(add, add_clicked, LV_EVENT_CLICKED, NULL);
    qty_changed(s_qty, NULL);
}

/* ---------- checkout sheet ---------- */

static lv_obj_t *s_co_layer, *s_co_box, *s_ring, *s_ring_icon;
static uint32_t s_co_built, s_co_gen = 1, s_co_built_gen;
static bool s_co_valid;
static char s_otp[8];
static int s_otp_n;
static bool s_otp_sent;
static const ui_state_t *s_cur;

static void cmd_cancel(lv_event_t *e) { (void)e; link_cmd("cancel_checkout", NULL); }
static void cmd_method(lv_event_t *e) { link_cmd_str("choose_auth", "method", lv_event_get_user_data(e)); }

static void otp_key(lv_event_t *e)
{
    const char *k = lv_event_get_user_data(e);
    if(!strcmp(k, "resend")) {
        s_otp_n = 0; s_otp[0] = 0;
        link_cmd_str("choose_auth", "method", "code");
    }
    else if(!strcmp(k, "del")) {
        if(s_otp_n > 0) s_otp[--s_otp_n] = 0;
    }
    else if(s_otp_n < 6) {
        s_otp[s_otp_n++] = k[0];
        s_otp[s_otp_n] = 0;
        if(s_otp_n == 6) {
            link_cmd_str("otp_code", "code", s_otp);
            s_otp_sent = true;
        }
    }
    s_co_gen++;
    app_refresh_async();
}

static bool is_digits(const char *t)
{
    int n = 0;
    for(; *t; t++) {
        if(isdigit((unsigned char)*t)) n++;
        else if(*t != ' ') return false;
    }
    return n >= 3;
}

static lv_obj_t *method_tile(lv_obj_t *row, const char *cmd, dos_icon_t ic, const char *name, const char *hint, bool allowed, bool active, int32_t w)
{
    lv_obj_t *t = dos_box(row, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_size(t, w, 96);
    lv_obj_set_style_radius(t, 16, 0);
    lv_obj_set_style_bg_color(t, active ? DOS_ACCENT : DOS_WHITE, 0);
    lv_obj_set_style_bg_opa(t, active ? DOS_OPA_SOFT : DOS_OPA_FILL, 0);
    lv_obj_set_style_border_width(t, active ? 2 : 1, 0);
    lv_obj_set_style_border_color(t, active ? DOS_ACCENT : DOS_WHITE, 0);
    lv_obj_set_style_border_opa(t, active ? LV_OPA_COVER : DOS_OPA_LINE, 0);
    lv_obj_set_style_pad_row(t, 5, 0);
    lv_obj_set_flex_align(t, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    if(!allowed) lv_obj_set_style_opa(t, 97, 0);
    else {
        lv_obj_set_clickable(t, true);
        lv_obj_set_style_bg_opa(t, DOS_OPA_FILL_HI, LV_STATE_PRESSED);
        lv_obj_add_event_cb(t, cmd_method, LV_EVENT_CLICKED, (void *)cmd);
    }
    dos_icon(t, ic, 24, active ? DOS_ACCENT : DOS_INK);
    dos_text(t, DOS_F15, DOS_INK, name);
    lv_obj_t *h = dos_text(t, DOS_F11, DOS_DIM, "");
    lv_obj_set_style_text_font(h, dos_font(DOS_F13), 0);
    lv_label_set_text(h, allowed ? hint : (!strcmp(hint, "Unavailable") ? hint : "Not for this amount"));
    return t;
}

/* ---------- Face ID camera panel ---------- */

#define FACE_S 240          /* the square the camera image is shown in */
#define BR_T 2              /* bracket stroke: thin, not bold */
#define BR_OPA 190          /* semi-transparent over the face */

static lv_obj_t *s_pv_img, *s_pv_icon, *s_pv_br[8], *s_pv_bk[8];   /* brackets, and a faint dark backing for contrast */

/* Four L-shaped corners around the face (or a centred frame until a face is found), eased towards the target. */
static void brackets_place(void)
{
    if(!s_pv_br[0]) return;
    const pv_rect_t *r = preview_rect();
    const ui_state_t *s = ui_state();
    float x, y, w, h;
    if(r->valid && preview_image()) {
        float k = (float)FACE_S / (float)preview_image()->header.w;
        x = r->x * k; y = r->y * k; w = r->w * k; h = r->h * k;
    }
    else if(s->face_rect.valid) {
        x = s->face_rect.x * FACE_S; y = s->face_rect.y * FACE_S; w = s->face_rect.w * FACE_S; h = s->face_rect.h * FACE_S;
    }
    else {
        w = h = FACE_S * 0.62f;
        x = (FACE_S - w) / 2; y = (FACE_S - h) / 2 - 6;
    }
    const float pad = 6;
    x -= pad; y -= pad; w += 2 * pad; h += 2 * pad;
    if(x < 3) { w -= 3 - x; x = 3; }
    if(y < 3) { h -= 3 - y; y = 3; }
    if(x + w > FACE_S - 3) w = FACE_S - 3 - x;
    if(y + h > FACE_S - 3) h = FACE_S - 3 - y;
    if(w < 24) w = 24;
    if(h < 24) h = 24;
    float arm = (w < h ? w : h) * 0.22f;
    if(arm < 14) arm = 14;
    if(arm > 34) arm = 34;
    int32_t ix = (int32_t)x, iy = (int32_t)y, iw = (int32_t)w, ih = (int32_t)h, ia = (int32_t)arm;
    const int32_t px[8] = { ix, ix, ix + iw - ia, ix + iw - BR_T, ix, ix, ix + iw - ia, ix + iw - BR_T };
    const int32_t py[8] = { iy, iy, iy, iy, iy + ih - BR_T, iy + ih - ia, iy + ih - BR_T, iy + ih - ia };
    const bool horiz[8] = { true, false, true, false, true, false, true, false };
    for(int i = 0; i < 8; i++) {
        lv_obj_set_pos(s_pv_br[i], px[i], py[i]);
        lv_obj_set_size(s_pv_br[i], horiz[i] ? ia : BR_T, horiz[i] ? BR_T : ia);
        lv_obj_set_pos(s_pv_bk[i], px[i] - 1, py[i] - 1);
        lv_obj_set_size(s_pv_bk[i], (horiz[i] ? ia : BR_T) + 2, (horiz[i] ? BR_T : ia) + 2);
    }
}

static void pv_frame(void *ud)
{
    (void)ud;
    if(!s_pv_img) {      /* the first frame of a scan arrived before the sheet showed the camera: rebuild it */
        if(preview_frames() == 1 && ui_state()->shop.auth_stage == UI_AUTH_FACE) { s_co_gen++; app_refresh_async(); }
        return;
    }
    if(!lv_obj_is_valid(s_pv_img)) { s_pv_img = NULL; return; }
    if(lv_image_get_src(s_pv_img) != preview_image()) {
        lv_image_set_src(s_pv_img, preview_image());
        if(s_pv_icon) lv_obj_set_hidden(s_pv_icon, true);
    }
    else lv_obj_invalidate(s_pv_img);
    brackets_place();
}

/* The square preview with corner brackets (white scanning, green verified, red failed) and the line beneath it. */
static void face_panel(lv_obj_t *c, const ui_state_t *s)
{
    bool verified = !strcmp(s->shop.face_state, "verified"), failed = !strcmp(s->shop.face_state, "failed");
    lv_color_t col = verified ? DOS_OK : failed ? DOS_DANGER : DOS_WHITE;
    lv_obj_t *wrap = lv_obj_create(c);
    lv_obj_remove_style_all(wrap);
    lv_obj_set_scrollable(wrap, false);
    lv_obj_set_size(wrap, FACE_S, FACE_S);
    lv_obj_set_style_radius(wrap, 22, 0);
    lv_obj_set_style_clip_corner(wrap, true, 0);
    lv_obj_set_style_bg_color(wrap, DOS_CARD, 0);
    lv_obj_set_style_bg_opa(wrap, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(wrap, 1, 0);
    lv_obj_set_style_border_color(wrap, DOS_WHITE, 0);
    lv_obj_set_style_border_opa(wrap, DOS_OPA_LINE_HI, 0);
    s_pv_icon = dos_icon(wrap, DOS_IC_SCAN_FACE, 84, DOS_FAINT);
    lv_obj_center(s_pv_icon);
    s_pv_img = lv_image_create(wrap);
    lv_obj_set_size(s_pv_img, FACE_S, FACE_S);
    lv_obj_set_pos(s_pv_img, 0, 0);
    lv_image_set_inner_align(s_pv_img, LV_IMAGE_ALIGN_STRETCH);
    if(preview_image()) {
        lv_image_set_src(s_pv_img, preview_image());
        lv_obj_set_hidden(s_pv_icon, true);
    }
    for(int i = 0; i < 8; i++) {
        lv_obj_t *k = lv_obj_create(wrap);
        lv_obj_remove_style_all(k);
        lv_obj_set_scrollable(k, false);
        lv_obj_set_clickable(k, false);
        lv_obj_set_style_bg_color(k, lv_color_black(), 0);
        lv_obj_set_style_bg_opa(k, 70, 0);
        s_pv_bk[i] = k;
        lv_obj_t *b = lv_obj_create(wrap);
        lv_obj_remove_style_all(b);
        lv_obj_set_scrollable(b, false);
        lv_obj_set_clickable(b, false);
        lv_obj_set_style_bg_color(b, col, 0);
        lv_obj_set_style_bg_opa(b, BR_OPA, 0);
        s_pv_br[i] = b;
    }
    brackets_place();
    char line[96];
    if(verified) {
        if(s->shop.face_name[0]) snprintf(line, sizeof line, "Face verified · %s", s->shop.face_name);
        else snprintf(line, sizeof line, "Face verified");
    }
    else if(failed) snprintf(line, sizeof line, "Face didn’t match");
    else snprintf(line, sizeof line, "Look at the camera");
    lv_obj_t *t = dos_text(c, DOS_F17, verified ? DOS_OK : failed ? DOS_DANGER : DOS_INK, line);
    lv_obj_set_style_text_align(t, LV_TEXT_ALIGN_CENTER, 0);
}

static bool face_panel_wanted(const ui_state_t *s)
{
    const char *st = s->shop.face_state;
    bool result = !strcmp(st, "verified") || !strcmp(st, "failed");
    if(s->shop.auth_stage == UI_AUTH_FACE) return s->shop.auth_preview || preview_image() || result;
    return s->shop.auth_stage == UI_AUTH_REJECTED && !strcmp(st, "failed");
}

static void center_col(lv_obj_t *pane, lv_obj_t **col)
{
    lv_obj_t *c = dos_box(pane, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_size(c, lv_pct(100), lv_pct(100));
    lv_obj_set_flex_align(c, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_row(c, 10, 0);
    *col = c;
}

static void fill_stage(lv_obj_t *pane, const ui_state_t *s)
{
    lv_obj_t *c;
    center_col(pane, &c);
    s_ring = NULL;
    s_pv_img = s_pv_icon = NULL;
    memset(s_pv_br, 0, sizeof s_pv_br);
    memset(s_pv_bk, 0, sizeof s_pv_bk);
    if(face_panel_wanted(s)) {
        face_panel(c, s);
        if(s->shop.auth_stage == UI_AUTH_REJECTED) {
            lv_obj_t *n = dos_text(c, DOS_F13, DOS_DIM, "Nothing was charged. Choose a method to try again.");
            lv_obj_set_style_text_align(n, LV_TEXT_ALIGN_CENTER, 0);
        }
        return;
    }
    switch(s->shop.auth_stage) {
        case UI_AUTH_CHALLENGE: {
            bool digits = is_digits(s->shop.auth_challenge) && !s->shop.voice_consent;
            bool choose = !strncmp(s->shop.auth_challenge, "Choose", 6) || !s->shop.auth_challenge[0];
            if(choose) {
                dos_icon(c, DOS_IC_SHIELD_CHECK, 40, DOS_LIVE);
                dos_text(c, DOS_F17, DOS_INK, s->shop.method_voice ? "Say “confirm” to place it" : "Use Face ID or a one-time code");
                lv_obj_t *t = dos_text(c, DOS_F13, DOS_DIM, "Pick how to approve above.");
                lv_obj_set_style_text_align(t, LV_TEXT_ALIGN_CENTER, 0);
                break;
            }
            lv_obj_t *wrap = lv_obj_create(c);
            lv_obj_remove_style_all(wrap);
            lv_obj_set_scrollable(wrap, false);
            lv_obj_set_size(wrap, 150, 150);
            s_ring = lv_obj_create(wrap);
            lv_obj_remove_style_all(s_ring);
            lv_obj_set_size(s_ring, 110, 110);
            lv_obj_center(s_ring);
            lv_obj_set_style_radius(s_ring, LV_RADIUS_CIRCLE, 0);
            lv_obj_set_style_bg_color(s_ring, DOS_LIVE, 0);
            lv_obj_set_style_bg_opa(s_ring, DOS_OPA_SOFT, 0);
            lv_obj_set_style_border_width(s_ring, 2, 0);
            lv_obj_set_style_border_color(s_ring, DOS_LIVE, 0);
            lv_obj_set_style_border_opa(s_ring, DOS_OPA_LINE_C, 0);
            s_ring_icon = dos_icon(wrap, DOS_IC_MIC, 40, DOS_LIVE);
            lv_obj_center(s_ring_icon);
            dos_text(c, DOS_F13, DOS_DIM, digits ? (s->shop.auth_detail[0] ? s->shop.auth_detail : "Say these digits") : "Say it now");
            lv_obj_t *t = dos_text(c, digits ? DOS_F46 : DOS_F17, DOS_INK, !digits && s->shop.voice_consent ? "Say “confirm” to pay" : s->shop.auth_challenge);
            if(digits) lv_obj_set_style_text_letter_space(t, 6, 0);
            break;
        }
        case UI_AUTH_VERIFYING: {
            lv_obj_t *sp = lv_spinner_create(c);
            lv_obj_set_size(sp, 48, 48);
            lv_spinner_set_anim_params(sp, 1000, 240);
            lv_obj_set_style_arc_width(sp, 4, LV_PART_MAIN);
            lv_obj_set_style_arc_width(sp, 4, LV_PART_INDICATOR);
            lv_obj_set_style_arc_color(sp, DOS_WHITE, LV_PART_MAIN);
            lv_obj_set_style_arc_opa(sp, DOS_OPA_LINE, LV_PART_MAIN);
            lv_obj_set_style_arc_color(sp, DOS_ACCENT, LV_PART_INDICATOR);
            dos_text(c, DOS_F17, DOS_INK, "Checking it’s you…");
            break;
        }
        case UI_AUTH_FACE: {
            lv_obj_t *fr = dos_box(c, LV_FLEX_FLOW_ROW);
            lv_obj_set_size(fr, 150, 150);
            lv_obj_set_style_radius(fr, 34, 0);
            lv_obj_set_style_bg_color(fr, DOS_ACCENT, 0);
            lv_obj_set_style_bg_opa(fr, 15, 0);
            lv_obj_set_style_border_width(fr, 2, 0);
            lv_obj_set_style_border_color(fr, DOS_ACCENT, 0);
            lv_obj_set_style_border_opa(fr, DOS_OPA_LINE_C, 0);
            lv_obj_set_flex_align(fr, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
            dos_icon(fr, DOS_IC_SCAN_FACE, 84, DOS_ACCENT);
            dos_text(c, DOS_F17, DOS_INK, "Look at the camera");
            break;
        }
        case UI_AUTH_OTP: {
            const char *note = !strncmp(s->shop.auth_challenge, "Code sent", 9) ? s->shop.auth_challenge : "Code sent to your phone";
            dos_text(c, DOS_F13, DOS_DIM, note);
            if(s->shop.voice_entry) dos_text(c, DOS_F15, DOS_INK, "Say the code or type it");
            lv_obj_t *boxes = dos_box(c, LV_FLEX_FLOW_ROW);
            lv_obj_set_style_pad_column(boxes, 8, 0);
            for(int i = 0; i < 6; i++) {
                lv_obj_t *b = dos_box(boxes, LV_FLEX_FLOW_ROW);
                lv_obj_set_size(b, 44, 52);
                lv_obj_set_style_radius(b, 12, 0);
                lv_obj_set_style_bg_color(b, DOS_WHITE, 0);
                lv_obj_set_style_bg_opa(b, DOS_OPA_FILL, 0);
                bool has = i < s_otp_n;
                lv_obj_set_style_border_width(b, has ? 2 : 1, 0);
                lv_obj_set_style_border_color(b, has ? DOS_ACCENT : DOS_WHITE, 0);
                lv_obj_set_style_border_opa(b, has ? LV_OPA_COVER : i == s_otp_n ? DOS_OPA_LINE_HI : DOS_OPA_LINE, 0);
                lv_obj_set_flex_align(b, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
                char ch[2] = { has ? s_otp[i] : 0, 0 };
                dos_text(b, DOS_F30, DOS_INK, ch);
            }
            lv_obj_t *err = dos_text(c, DOS_F13, DOS_DANGER, s->shop.auth_stage == UI_AUTH_OTP ? s->shop.auth_detail : "");
            lv_obj_set_height(err, 16);
            lv_obj_t *grid = dos_box(c, LV_FLEX_FLOW_ROW_WRAP);
            lv_obj_set_width(grid, 6 * 54 + 5 * 6 + 12);
            lv_obj_set_style_pad_column(grid, 6, 0);
            lv_obj_set_style_pad_row(grid, 6, 0);
            static const char *const K[] = { "1", "2", "3", "4", "5", "del", "6", "7", "8", "9", "0", "resend" };
            for(int i = 0; i < 12; i++) {
                lv_obj_t *k = dos_box(grid, LV_FLEX_FLOW_ROW);
                lv_obj_set_size(k, !strcmp(K[i], "resend") ? 66 : 54, 42);
                lv_obj_set_style_radius(k, 11, 0);
                lv_obj_set_style_bg_color(k, DOS_WHITE, 0);
                lv_obj_set_style_bg_opa(k, DOS_OPA_FILL, 0);
                lv_obj_set_style_bg_opa(k, DOS_OPA_FILL_HI, LV_STATE_PRESSED);
                lv_obj_set_style_border_width(k, 1, 0);
                lv_obj_set_style_border_color(k, DOS_WHITE, 0);
                lv_obj_set_style_border_opa(k, DOS_OPA_LINE, 0);
                lv_obj_set_flex_align(k, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
                lv_obj_set_clickable(k, true);
                if(!strcmp(K[i], "del")) dos_icon(k, DOS_IC_DELETE, 20, DOS_INK);
                else if(!strcmp(K[i], "resend")) dos_text(k, DOS_F13, DOS_ACCENT, "Resend");
                else dos_text(k, DOS_F17, DOS_INK, K[i]);
                lv_obj_add_event_cb(k, otp_key, LV_EVENT_CLICKED, (void *)K[i]);
            }
            break;
        }
        case UI_AUTH_ACCEPTED:
            dos_icon(c, DOS_IC_SHIELD_CHECK, 56, DOS_OK);
            dos_text(c, DOS_F17, DOS_INK, "Approved");
            dos_text(c, DOS_F13, DOS_OK, "Placing your order…");
            break;
        case UI_AUTH_REJECTED: {
            dos_icon(c, DOS_IC_CIRCLE_X, 40, DOS_DANGER);
            dos_text(c, DOS_F17, DOS_INK, s->shop.auth_detail[0] ? s->shop.auth_detail : "Not verified");
            dos_text(c, DOS_F13, DOS_DIM, "Nothing was charged. Choose a method to try again.");
            break;
        }
        default:
            dos_icon(c, DOS_IC_SHIELD_CHECK, 40, DOS_LIVE);
            dos_text(c, DOS_F17, DOS_INK, "Choose how to approve");
    }
}

static void fill_checkout(const ui_state_t *s)
{
    lv_obj_clean(s_co_box);
    int32_t bw = lv_obj_get_width(s_co_box), bh = lv_obj_get_height(s_co_box);

    /* left: what you pay for */
    lv_obj_t *left = dos_box(s_co_box, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_pos(left, 24, 22);
    lv_obj_set_size(left, 330, bh - 44);
    lv_obj_set_style_pad_row(left, 6, 0);
    dos_section_label(left, "Approve order");
    lv_obj_t *m = dos_text(left, DOS_F17, DOS_INK, s->shop.checkout_merchant);
    lv_obj_set_width(m, lv_pct(100));
    lv_label_set_long_mode(m, LV_LABEL_LONG_MODE_DOTS);
    lv_obj_t *amt = dos_text(left, DOS_F46, DOS_INK, s->shop.checkout_amount_text);
    lv_obj_set_style_text_letter_space(amt, -1, 0);
    lv_obj_t *dv = lv_obj_create(left);
    lv_obj_remove_style_all(dv);
    lv_obj_set_size(dv, lv_pct(100), 1);
    lv_obj_set_style_bg_color(dv, DOS_WHITE, 0);
    lv_obj_set_style_bg_opa(dv, DOS_OPA_LINE, 0);
    lv_obj_set_style_margin_ver(dv, 8, 0);
    for(int i = 0; i < s->shop.cart_lines && i < 6; i++) {
        lv_obj_t *r = dos_box(left, LV_FLEX_FLOW_ROW);
        lv_obj_set_width(r, lv_pct(100));
        lv_obj_set_flex_align(r, LV_FLEX_ALIGN_SPACE_BETWEEN, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
        char b[120];
        snprintf(b, sizeof b, "%d × %s", s->shop.lines[i].qty, s->shop.lines[i].name);
        lv_obj_t *t = dos_text(r, DOS_F13, DOS_INK_SOFT, b);
        lv_obj_set_width(t, 240);
        lv_label_set_long_mode(t, LV_LABEL_LONG_MODE_DOTS);
        dos_text(r, DOS_F13, DOS_INK, s->shop.lines[i].price_text);
    }
    lv_obj_t *foot = dos_box(left, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_width(foot, lv_pct(100));
    lv_obj_set_style_pad_row(foot, 4, 0);
    lv_obj_set_style_margin_top(foot, 12, 0);
    const char *nm[2] = { "Subtotal", "Tax" };
    double vals[2] = { s->shop.subtotal, s->shop.tax };
    for(int i = 0; i < 2; i++) {
        lv_obj_t *r = dos_box(foot, LV_FLEX_FLOW_ROW);
        lv_obj_set_width(r, lv_pct(100));
        lv_obj_set_flex_align(r, LV_FLEX_ALIGN_SPACE_BETWEEN, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
        dos_text(r, DOS_F13, DOS_DIM, nm[i]);
        char b[24];
        dos_fmt_money(b, sizeof b, vals[i]);
        dos_text(r, DOS_F13, DOS_INK_SOFT, b);
    }
    lv_obj_t *note = dos_text(foot, DOS_F11, DOS_FAINT, "Exactly this amount will be charged. If anything changes, you’ll be asked again.");
    lv_obj_set_style_text_font(note, dos_font(DOS_F13), 0);
    lv_obj_set_width(note, lv_pct(100));
    lv_obj_set_style_pad_top(note, 6, 0);

    lv_obj_t *vd = lv_obj_create(s_co_box);
    lv_obj_remove_style_all(vd);
    lv_obj_set_size(vd, 1, bh - 44);
    lv_obj_set_pos(vd, 24 + 330 + 22, 22);
    lv_obj_set_style_bg_color(vd, DOS_WHITE, 0);
    lv_obj_set_style_bg_opa(vd, DOS_OPA_LINE, 0);

    /* right: how you approve */
    int32_t rx = 24 + 330 + 46, rw = bw - rx - 24;
    lv_obj_t *methods = dos_box(s_co_box, LV_FLEX_FLOW_ROW);
    lv_obj_set_pos(methods, rx, 22);
    lv_obj_set_width(methods, rw);
    lv_obj_set_style_pad_column(methods, 10, 0);
    int32_t tw = (rw - 20) / 3;
    ui_auth_stage_t st = s->shop.auth_stage;
    bool voice_active = st == UI_AUTH_CHALLENGE && strncmp(s->shop.auth_challenge, "Choose", 6) != 0 && s->shop.auth_challenge[0];
    bool info = s->shop.info_present;
    const char *vs = info && s->shop.voice_sub[0] ? s->shop.voice_sub : "Say 4 digits";
    const char *fs = info && s->shop.face_sub[0] ? s->shop.face_sub : "Look at the camera";
    const char *cs = info && s->shop.code_sub[0] ? s->shop.code_sub : "Sent to your phone";
    method_tile(methods, "voice", DOS_IC_AUDIO_LINES, "Voice ID", vs, s->shop.method_voice, voice_active, tw);
    method_tile(methods, "face", DOS_IC_SCAN_FACE, "Face ID", fs, s->shop.method_face && s->shop.face_available, st == UI_AUTH_FACE, tw);
    method_tile(methods, "code", DOS_IC_SMARTPHONE, "Code", cs, s->shop.method_code, st == UI_AUTH_OTP, tw);

    lv_obj_t *pane = lv_obj_create(s_co_box);
    lv_obj_remove_style_all(pane);
    lv_obj_set_scrollable(pane, false);
    lv_obj_set_pos(pane, rx, 22 + 96 + 16);
    lv_obj_set_size(pane, rw, bh - 44 - 96 - 16 - 52);
    fill_stage(pane, s);

    lv_obj_t *cancel = dos_btn(s_co_box, DOS_BTN_GHOST, false, -1, "Cancel");
    lv_obj_align(cancel, LV_ALIGN_BOTTOM_RIGHT, -24, -22);
    if(st == UI_AUTH_VERIFYING || st == UI_AUTH_ACCEPTED) lv_obj_set_style_opa(cancel, 97, 0);
    else lv_obj_add_event_cb(cancel, cmd_cancel, LV_EVENT_CLICKED, NULL);
}

/* ---------- shell ---------- */

void sheets_build(lv_obj_t *stage)
{
    s_item_layer = lv_obj_create(stage);
    lv_obj_remove_style_all(s_item_layer);
    lv_obj_set_ignore_layout(s_item_layer, true);
    lv_obj_set_size(s_item_layer, lv_pct(100), lv_pct(100));
    lv_obj_set_scrollable(s_item_layer, false);
    lv_obj_t *sc = scrim(s_item_layer, 153);
    lv_obj_add_event_cb(sc, cmd_back, LV_EVENT_CLICKED, NULL);
    s_item_box = panel_box(s_item_layer, 560, 640, 30);
    lv_obj_set_style_max_height(s_item_box, lv_pct(88), 0);
    lv_obj_set_hidden(s_item_layer, true);

    s_co_layer = lv_obj_create(stage);
    lv_obj_remove_style_all(s_co_layer);
    lv_obj_set_ignore_layout(s_co_layer, true);
    lv_obj_set_size(s_co_layer, lv_pct(100), lv_pct(100));
    lv_obj_set_scrollable(s_co_layer, false);
    scrim(s_co_layer, 158);
    preview_on_frame(pv_frame, NULL);
    s_co_box = panel_box(s_co_layer, 880, 560, -20);
    lv_obj_set_style_max_height(s_co_box, lv_pct(92), 0);
    lv_obj_set_hidden(s_co_layer, true);
}

void sheets_update(const ui_state_t *s)
{
    s_cur = s;
    uint32_t h = ui_shop_hash();

    bool item = s->shop.item.open && !s->shop.checkout_pending;
    dos_show(s_item_layer, item);
    if(item) {
        if(strcmp(s_item_id, s->shop.item.id)) {
            snprintf(s_item_id, sizeof s_item_id, "%s", s->shop.item.id);
            s_qty = s->shop.item.qty > 0 ? s->shop.item.qty : 1;
            s_item_valid = false;
        }
        if(!s_item_valid || s_item_built != h || s_item_built_gen != s_item_gen) {
            lv_obj_update_layout(s_item_layer);
            fill_item(s);
            s_item_built = h; s_item_built_gen = s_item_gen; s_item_valid = true;
        }
    }
    else s_item_id[0] = 0;

    bool co = s->shop.checkout_pending;
    dos_show(s_co_layer, co);
    if(co) {
        if(s->shop.auth_stage != UI_AUTH_OTP) { s_otp_n = 0; s_otp[0] = 0; s_otp_sent = false; }
        else if(s_otp_sent && s_co_built != h) { s_otp_n = 0; s_otp[0] = 0; s_otp_sent = false; s_co_valid = false; }
        if(!s_co_valid || s_co_built != h || s_co_built_gen != s_co_gen) {
            lv_obj_update_layout(s_co_layer);
            fill_checkout(s);
            s_co_built = h; s_co_built_gen = s_co_gen; s_co_valid = true;
        }
        if(s_ring) {
            int32_t d = 100 + (int32_t)(s->voice.level * 44);
            lv_obj_set_size(s_ring, d, d);
            lv_obj_center(s_ring);
        }
    }
    else { s_co_valid = false; s_ring = NULL; s_pv_img = s_pv_icon = NULL; }
    if(!co || !strcmp(s->shop.face_state, "none")) preview_clear();
}
