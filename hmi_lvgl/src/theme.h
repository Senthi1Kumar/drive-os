#pragma once
#include "icons.h"
#include "lvgl.h"

/* DriveOS design tokens (mirrors the Qt app's Theme.qml): blue = actions you take, teal = the car doing something live. */
#define DOS_BG0        lv_color_hex(0x050608)
#define DOS_BG1        lv_color_hex(0x0a0d12)
#define DOS_PANEL      lv_color_hex(0x12161d)
#define DOS_GLASS      lv_color_hex(0x101216)   /* used at DOS_OPA_GLASS */
#define DOS_CARD       lv_color_hex(0x0c0f14)
#define DOS_INK        lv_color_hex(0xe9eef5)
#define DOS_INK_SOFT   lv_color_hex(0xc3c8cf)   /* ink at 0.78 over the dark ground */
#define DOS_DIM        lv_color_hex(0x8b97a8)
#define DOS_FAINT      lv_color_hex(0x5c6776)
#define DOS_ACCENT     lv_color_hex(0x80a7ff)
#define DOS_ACCENT_INK lv_color_hex(0x061027)
#define DOS_LIVE       lv_color_hex(0x66edc5)
#define DOS_LIVE_INK   lv_color_hex(0x04150f)
#define DOS_OK         lv_color_hex(0x3dd68c)
#define DOS_WARN       lv_color_hex(0xffc56b)
#define DOS_DANGER     lv_color_hex(0xff8f8f)
#define DOS_WHITE      lv_color_hex(0xffffff)

/* alpha tokens (0..255) */
#define DOS_OPA_FILL    10    /* white 0.04 */
#define DOS_OPA_FILL_HI 20    /* white 0.08 */
#define DOS_OPA_LINE    26    /* white 0.10 */
#define DOS_OPA_LINE_HI 41    /* white 0.16 */
#define DOS_OPA_SOFT    36    /* accent/live/danger fills 0.14 */
#define DOS_OPA_LINE_C  77    /* accent/live lines 0.30 */
#define DOS_OPA_GLASS   235   /* 0.92 */

#define DOS_RADIUS    18
#define DOS_RADIUS_SM 14
#define DOS_PAD       16
#define DOS_GAP       10
#define DOS_TAP       52
#define DOS_STAGE_MAX 1200
#define DOS_TOPBAR_H  56
#define DOS_DOCK_H    190

typedef enum { DOS_F11, DOS_F13, DOS_F15, DOS_F17, DOS_F30, DOS_F46, DOS_F64 } dos_font_t;
typedef enum { DOS_BTN_DEFAULT, DOS_BTN_PRIMARY, DOS_BTN_LIVE, DOS_BTN_TINT_LIVE, DOS_BTN_DANGER, DOS_BTN_GHOST } dos_btn_kind_t;
typedef enum { DOS_CHIP_DEFAULT, DOS_CHIP_QUIET, DOS_CHIP_LIVE, DOS_CHIP_ACCENT, DOS_CHIP_WARN, DOS_CHIP_DANGER } dos_chip_kind_t;

const lv_font_t *dos_font(dos_font_t f);

/* base: a transparent, non-scrolling flex container (row or column) */
lv_obj_t *dos_box(lv_obj_t *parent, lv_flex_flow_t flow);
/* card: card colour, hairline border, radius 18, pad 16 */
lv_obj_t *dos_card(lv_obj_t *parent);
/* glass panel (voice float, dock pill): translucent, hairline-hi border */
lv_obj_t *dos_glass(lv_obj_t *parent, int32_t radius);
/* text */
lv_obj_t *dos_text(lv_obj_t *parent, dos_font_t f, lv_color_t c, const char *s);
lv_obj_t *dos_section_label(lv_obj_t *parent, const char *s);   /* small uppercase tracking label */
void dos_text_set_upper(lv_obj_t *label, const char *s);
/* icon (alpha bitmap, recoloured); px picks the nearest generated size */
lv_obj_t *dos_icon(lv_obj_t *parent, dos_icon_t id, int px, lv_color_t c);
void dos_icon_set(lv_obj_t *img, dos_icon_t id, int px, lv_color_t c);
/* pill chip: icon (or -1) + text; returns the chip, the label is its last child */
lv_obj_t *dos_chip(lv_obj_t *parent, dos_chip_kind_t k, int icon, const char *text, bool tiny);
void dos_chip_set(lv_obj_t *chip, dos_chip_kind_t k, const char *text);
/* button: icon (or -1) + text; returns the button, the label is its last child (absent when text is NULL/empty) */
lv_obj_t *dos_btn(lv_obj_t *parent, dos_btn_kind_t k, bool small, int icon, const char *text);
/* round icon button (dock mic/settings), `d` px diameter */
lv_obj_t *dos_round_btn(lv_obj_t *parent, int d, int icon, int icon_px);
/* "LABEL / Big title / sub" screen header; returns the container, title & sub via out params (may be NULL) */
lv_obj_t *dos_screen_head(lv_obj_t *parent, const char *label, const char *title, const char *sub, lv_obj_t **title_out, lv_obj_t **sub_out);
/* gradient helper: vertical/horizontal 2-3 stop gradient on obj background */
void dos_bg_grad(lv_obj_t *obj, lv_grad_dsc_t *store, bool horizontal, lv_color_t a, lv_color_t b);
lv_color_t dos_mix(lv_color_t a, lv_color_t b, uint8_t t255);   /* t=0 -> a, 255 -> b */
/* label text only when it changed (avoids needless relayout/redraw on the 10 Hz state stream) */
void dos_label_set(lv_obj_t *label, const char *s);
void dos_show(lv_obj_t *obj, bool on);

/* gradient tile with a centred glyph (stand-in for photos, which DriveOS does not ship) */
lv_obj_t *dos_thumb(lv_obj_t *parent, int32_t w, int32_t h, int icon, int icon_px, int32_t radius);
dos_icon_t dos_cat_icon(const char *category);
/* quantity stepper [- n +]; cb(value) fires with the requested new value */
typedef void (*dos_qty_cb)(int value, void *ud);
lv_obj_t *dos_qty(lv_obj_t *parent, int value, int min, int max, dos_qty_cb cb, void *ud);
/* row/column container filling the parent width with optional flex grow children helpers */
lv_obj_t *dos_head_row(lv_obj_t *parent, const char *label, const char *title, const char *sub, lv_obj_t **actions);
void dos_fmt_money(char *out, size_t cap, double v);
int32_t dos_content_w(void);   /* usable screen width inside the stage (independent of which screen is visible) */
