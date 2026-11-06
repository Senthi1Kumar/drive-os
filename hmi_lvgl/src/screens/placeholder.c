#include "screens.h"

lv_obj_t *screen_column(lv_obj_t *slot, int32_t pad_top)
{
    lv_obj_t *c = lv_obj_create(slot);
    lv_obj_remove_style_all(c);
    lv_obj_set_size(c, lv_pct(100), lv_pct(100));
    lv_obj_set_flex_flow(c, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_hor(c, DOS_PAD, 0);
    lv_obj_set_style_pad_top(c, pad_top, 0);
    lv_obj_set_style_pad_bottom(c, DOS_DOCK_H + 10, 0);
    lv_obj_set_style_pad_row(c, 14, 0);
    lv_obj_set_scrollbar_mode(c, LV_SCROLLBAR_MODE_OFF);
    return c;
}

void placeholder_build(lv_obj_t *slot, const char *label, const char *title, const char *sub, dos_icon_t icon)
{
    lv_obj_t *col = screen_column(slot, 18);
    dos_screen_head(col, label, title, sub, NULL, NULL);
    lv_obj_t *card = dos_card(col);
    lv_obj_set_width(card, lv_pct(100));
    lv_obj_set_height(card, 220);
    lv_obj_set_flex_flow(card, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(card, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_row(card, 10, 0);
    dos_icon(card, icon, 30, DOS_FAINT);
    dos_text(card, DOS_F15, DOS_DIM, "Coming soon");
}
