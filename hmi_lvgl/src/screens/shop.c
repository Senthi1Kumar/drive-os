#include "screens.h"
#include "thumbs.h"
#include "ws_client.h"
#include <stdio.h>
#include <string.h>

enum { V_PLACES, V_MENU, V_CART, V_ORDER, V_COUNT };

static lv_obj_t *s_col[V_COUNT], *s_cartbar;
static uint32_t s_built[V_COUNT];
static bool s_valid[V_COUNT];
static int s_section;
static char s_section_place[64];
static int s_last_view = -1;
static uint32_t s_gen;   /* bumped by local changes so the visible view rebuilds */
static uint32_t s_built_gen[V_COUNT];

static void cmd_open_place(lv_event_t *e) { link_cmd_str("open_place", "id", lv_event_get_user_data(e)); }
static void cmd_drive_to(lv_event_t *e) { link_cmd_str("drive_to", "place_id", lv_event_get_user_data(e)); }
static void cmd_open_item(lv_event_t *e) { link_cmd_str("open_item", "id", lv_event_get_user_data(e)); }
static void cmd_find(lv_event_t *e) { link_cmd_str("find", "category", lv_event_get_user_data(e)); }
static void cmd_back(lv_event_t *e) { (void)e; link_cmd("back", NULL); }
static void cmd_checkout(lv_event_t *e) { (void)e; link_cmd("checkout", NULL); }
static void cmd_quick_add(lv_event_t *e) { link_cmd_str_int("add_item", "id", lv_event_get_user_data(e), "qty", 1); }
static void cmd_remove_all(lv_event_t *e)
{
    (void)e;
    const ui_state_t *s = ui_state();
    for(int i = 0; i < s->shop.cart_lines; i++) link_cmd_str("remove_line", "id", s->shop.lines[i].id);
}

static void cmd_section(lv_event_t *e)
{
    s_section = (int)(intptr_t)lv_event_get_user_data(e);
    s_gen++;
    app_refresh_async();
}

static void line_qty(int v, void *ud)
{
    const char *id = ud;   /* points into the state mirror's line, stable until the next snapshot rebuilds us */
    if(v <= 0) link_cmd_str("remove_line", "id", id);
    else link_cmd_str_int("set_line_qty", "id", id, "qty", v);
}

/* ---------- places ---------- */

static void place_card(lv_obj_t *grid, const ui_place_t *p, int idx, int32_t w)
{
    lv_obj_t *c = dos_card(grid);
    lv_obj_set_size(c, w, 112);
    lv_obj_set_style_pad_all(c, 0, 0);
    lv_obj_set_clickable(c, true);
    lv_obj_set_style_bg_color(c, DOS_PANEL, LV_STATE_PRESSED);
    if(!p->orderable) lv_obj_set_style_opa(c, 210, 0);
    lv_obj_add_event_cb(c, p->orderable ? cmd_open_place : cmd_drive_to, LV_EVENT_CLICKED, (void *)p->id);

    lv_obj_t *th = dos_thumb_img(c, 96, 96, dos_cat_icon(p->category), 24, 12, p->img, p->img_lg);
    lv_obj_set_pos(th, 8, 8);
    lv_obj_set_clickable(th, false);
    lv_obj_t *pin = dos_box(c, LV_FLEX_FLOW_ROW);
    lv_obj_set_size(pin, 24, 24);
    lv_obj_set_pos(pin, 14, 14);
    lv_obj_set_style_radius(pin, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(pin, DOS_ACCENT, 0);
    lv_obj_set_style_bg_opa(pin, LV_OPA_COVER, 0);
    lv_obj_set_flex_align(pin, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    char letter[2] = { (char)('A' + (idx % 26)), 0 };
    dos_text(pin, DOS_F13, DOS_ACCENT_INK, letter);

    lv_obj_t *tc = dos_box(c, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_width(tc, w - 116 - 56);
    lv_obj_set_style_pad_row(tc, 5, 0);
    lv_obj_align(tc, LV_ALIGN_LEFT_MID, 116, 0);
    lv_obj_t *n = dos_text(tc, DOS_F15, DOS_INK, p->name);
    lv_obj_set_width(n, lv_pct(100));
    lv_label_set_long_mode(n, LV_LABEL_LONG_MODE_DOTS);
    if(p->address[0]) {
        lv_obj_t *a = dos_text(tc, DOS_F13, DOS_DIM, p->address);
        lv_obj_set_width(a, lv_pct(100));
        lv_label_set_long_mode(a, LV_LABEL_LONG_MODE_DOTS);
    }
    lv_obj_t *row = dos_box(tc, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(row, 8, 0);
    lv_obj_set_flex_align(row, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    dos_chip(row, DOS_CHIP_ACCENT, DOS_IC_MAP_PIN, p->distance_text[0] ? p->distance_text : "Nearby", true);
    char b[48] = "";
    if(!p->orderable) snprintf(b, sizeof b, "Directions only");
    else if(p->eta_min > 0 && !p->sample) snprintf(b, sizeof b, "%d min away", p->eta_min);
    dos_text(row, DOS_F13, p->orderable ? DOS_DIM : DOS_FAINT, b);
    if(p->sample) dos_chip(row, DOS_CHIP_DEFAULT, -1, "Sample Only", true);

    lv_obj_t *go = dos_box(c, LV_FLEX_FLOW_ROW);
    lv_obj_set_size(go, 36, 36);
    lv_obj_align(go, LV_ALIGN_RIGHT_MID, -10, 0);
    lv_obj_set_style_radius(go, 11, 0);
    lv_obj_set_style_bg_color(go, p->orderable ? DOS_WHITE : DOS_LIVE, 0);
    lv_obj_set_style_bg_opa(go, p->orderable ? DOS_OPA_FILL : DOS_OPA_SOFT, 0);
    lv_obj_set_style_border_width(go, 1, 0);
    lv_obj_set_style_border_color(go, p->orderable ? DOS_WHITE : DOS_LIVE, 0);
    lv_obj_set_style_border_opa(go, p->orderable ? DOS_OPA_LINE : DOS_OPA_LINE_C, 0);
    lv_obj_set_flex_align(go, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_clickable(go, false);
    dos_icon(go, p->orderable ? DOS_IC_CHEVRON_RIGHT : DOS_IC_NAVIGATION, 16, p->orderable ? DOS_INK : DOS_LIVE);
}

static void fill_places(lv_obj_t *col, const ui_state_t *s)
{
    lv_obj_t *act;
    char psub[200];
    snprintf(psub, sizeof psub, "%s%s%s", s->shop.status[0] ? s->shop.status : "Ask for coffee, food, gas, a charger or a store",
             s->vehicle.source_text[0] ? " · " : "", s->vehicle.source_text);
    dos_head_row(col, "Shop", "Nearby", psub, &act);
    if(s->shop.cart_count > 0) {
        char b[32];
        snprintf(b, sizeof b, "Cart %d", s->shop.cart_count);
        lv_obj_t *cb = dos_btn(act, DOS_BTN_PRIMARY, true, -1, b);
        lv_obj_add_event_cb(cb, cmd_checkout, LV_EVENT_CLICKED, NULL);
    }
    static const struct { const char *cat, *label; dos_icon_t ic; } K[] = {
        { "coffee", "Coffee", DOS_IC_COFFEE }, { "sandwich", "Sandwiches", DOS_IC_SANDWICH }, { "food", "Food", DOS_IC_UTENSILS },
        { "bakery", "Bakery", DOS_IC_CROISSANT }, { "bar", "Bars", DOS_IC_WINE }, { "gas", "Gas", DOS_IC_FUEL },
        { "ev", "Charging", DOS_IC_ZAP }, { "store", "Store", DOS_IC_SHOPPING_BASKET }, { "pharmacy", "Pharmacy", DOS_IC_PILL } };
    lv_obj_t *flow = dos_box(col, LV_FLEX_FLOW_ROW_WRAP);
    lv_obj_set_width(flow, lv_pct(100));
    lv_obj_set_style_pad_column(flow, 8, 0);
    lv_obj_set_style_pad_row(flow, 8, 0);
    for(size_t i = 0; i < sizeof K / sizeof K[0]; i++) {
        lv_obj_t *c = dos_chip(flow, DOS_CHIP_DEFAULT, K[i].ic, K[i].label, false);
        lv_obj_set_clickable(c, true);
        lv_obj_set_style_opa(c, LV_OPA_70, LV_STATE_PRESSED);
        lv_obj_add_event_cb(c, cmd_find, LV_EVENT_CLICKED, (void *)K[i].cat);
    }
    if(s->shop.n_places == 0) {
        lv_obj_t *card = dos_card(col);
        lv_obj_set_size(card, lv_pct(100), 160);
        lv_obj_set_flex_flow(card, LV_FLEX_FLOW_COLUMN);
        lv_obj_set_flex_align(card, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
        lv_obj_set_style_pad_row(card, 6, 0);
        dos_text(card, DOS_F17, DOS_INK, !strcmp(s->shop.view, "places") ? "Nothing open nearby" : "What do you need?");
        dos_text(card, DOS_F13, DOS_DIM, "Say “find me a coffee”, or pick a category above.");
        return;
    }
    int32_t cw = dos_content_w();
    int cols = cw > 1000 ? 3 : 2;
    int32_t w = (cw - (cols - 1) * DOS_GAP) / cols;
    lv_obj_t *grid = dos_box(col, LV_FLEX_FLOW_ROW_WRAP);
    lv_obj_set_width(grid, lv_pct(100));
    lv_obj_set_style_pad_column(grid, DOS_GAP, 0);
    lv_obj_set_style_pad_row(grid, DOS_GAP, 0);
    for(int i = 0; i < s->shop.n_places; i++) place_card(grid, &s->shop.places[i], i, w);
}

/* ---------- menu ---------- */

static void menu_card(lv_obj_t *grid, const ui_menu_item_t *it, int32_t w)
{
    int32_t h = 306;
    lv_obj_t *c = dos_card(grid);
    lv_obj_set_size(c, w, h);
    lv_obj_set_style_pad_all(c, 0, 0);
    lv_obj_set_style_clip_corner(c, true, 0);
    lv_obj_set_clickable(c, true);
    lv_obj_set_style_bg_color(c, DOS_PANEL, LV_STATE_PRESSED);
    lv_obj_add_event_cb(c, cmd_open_item, LV_EVENT_CLICKED, (void *)it->id);
    lv_obj_t *th = dos_thumb_img(c, w - 2, h * 52 / 100, DOS_IC_UTENSILS, 30, 0, it->img, it->img_lg);
    lv_obj_set_pos(th, 0, 0);
    lv_obj_set_clickable(th, false);
    lv_obj_t *tc = dos_box(c, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_width(tc, w - 26);
    lv_obj_set_pos(tc, 12, h * 52 / 100 + 10);
    lv_obj_set_style_pad_row(tc, 4, 0);
    lv_obj_t *n = dos_text(tc, DOS_F15, DOS_INK, it->name);
    lv_obj_set_width(n, lv_pct(100));
    lv_label_set_long_mode(n, LV_LABEL_LONG_MODE_DOTS);
    lv_obj_set_style_max_height(n, 40, 0);
    if(it->desc[0]) {
        lv_obj_t *d = dos_text(tc, DOS_F13, DOS_DIM, it->desc);
        lv_obj_set_width(d, lv_pct(100));
        lv_label_set_long_mode(d, LV_LABEL_LONG_MODE_DOTS);
        lv_obj_set_style_max_height(d, 34, 0);
    }
    lv_obj_t *pr = dos_text(c, DOS_F17, DOS_INK, it->price_text);
    lv_obj_align(pr, LV_ALIGN_BOTTOM_LEFT, 12, -16);
    lv_obj_t *add = dos_box(c, LV_FLEX_FLOW_ROW);
    lv_obj_set_size(add, 46, 46);
    lv_obj_align(add, LV_ALIGN_BOTTOM_RIGHT, -12, -12);
    lv_obj_set_style_radius(add, 13, 0);
    lv_obj_set_style_bg_color(add, it->addable ? DOS_ACCENT : DOS_WHITE, 0);
    lv_obj_set_style_bg_opa(add, it->addable ? DOS_OPA_SOFT : DOS_OPA_FILL, 0);
    lv_obj_set_style_border_width(add, 1, 0);
    lv_obj_set_style_border_color(add, it->addable ? DOS_ACCENT : DOS_WHITE, 0);
    lv_obj_set_style_border_opa(add, it->addable ? DOS_OPA_LINE_C : DOS_OPA_LINE, 0);
    lv_obj_set_flex_align(add, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    dos_icon(add, DOS_IC_PLUS, 20, it->addable ? DOS_ACCENT : DOS_FAINT);
    if(it->addable) {
        lv_obj_set_clickable(add, true);
        lv_obj_add_event_cb(add, cmd_quick_add, LV_EVENT_CLICKED, (void *)it->id);
    }
}

static void fill_menu(lv_obj_t *col, const ui_state_t *s)
{
    const ui_place_t *pl = NULL;
    for(int i = 0; i < s->shop.n_places; i++)
        if(!strcmp(s->shop.places[i].id, s->shop.place_id)) pl = &s->shop.places[i];
    if(strcmp(s_section_place, s->shop.place_id)) {
        snprintf(s_section_place, sizeof s_section_place, "%s", s->shop.place_id);
        s_section = 0;
    }
    if(s_section >= s->shop.n_sections) s_section = 0;
    char sub[200] = "";
    if(pl) snprintf(sub, sizeof sub, "%s%s%s", pl->address, pl->address[0] && pl->distance_text[0] ? " · " : "", pl->distance_text);
    lv_obj_t *act;
    dos_head_row(col, "Menu", s->shop.place_name, sub, &act);
    if(pl && s->shop.place_id[0]) {
        lv_obj_t *d = dos_btn(act, DOS_BTN_DEFAULT, true, DOS_IC_NAVIGATION, "Drive");
        lv_obj_add_event_cb(d, cmd_drive_to, LV_EVENT_CLICKED, (void *)s->shop.place_id);
    }
    lv_obj_t *bk = dos_btn(act, DOS_BTN_DEFAULT, true, DOS_IC_ARROW_LEFT, NULL);
    lv_obj_add_event_cb(bk, cmd_back, LV_EVENT_CLICKED, NULL);

    if(s->shop.sample) {
        lv_obj_t *note = dos_chip(col, DOS_CHIP_ACCENT, DOS_IC_INFO, "Sample Only", false);
        (void)note;
    }
    lv_obj_t *flow = dos_box(col, LV_FLEX_FLOW_ROW_WRAP);
    lv_obj_set_width(flow, lv_pct(100));
    lv_obj_set_style_pad_column(flow, 8, 0);
    lv_obj_set_style_pad_row(flow, 8, 0);
    for(int i = 0; i < s->shop.n_sections; i++) {
        lv_obj_t *c = dos_chip(flow, i == s_section ? DOS_CHIP_LIVE : DOS_CHIP_DEFAULT, -1, s->shop.sections[i], false);
        lv_obj_set_clickable(c, true);
        lv_obj_add_event_cb(c, cmd_section, LV_EVENT_CLICKED, (void *)(intptr_t)i);
    }
    int32_t cw = dos_content_w();
    int cols = cw > 1000 ? 4 : 3;
    int32_t w = (cw - (cols - 1) * DOS_GAP) / cols;
    lv_obj_t *grid = dos_box(col, LV_FLEX_FLOW_ROW_WRAP);
    lv_obj_set_width(grid, lv_pct(100));
    lv_obj_set_style_pad_column(grid, DOS_GAP, 0);
    lv_obj_set_style_pad_row(grid, DOS_GAP, 0);
    for(int i = 0; i < s->shop.n_items; i++)
        if(s->shop.items[i].section == s_section) menu_card(grid, &s->shop.items[i], w);
}

/* ---------- cart ---------- */

static void fill_cart(lv_obj_t *col, const ui_state_t *s)
{
    char sub[160];
    snprintf(sub, sizeof sub, "%d item%s%s%s", s->shop.cart_count, s->shop.cart_count == 1 ? "" : "s",
             s->shop.cart_place[0] ? " from " : "", s->shop.cart_place);
    lv_obj_t *act;
    dos_head_row(col, "Order", "Cart", sub, &act);
    lv_obj_t *bk = dos_btn(act, DOS_BTN_DEFAULT, true, DOS_IC_ARROW_LEFT, NULL);
    lv_obj_add_event_cb(bk, cmd_back, LV_EVENT_CLICKED, NULL);

    if(s->shop.cart_lines == 0) {
        lv_obj_t *card = dos_card(col);
        lv_obj_set_size(card, lv_pct(100), 170);
        lv_obj_set_flex_flow(card, LV_FLEX_FLOW_COLUMN);
        lv_obj_set_flex_align(card, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
        lv_obj_set_style_pad_row(card, 10, 0);
        dos_text(card, DOS_F17, DOS_INK, "Cart is empty");
        dos_text(card, DOS_F13, DOS_DIM, "Pick a place, open its menu, and add something.");
        lv_obj_t *b = dos_btn(card, DOS_BTN_PRIMARY, false, DOS_IC_STORE, "Browse places");
        lv_obj_add_event_cb(b, cmd_find, LV_EVENT_CLICKED, (void *)"food");
        return;
    }
    lv_obj_t *row = dos_box(col, LV_FLEX_FLOW_ROW);
    lv_obj_set_width(row, lv_pct(100));
    lv_obj_set_style_pad_column(row, 12, 0);
    lv_obj_set_flex_align(row, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_START);
    lv_obj_t *lines = dos_box(row, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_grow(lines, 1);
    lv_obj_set_style_pad_row(lines, 10, 0);
    for(int i = 0; i < s->shop.cart_lines; i++) {
        const ui_line_t *l = &s->shop.lines[i];
        lv_obj_t *c = dos_card(lines);
        lv_obj_set_size(c, lv_pct(100), 76);
        lv_obj_set_style_pad_all(c, 0, 0);
        lv_obj_t *th = dos_thumb_img(c, 56, 56, DOS_IC_UTENSILS, 20, 12, l->img, l->img);
        lv_obj_align(th, LV_ALIGN_LEFT_MID, 10, 0);
        lv_obj_t *tc = dos_box(c, LV_FLEX_FLOW_COLUMN);
        lv_obj_set_style_pad_row(tc, 5, 0);
        lv_obj_set_width(tc, lv_pct(100));
        lv_obj_set_style_pad_left(tc, 78, 0);
        lv_obj_set_style_pad_right(tc, 140, 0);
        lv_obj_align(tc, LV_ALIGN_LEFT_MID, 0, 0);
        lv_obj_t *n = dos_text(tc, DOS_F15, DOS_INK, l->name);
        lv_obj_set_width(n, lv_pct(100));
        lv_label_set_long_mode(n, LV_LABEL_LONG_MODE_DOTS);
        lv_obj_t *r2 = dos_box(tc, LV_FLEX_FLOW_ROW);
        lv_obj_set_style_pad_column(r2, 10, 0);
        lv_obj_t *sub = dos_text(r2, DOS_F13, DOS_DIM, l->opts[0] ? l->opts : s->shop.cart_place);   /* "Large · Iced · Oat" */
        lv_obj_set_style_max_width(sub, 330, 0);
        lv_label_set_long_mode(sub, LV_LABEL_LONG_MODE_DOTS);
        dos_text(r2, DOS_F15, DOS_INK, l->price_text);
        lv_obj_t *q = dos_qty(c, l->qty, 0, 20, line_qty, (void *)l->id);
        lv_obj_align(q, LV_ALIGN_RIGHT_MID, -12, 0);
    }
    lv_obj_t *sum = dos_card(row);
    lv_obj_set_size(sum, 300, 210);
    lv_obj_set_style_pad_all(sum, 0, 0);
    lv_obj_t *l1 = dos_section_label(sum, "Subtotal");
    lv_obj_align(l1, LV_ALIGN_TOP_LEFT, 16, 16);
    char b[32];
    dos_fmt_money(b, sizeof b, s->shop.subtotal);
    lv_obj_t *big = dos_text(sum, DOS_F30, DOS_INK, b);
    lv_obj_set_style_text_letter_space(big, -1, 0);
    lv_obj_align(big, LV_ALIGN_TOP_LEFT, 16, 38);
    char note[64];
    if(s->shop.tax > 0) {
        char t[16], tot[16];
        dos_fmt_money(t, sizeof t, s->shop.tax);
        dos_fmt_money(tot, sizeof tot, s->shop.total);
        snprintf(note, sizeof note, "Tax %s · Total %s", t, tot);
    }
    else snprintf(note, sizeof note, "Tax at checkout · pickup");
    lv_obj_t *nt = dos_text(sum, DOS_F13, DOS_DIM, note);
    lv_obj_align(nt, LV_ALIGN_TOP_LEFT, 16, 84);
    lv_obj_t *clr = dos_btn(sum, DOS_BTN_DANGER, false, DOS_IC_TRASH_2, "Clear");
    lv_obj_align(clr, LV_ALIGN_BOTTOM_LEFT, 16, -16);
    lv_obj_set_width(clr, 104);
    lv_obj_add_event_cb(clr, cmd_remove_all, LV_EVENT_CLICKED, NULL);
    lv_obj_t *po = dos_btn(sum, DOS_BTN_PRIMARY, false, DOS_IC_CHECK, "Place order");
    lv_obj_align(po, LV_ALIGN_BOTTOM_RIGHT, -16, -16);
    lv_obj_set_width(po, 148);
    lv_obj_add_event_cb(po, cmd_checkout, LV_EVENT_CLICKED, NULL);
}

/* ---------- shell ---------- */

void shop_build(lv_obj_t *slot)
{
    for(int i = 0; i < V_COUNT; i++) {
        s_col[i] = screen_column(slot, 18);
        lv_obj_set_hidden(s_col[i], true);
    }
    s_cartbar = dos_btn(slot, DOS_BTN_PRIMARY, false, DOS_IC_SHOPPING_BASKET, "Checkout");
    lv_obj_set_ignore_layout(s_cartbar, true);
    lv_obj_align(s_cartbar, LV_ALIGN_BOTTOM_MID, 0, -186);
    lv_obj_add_event_cb(s_cartbar, cmd_checkout, LV_EVENT_CLICKED, NULL);
    lv_obj_set_hidden(s_cartbar, true);
}

void shop_update(const ui_state_t *s)
{
    const char *v = s->shop.view;
    int which = V_PLACES;
    if(!strcmp(v, "menu") || !strcmp(v, "item")) which = s->shop.n_sections ? V_MENU : V_PLACES;
    else if(!strcmp(v, "cart") || !strcmp(v, "checkout")) which = V_CART;
    else if(!strcmp(v, "order") && s->shop.order.present) which = V_ORDER;
    for(int i = 0; i < V_COUNT; i++) dos_show(s_col[i], i == which);

    uint32_t h = ui_shop_hash();
    if(!s_valid[which] || s_built[which] != h || s_built_gen[which] != s_gen) {
        lv_obj_t *col = s_col[which];
        int32_t y = s_valid[which] && which == s_last_view ? lv_obj_get_scroll_y(col) : 0;
        lv_obj_clean(col);
        lv_obj_set_hidden(col, false);
        lv_obj_update_layout(col);
        switch(which) {
            case V_PLACES: fill_places(col, s); break;
            case V_MENU: fill_menu(col, s); break;
            case V_CART: fill_cart(col, s); break;
            default: order_fill(col, s); break;
        }
        lv_obj_update_layout(col);
        lv_obj_scroll_to_y(col, y, LV_ANIM_OFF);
        s_built[which] = h;
        s_built_gen[which] = s_gen;
        s_valid[which] = true;
    }
    s_last_view = which;

    bool bar = which == V_MENU && s->shop.cart_count > 0;
    dos_show(s_cartbar, bar);
    if(bar) {
        char b[64];
        dos_fmt_money(b, sizeof b, s->shop.subtotal);
        char t[96];
        snprintf(t, sizeof t, "Checkout · %d · %s", s->shop.cart_count, b);
        lv_obj_t *lb = lv_obj_get_child(s_cartbar, lv_obj_get_child_count(s_cartbar) - 1);
        dos_label_set(lb, t);
    }
}
