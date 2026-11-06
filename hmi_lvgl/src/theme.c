#include "theme.h"
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

LV_FONT_DECLARE(dos_manrope_11);
LV_FONT_DECLARE(dos_manrope_13);
LV_FONT_DECLARE(dos_manrope_15);
LV_FONT_DECLARE(dos_manrope_17);
LV_FONT_DECLARE(dos_manrope_30);
LV_FONT_DECLARE(dos_manrope_46);
LV_FONT_DECLARE(dos_manrope_64);

const lv_font_t *dos_font(dos_font_t f)
{
    switch(f) {
        case DOS_F11: return &dos_manrope_11;
        case DOS_F13: return &dos_manrope_13;
        case DOS_F15: return &dos_manrope_15;
        case DOS_F17: return &dos_manrope_17;
        case DOS_F30: return &dos_manrope_30;
        case DOS_F46: return &dos_manrope_46;
        case DOS_F64: return &dos_manrope_64;
    }
    return &dos_manrope_13;
}

lv_color_t dos_mix(lv_color_t a, lv_color_t b, uint8_t t) { return lv_color_mix(b, a, t); }

lv_obj_t *dos_box(lv_obj_t *parent, lv_flex_flow_t flow)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_set_scrollable(o, false);
    lv_obj_set_flex_flow(o, flow);
    lv_obj_set_size(o, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
    return o;
}

lv_obj_t *dos_card(lv_obj_t *parent)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_set_scrollable(o, false);
    lv_obj_set_style_bg_color(o, DOS_CARD, 0);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(o, DOS_RADIUS, 0);
    lv_obj_set_style_border_width(o, 1, 0);
    lv_obj_set_style_border_color(o, DOS_WHITE, 0);
    lv_obj_set_style_border_opa(o, DOS_OPA_LINE, 0);
    lv_obj_set_style_pad_all(o, DOS_PAD, 0);
    return o;
}

lv_obj_t *dos_glass(lv_obj_t *parent, int32_t radius)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_set_scrollable(o, false);
    lv_obj_set_style_bg_color(o, DOS_GLASS, 0);
    lv_obj_set_style_bg_opa(o, DOS_OPA_GLASS, 0);
    lv_obj_set_style_radius(o, radius, 0);
    lv_obj_set_style_border_width(o, 1, 0);
    lv_obj_set_style_border_color(o, DOS_WHITE, 0);
    lv_obj_set_style_border_opa(o, DOS_OPA_LINE_HI, 0);
    return o;
}

lv_obj_t *dos_text(lv_obj_t *parent, dos_font_t f, lv_color_t c, const char *s)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_obj_set_style_text_font(l, dos_font(f), 0);
    lv_obj_set_style_text_color(l, c, 0);
    lv_label_set_text(l, s ? s : "");
    return l;
}

void dos_text_set_upper(lv_obj_t *label, const char *s)
{
    char buf[128];
    size_t i = 0;
    for(; s && s[i] && i < sizeof buf - 1; i++) buf[i] = (char)toupper((unsigned char)s[i]);
    buf[i] = 0;
    lv_label_set_text(label, buf);
}

lv_obj_t *dos_section_label(lv_obj_t *parent, const char *s)
{
    lv_obj_t *l = dos_text(parent, DOS_F11, DOS_DIM, "");
    dos_text_set_upper(l, s);
    lv_obj_set_style_text_letter_space(l, 1, 0);
    return l;
}

void dos_icon_set(lv_obj_t *img, dos_icon_t id, int px, lv_color_t c)
{
    int p = px;
    const lv_image_dsc_t *src = dos_icon_src(id, &p);
    lv_image_set_src(img, src);
    lv_obj_set_size(img, p, p);
    lv_obj_set_style_image_recolor(img, c, 0);
    lv_obj_set_style_image_recolor_opa(img, LV_OPA_COVER, 0);
}

lv_obj_t *dos_icon(lv_obj_t *parent, dos_icon_t id, int px, lv_color_t c)
{
    lv_obj_t *img = lv_image_create(parent);
    lv_obj_set_clickable(img, false);
    dos_icon_set(img, id, px, c);
    return img;
}

typedef struct { lv_color_t fg, bg, border; lv_opa_t bg_opa, border_opa; } chip_colors_t;

static chip_colors_t chip_colors(dos_chip_kind_t k)
{
    switch(k) {
        case DOS_CHIP_LIVE: return (chip_colors_t){ DOS_LIVE, DOS_LIVE, DOS_LIVE, DOS_OPA_SOFT, DOS_OPA_LINE_C };
        case DOS_CHIP_ACCENT: return (chip_colors_t){ DOS_ACCENT, DOS_ACCENT, DOS_ACCENT, DOS_OPA_SOFT, DOS_OPA_LINE_C };
        case DOS_CHIP_WARN: return (chip_colors_t){ DOS_WARN, DOS_WARN, DOS_WARN, DOS_OPA_SOFT, DOS_OPA_LINE_C };
        case DOS_CHIP_DANGER: return (chip_colors_t){ DOS_DANGER, DOS_DANGER, DOS_DANGER, DOS_OPA_SOFT, 82 };
        case DOS_CHIP_QUIET: return (chip_colors_t){ DOS_DIM, DOS_WHITE, DOS_WHITE, DOS_OPA_FILL, DOS_OPA_LINE };
        default: return (chip_colors_t){ DOS_INK, DOS_WHITE, DOS_WHITE, DOS_OPA_FILL, DOS_OPA_LINE };
    }
}

static void chip_apply(lv_obj_t *chip, dos_chip_kind_t k)
{
    chip_colors_t c = chip_colors(k);
    lv_obj_set_style_bg_color(chip, c.bg, 0);
    lv_obj_set_style_bg_opa(chip, c.bg_opa, 0);
    lv_obj_set_style_border_color(chip, c.border, 0);
    lv_obj_set_style_border_opa(chip, c.border_opa, 0);
    uint32_t n = lv_obj_get_child_count(chip);
    for(uint32_t i = 0; i < n; i++) {
        lv_obj_t *ch = lv_obj_get_child(chip, (int32_t)i);
        lv_obj_set_style_text_color(ch, c.fg, 0);
        lv_obj_set_style_image_recolor(ch, c.fg, 0);
    }
}

lv_obj_t *dos_chip(lv_obj_t *parent, dos_chip_kind_t k, int icon, const char *text, bool tiny)
{
    lv_obj_t *c = dos_box(parent, LV_FLEX_FLOW_ROW);
    lv_obj_set_height(c, tiny ? 22 : 34);
    lv_obj_set_style_radius(c, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_border_width(c, 1, 0);
    lv_obj_set_style_pad_hor(c, tiny ? 8 : 11, 0);
    lv_obj_set_style_pad_column(c, 6, 0);
    lv_obj_set_flex_align(c, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    if(icon >= 0) dos_icon(c, (dos_icon_t)icon, tiny ? 12 : 16, DOS_INK);
    dos_text(c, tiny ? DOS_F11 : DOS_F13, DOS_INK, text);
    chip_apply(c, k);
    return c;
}

void dos_chip_set(lv_obj_t *chip, dos_chip_kind_t k, const char *text)
{
    uint32_t n = lv_obj_get_child_count(chip);
    if(n) lv_label_set_text(lv_obj_get_child(chip, (int32_t)n - 1), text);
    chip_apply(chip, k);
}

lv_obj_t *dos_btn(lv_obj_t *parent, dos_btn_kind_t k, bool small, int icon, const char *text)
{
    bool has_text = text && *text;
    lv_obj_t *b = dos_box(parent, LV_FLEX_FLOW_ROW);
    lv_obj_set_clickable(b, true);
    int h = small ? 40 : DOS_TAP;
    lv_obj_set_height(b, h);
    if(!has_text) lv_obj_set_width(b, h);
    lv_obj_set_style_radius(b, small ? 12 : DOS_RADIUS_SM, 0);
    lv_obj_set_style_pad_hor(b, small ? 13 : 18, 0);
    lv_obj_set_style_pad_column(b, 8, 0);
    lv_obj_set_flex_align(b, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_color_t fg = DOS_INK, bg = DOS_WHITE, bd = DOS_WHITE;
    lv_opa_t bgo = DOS_OPA_FILL, bdo = DOS_OPA_LINE;
    switch(k) {
        case DOS_BTN_PRIMARY: fg = DOS_ACCENT_INK; bg = DOS_ACCENT; bgo = LV_OPA_COVER; bdo = 0; break;
        case DOS_BTN_LIVE: fg = DOS_LIVE_INK; bg = DOS_LIVE; bgo = LV_OPA_COVER; bdo = 0; break;
        case DOS_BTN_TINT_LIVE: fg = DOS_LIVE; bg = DOS_LIVE; bd = DOS_LIVE; bgo = DOS_OPA_SOFT; bdo = DOS_OPA_LINE_C; break;
        case DOS_BTN_DANGER: fg = DOS_DANGER; bg = DOS_DANGER; bd = DOS_DANGER; bgo = DOS_OPA_SOFT; bdo = 82; break;
        case DOS_BTN_GHOST: bgo = 0; bdo = 0; break;
        default: break;
    }
    lv_obj_set_style_bg_color(b, bg, 0);
    lv_obj_set_style_bg_opa(b, bgo, 0);
    lv_obj_set_style_border_width(b, 1, 0);
    lv_obj_set_style_border_color(b, bd, 0);
    lv_obj_set_style_border_opa(b, bdo, 0);
    lv_obj_set_style_opa(b, LV_OPA_80, LV_STATE_PRESSED);
    if(icon >= 0) dos_icon(b, (dos_icon_t)icon, small ? 16 : 16, fg);
    if(has_text) dos_text(b, small ? DOS_F13 : DOS_F15, fg, text);
    return b;
}

lv_obj_t *dos_round_btn(lv_obj_t *parent, int d, int icon, int icon_px)
{
    lv_obj_t *b = dos_glass(parent, LV_RADIUS_CIRCLE);
    lv_obj_set_clickable(b, true);
    lv_obj_set_size(b, d, d);
    lv_obj_set_style_opa(b, LV_OPA_80, LV_STATE_PRESSED);
    lv_obj_t *ic = dos_icon(b, (dos_icon_t)icon, icon_px, DOS_INK);
    lv_obj_center(ic);
    return b;
}

lv_obj_t *dos_screen_head(lv_obj_t *parent, const char *label, const char *title, const char *sub, lv_obj_t **title_out, lv_obj_t **sub_out)
{
    lv_obj_t *c = dos_box(parent, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_width(c, lv_pct(100));
    lv_obj_set_style_pad_row(c, 3, 0);
    dos_section_label(c, label);
    lv_obj_t *t = dos_text(c, DOS_F30, DOS_INK, title);
    lv_obj_set_style_text_letter_space(t, -1, 0);
    lv_obj_set_width(t, lv_pct(100));
    lv_label_set_long_mode(t, LV_LABEL_LONG_MODE_DOTS);
    lv_obj_t *s = dos_text(c, DOS_F13, DOS_DIM, sub);
    lv_obj_set_width(s, lv_pct(100));
    lv_label_set_long_mode(s, LV_LABEL_LONG_MODE_DOTS);
    if(title_out) *title_out = t;
    if(sub_out) *sub_out = s;
    return c;
}

void dos_bg_grad(lv_obj_t *obj, lv_grad_dsc_t *store, bool horizontal, lv_color_t a, lv_color_t b)
{
    lv_color_t cols[2] = { a, b };
    lv_grad_init_stops(store, cols, NULL, NULL, 2);
    if(horizontal) lv_grad_horizontal_init(store);
    else lv_grad_vertical_init(store);
    lv_obj_set_style_bg_grad(obj, store, 0);
}

void dos_label_set(lv_obj_t *label, const char *s)
{
    const char *cur = lv_label_get_text(label);
    if(!cur || strcmp(cur, s ? s : "")) lv_label_set_text(label, s ? s : "");
}

void dos_show(lv_obj_t *obj, bool on)
{
    if(on == !lv_obj_is_hidden(obj)) return;
    if(on) lv_obj_set_hidden(obj, false);
    else lv_obj_set_hidden(obj, true);
}

lv_obj_t *dos_thumb(lv_obj_t *parent, int32_t w, int32_t h, int icon, int icon_px, int32_t radius)
{
    lv_obj_t *t = dos_box(parent, LV_FLEX_FLOW_ROW);
    lv_obj_set_size(t, w, h);
    lv_obj_set_style_radius(t, radius, 0);
    lv_obj_set_style_bg_opa(t, LV_OPA_COVER, 0);
    lv_obj_set_style_clip_corner(t, true, 0);
    static lv_grad_dsc_t g;
    dos_bg_grad(t, &g, false, lv_color_hex(0x1a2029), lv_color_hex(0x10141a));
    lv_obj_set_flex_align(t, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    if(icon >= 0) dos_icon(t, (dos_icon_t)icon, icon_px, DOS_FAINT);
    return t;
}

dos_icon_t dos_cat_icon(const char *c)
{
    if(!strcmp(c, "coffee")) return DOS_IC_COFFEE;
    if(!strcmp(c, "food") || !strcmp(c, "restaurant")) return DOS_IC_UTENSILS;
    if(!strcmp(c, "sandwich")) return DOS_IC_SANDWICH;
    if(!strcmp(c, "bakery")) return DOS_IC_CROISSANT;
    if(!strcmp(c, "gas") || !strcmp(c, "fuel")) return DOS_IC_FUEL;
    if(!strcmp(c, "pharmacy")) return DOS_IC_PILL;
    if(!strcmp(c, "bar") || !strcmp(c, "pub")) return DOS_IC_WINE;
    if(!strcmp(c, "ev")) return DOS_IC_ZAP;
    if(!strcmp(c, "store")) return DOS_IC_STORE;
    return DOS_IC_MAP_PIN;
}

void dos_fmt_money(char *out, size_t cap, double v) { snprintf(out, cap, "$%.2f", v); }

typedef struct { int value, min, max; dos_qty_cb cb; void *ud; lv_obj_t *label, *minus, *plus; } qty_t;

static void qty_click(lv_event_t *e)
{
    qty_t *q = lv_event_get_user_data(e);
    int d = (int)(intptr_t)lv_obj_get_user_data(lv_event_get_target_obj(e));
    int v = q->value + d;
    if(v < q->min || v > q->max) return;
    q->value = v;
    lv_label_set_text_fmt(q->label, "%d", v);
    lv_obj_set_style_opa(q->minus, v <= q->min ? 90 : LV_OPA_COVER, 0);
    lv_obj_set_style_opa(q->plus, v >= q->max ? 90 : LV_OPA_COVER, 0);
    if(q->cb) q->cb(v, q->ud);
}

static void qty_del(lv_event_t *e) { free(lv_event_get_user_data(e)); }

static lv_obj_t *qty_btn(lv_obj_t *row, int icon, int delta, qty_t *q)
{
    lv_obj_t *b = dos_box(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_size(b, 36, 36);
    lv_obj_set_style_radius(b, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(b, DOS_WHITE, 0);
    lv_obj_set_style_bg_opa(b, LV_OPA_TRANSP, 0);
    lv_obj_set_style_bg_opa(b, DOS_OPA_FILL_HI, LV_STATE_PRESSED);
    lv_obj_set_flex_align(b, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_clickable(b, true);
    lv_obj_set_user_data(b, (void *)(intptr_t)delta);
    dos_icon(b, (dos_icon_t)icon, 16, DOS_INK);
    lv_obj_add_event_cb(b, qty_click, LV_EVENT_CLICKED, q);
    return b;
}

lv_obj_t *dos_qty(lv_obj_t *parent, int value, int min, int max, dos_qty_cb cb, void *ud)
{
    qty_t *q = calloc(1, sizeof *q);
    q->value = value; q->min = min; q->max = max; q->cb = cb; q->ud = ud;
    lv_obj_t *c = dos_box(parent, LV_FLEX_FLOW_ROW);
    lv_obj_set_size(c, 114, 44);
    lv_obj_set_style_radius(c, 22, 0);
    lv_obj_set_style_bg_color(c, DOS_WHITE, 0);
    lv_obj_set_style_bg_opa(c, DOS_OPA_FILL, 0);
    lv_obj_set_style_border_width(c, 1, 0);
    lv_obj_set_style_border_color(c, DOS_WHITE, 0);
    lv_obj_set_style_border_opa(c, DOS_OPA_LINE, 0);
    lv_obj_set_style_pad_column(c, 2, 0);
    lv_obj_set_flex_align(c, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    q->minus = qty_btn(c, DOS_IC_MINUS, -1, q);
    q->label = dos_text(c, DOS_F15, DOS_INK, "");
    lv_obj_set_width(q->label, 26);
    lv_obj_set_style_text_align(q->label, LV_TEXT_ALIGN_CENTER, 0);
    q->plus = qty_btn(c, DOS_IC_PLUS, 1, q);
    lv_label_set_text_fmt(q->label, "%d", value);
    lv_obj_set_style_opa(q->minus, value <= min ? 90 : LV_OPA_COVER, 0);
    lv_obj_set_style_opa(q->plus, value >= max ? 90 : LV_OPA_COVER, 0);
    lv_obj_add_event_cb(c, qty_del, LV_EVENT_DELETE, q);
    return c;
}

lv_obj_t *dos_head_row(lv_obj_t *parent, const char *label, const char *title, const char *sub, lv_obj_t **actions)
{
    lv_obj_t *row = dos_box(parent, LV_FLEX_FLOW_ROW);
    lv_obj_set_width(row, lv_pct(100));
    lv_obj_set_flex_align(row, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_END, LV_FLEX_ALIGN_END);
    lv_obj_set_style_pad_column(row, 12, 0);
    lv_obj_t *h = dos_screen_head(row, label, title, sub, NULL, NULL);
    lv_obj_set_flex_grow(h, 1);
    lv_obj_t *a = dos_box(row, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(a, 8, 0);
    if(actions) *actions = a;
    return row;
}

int32_t dos_content_w(void)
{
    int32_t w = lv_display_get_horizontal_resolution(NULL);
    return (w > DOS_STAGE_MAX ? DOS_STAGE_MAX : w) - 2 * DOS_PAD;
}
