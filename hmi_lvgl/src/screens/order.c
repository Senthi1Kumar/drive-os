#include "screens.h"
#include "ws_client.h"
#include <stdio.h>
#include <string.h>

static void cmd_back(lv_event_t *e) { (void)e; link_cmd("back", NULL); }
static void cmd_here(lv_event_t *e) { (void)e; link_cmd("im_here", NULL); }
static void cmd_drive(lv_event_t *e) { link_cmd_str("drive_to", "place_id", lv_event_get_user_data(e)); }

void order_fill(lv_obj_t *col, const ui_state_t *s)
{
    const typeof(s->shop.order) *o = &s->shop.order;
    char label[96];
    if(o->terminal) snprintf(label, sizeof label, "Order");
    else snprintf(label, sizeof label, "Order · %s", o->status_text);
    lv_obj_t *act;
    dos_head_row(col, label, o->merchant, o->terminal ? o->status_text : "", &act);
    lv_obj_t *bk = dos_btn(act, DOS_BTN_DEFAULT, true, DOS_IC_ARROW_LEFT, NULL);
    lv_obj_add_event_cb(bk, cmd_back, LV_EVENT_CLICKED, NULL);

    lv_obj_t *row = dos_box(col, LV_FLEX_FLOW_ROW);
    lv_obj_set_width(row, lv_pct(100));
    lv_obj_set_style_pad_column(row, 12, 0);
    lv_obj_set_flex_align(row, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_START);

    lv_obj_t *card = dos_card(row);
    lv_obj_set_flex_grow(card, 1);
    lv_obj_set_height(card, 392);
    lv_obj_set_style_pad_all(card, 0, 0);
    lv_obj_t *l1 = dos_section_label(card, "Pickup code");
    lv_obj_align(l1, LV_ALIGN_TOP_LEFT, 20, 18);
    lv_obj_t *code = dos_text(card, DOS_F64, o->terminal ? DOS_DIM : DOS_INK, o->pickup_code[0] ? o->pickup_code : "—");
    lv_obj_set_style_text_letter_space(code, -2, 0);
    lv_obj_align(code, LV_ALIGN_TOP_LEFT, 20, 38);

    bool ready = !strcmp(o->status, "ready");
    lv_obj_t *l2 = dos_section_label(card, ready ? "Status" : "Ready in");
    lv_obj_align(l2, LV_ALIGN_TOP_RIGHT, -20, 18);
    char eta[32] = "—";
    if(o->terminal) snprintf(eta, sizeof eta, "—");
    else if(ready) snprintf(eta, sizeof eta, "Ready");
    else if(o->eta_min >= 0) snprintf(eta, sizeof eta, o->eta_min < 1 ? "<1 min" : "%d min", (int)(o->eta_min + 0.5));
    lv_obj_t *e = dos_text(card, DOS_F30, ready ? DOS_LIVE : DOS_ACCENT, eta);
    lv_obj_align(e, LV_ALIGN_TOP_RIGHT, -20, 38);

    for(int i = 0; i < o->n_steps; i++) {
        const ui_step_t *st = &o->steps[i];
        bool done = !strcmp(st->phase, "done"), now = !strcmp(st->phase, "now");
        int32_t y = 150 + i * 46;
        if(i < o->n_steps - 1) {
            lv_obj_t *ln = lv_obj_create(card);
            lv_obj_remove_style_all(ln);
            lv_obj_set_size(ln, 2, 30);
            lv_obj_set_pos(ln, 30, y + 26);
            lv_obj_set_style_bg_color(ln, done ? DOS_LIVE : DOS_WHITE, 0);
            lv_obj_set_style_bg_opa(ln, done ? LV_OPA_COVER : DOS_OPA_LINE, 0);
        }
        lv_obj_t *d = dos_box(card, LV_FLEX_FLOW_ROW);
        lv_obj_set_size(d, 22, 22);
        lv_obj_set_pos(d, 20, y + 4);
        lv_obj_set_style_radius(d, LV_RADIUS_CIRCLE, 0);
        lv_obj_set_flex_align(d, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
        if(done) {
            lv_obj_set_style_bg_color(d, DOS_LIVE, 0);
            lv_obj_set_style_bg_opa(d, LV_OPA_COVER, 0);
            dos_icon(d, DOS_IC_CHECK, 12, DOS_LIVE_INK);
        }
        else {
            lv_obj_set_style_border_width(d, 2, 0);
            lv_obj_set_style_border_color(d, now ? DOS_ACCENT : DOS_FAINT, 0);
            if(now) {
                lv_obj_set_style_bg_color(d, DOS_ACCENT, 0);
                lv_obj_set_style_bg_opa(d, DOS_OPA_SOFT, 0);
                lv_obj_t *dot = lv_obj_create(d);
                lv_obj_remove_style_all(dot);
                lv_obj_set_size(dot, 8, 8);
                lv_obj_set_style_radius(dot, LV_RADIUS_CIRCLE, 0);
                lv_obj_set_style_bg_color(dot, DOS_ACCENT, 0);
                lv_obj_set_style_bg_opa(dot, LV_OPA_COVER, 0);
                lv_obj_set_ignore_layout(dot, true);
                lv_obj_center(dot);
            }
        }
        lv_obj_t *t = dos_text(card, DOS_F15, !strcmp(st->phase, "next") ? DOS_DIM : DOS_INK, st->label);
        lv_obj_set_pos(t, 56, y + 3);
        lv_obj_t *w = dos_text(card, DOS_F13, DOS_DIM, st->at);
        lv_obj_align(w, LV_ALIGN_TOP_RIGHT, -20, y + 5);
    }

    lv_obj_t *right = dos_box(row, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_width(right, 360);
    lv_obj_set_style_pad_row(right, 12, 0);
    lv_obj_t *pc = dos_card(right);
    lv_obj_set_size(pc, lv_pct(100), 168);
    lv_obj_set_style_pad_all(pc, 0, 0);
    lv_obj_t *pl = dos_section_label(pc, o->terminal ? "Done" : "Pickup");
    lv_obj_align(pl, LV_ALIGN_TOP_LEFT, 16, 16);
    lv_obj_t *pt = dos_text(pc, DOS_F17, DOS_INK, o->terminal ? o->status_text : "Show this code at the counter");
    lv_obj_set_width(pt, 328);
    lv_obj_align(pt, LV_ALIGN_TOP_LEFT, 16, 38);
    if(!o->terminal && s->shop.place_id[0]) {
        lv_obj_t *b = dos_btn(pc, DOS_BTN_LIVE, false, DOS_IC_NAVIGATION, "Drive there");
        lv_obj_set_width(b, 328);
        lv_obj_align(b, LV_ALIGN_BOTTOM_MID, 0, -14);
        lv_obj_add_event_cb(b, cmd_drive, LV_EVENT_CLICKED, (void *)s->shop.place_id);
    }
    if(!o->terminal) {
        lv_obj_t *h = dos_btn(right, DOS_BTN_DEFAULT, false, DOS_IC_MAP_PIN, "I’m here");
        lv_obj_set_width(h, 360);
        lv_obj_add_event_cb(h, cmd_here, LV_EVENT_CLICKED, NULL);
    }
    lv_obj_t *items = dos_card(right);
    lv_obj_set_size(items, lv_pct(100), LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(items, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(items, 6, 0);
    dos_section_label(items, "Items");
    for(int i = 0; i < s->shop.cart_lines; i++) {
        char b[140];
        snprintf(b, sizeof b, "%d × %s", s->shop.lines[i].qty, s->shop.lines[i].name);
        lv_obj_t *t = dos_text(items, DOS_F13, DOS_INK_SOFT, b);
        lv_obj_set_width(t, lv_pct(100));
        lv_label_set_long_mode(t, LV_LABEL_LONG_MODE_DOTS);
    }
    if(s->shop.cart_lines == 0) dos_text(items, DOS_F13, DOS_DIM, o->merchant);
}
