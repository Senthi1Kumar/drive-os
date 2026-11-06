#include "app.h"
#include "screens.h"
#include <stdio.h>
#include <string.h>

extern const lv_image_dsc_t dos_logo_96;

static lv_obj_t *s_version, *s_upd_icon, *s_upd_title, *s_upd_sub, *s_upd_chip, *s_hl[UI_HIGHLIGHTS_MAX];

static void voice_id_cb(lv_event_t *e)
{
    (void)e;
    app_show(SCR_ENROLL);
}

void about_build(lv_obj_t *slot)
{
    lv_obj_t *col = screen_column(slot, 26);
    lv_obj_set_flex_align(col, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_row(col, 8, 0);

    lv_obj_t *logo = lv_image_create(col);
    lv_image_set_src(logo, &dos_logo_96);
    lv_obj_set_clickable(logo, false);
    lv_obj_t *name = dos_text(col, DOS_F30, DOS_INK, "DriveOS");
    lv_obj_set_style_text_letter_space(name, -1, 0);
    s_version = dos_text(col, DOS_F13, DOS_DIM, "");
    lv_obj_t *desc = dos_text(col, DOS_F15, DOS_INK_SOFT,
                              "The in-vehicle voice assistant: ask Nova to find places, order ahead and navigate, hands-free.");
    lv_obj_set_width(desc, 520);
    lv_obj_set_style_max_width(desc, lv_pct(100), 0);
    lv_obj_set_style_text_align(desc, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_pad_top(desc, 6, 0);

    lv_obj_t *card = dos_card(col);
    lv_obj_set_width(card, 520);
    lv_obj_set_style_max_width(card, lv_pct(100), 0);
    lv_obj_set_height(card, LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(card, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(card, 6, 0);
    lv_obj_set_style_margin_top(card, 10, 0);
    dos_section_label(card, "Software update");
    lv_obj_t *row = dos_box(card, LV_FLEX_FLOW_ROW);
    lv_obj_set_width(row, lv_pct(100));
    lv_obj_set_style_pad_column(row, 10, 0);
    lv_obj_set_flex_align(row, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    s_upd_icon = dos_icon(row, DOS_IC_CHECK, 20, DOS_OK);
    lv_obj_t *tcol = dos_box(row, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_grow(tcol, 1);
    s_upd_title = dos_text(tcol, DOS_F15, DOS_INK, "");
    s_upd_sub = dos_text(tcol, DOS_F13, DOS_DIM, "");
    s_upd_chip = dos_chip(row, DOS_CHIP_ACCENT, DOS_IC_REFRESH_CW, "Update", false);
    for(int i = 0; i < UI_HIGHLIGHTS_MAX; i++) {
        s_hl[i] = dos_text(card, DOS_F13, DOS_INK_SOFT, "");
        lv_obj_set_width(s_hl[i], lv_pct(100));
        lv_label_set_long_mode(s_hl[i], LV_LABEL_LONG_MODE_DOTS);
    }

    lv_obj_t *vid = dos_btn(col, DOS_BTN_DEFAULT, false, DOS_IC_SHIELD_CHECK, "Voice ID");
    lv_obj_set_width(vid, 520);
    lv_obj_set_style_max_width(vid, lv_pct(100), 0);
    lv_obj_add_event_cb(vid, voice_id_cb, LV_EVENT_CLICKED, NULL);

    lv_obj_t *foot = dos_box(col, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(foot, 4, 0);
    lv_obj_set_style_pad_top(foot, 14, 0);
    lv_obj_set_flex_align(foot, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    dos_text(foot, DOS_F13, DOS_DIM, "© 2026 Elevatics AI. All rights reserved.");
    dos_text(foot, DOS_F13, DOS_FAINT, "Powered and Released by Elevatics AI.");
}

void about_update(const ui_state_t *s)
{
    char buf[160];
    const char *ver = s->hello.have && s->hello.version[0] ? s->hello.version : DRIVEOS_VERSION;
    const char *bld = s->hello.have && s->hello.build[0] ? s->hello.build : DRIVEOS_BUILD;
    snprintf(buf, sizeof buf, "Version %s (build %s)", ver, bld);
    dos_label_set(s_version, buf);

    bool up = s->update.available;
    if(up) {
        dos_icon_set(s_upd_icon, DOS_IC_SPARKLES, 20, DOS_ACCENT);
        dos_label_set(s_upd_title, "Update available");
        snprintf(buf, sizeof buf, "DriveOS %s is ready to install", s->update.version);
        dos_label_set(s_upd_sub, buf);
        snprintf(buf, sizeof buf, "v%s", s->update.version);
        dos_chip_set(s_upd_chip, DOS_CHIP_ACCENT, buf);
    }
    else {
        dos_icon_set(s_upd_icon, DOS_IC_CHECK, 20, DOS_OK);
        dos_label_set(s_upd_title, "You’re up to date");
        dos_label_set(s_upd_sub, "No newer version is available");
    }
    dos_show(s_upd_chip, up);
    for(int i = 0; i < UI_HIGHLIGHTS_MAX; i++) {
        bool on = up && i < s->update.n;
        dos_show(s_hl[i], on);
        if(on) {
            snprintf(buf, sizeof buf, "· %s", s->update.highlights[i]);
            dos_label_set(s_hl[i], buf);
        }
    }
}
