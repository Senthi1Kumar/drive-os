#include "orb.h"
#include "screens.h"
#include "ws_client.h"
#include <string.h>

static void cmd_end(lv_event_t *e) { (void)e; link_cmd("end_conversation", NULL); }

static lv_obj_t *s_col, *s_orb, *s_caption, *s_heard_row, *s_heard, *s_reply;

void agent_build(lv_obj_t *slot)
{
    s_col = screen_column(slot, 120);
    lv_obj_set_flex_align(s_col, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_row(s_col, 18, 0);
    s_orb = orb_create(s_col, 240);
    s_caption = dos_text(s_col, DOS_F15, DOS_DIM, "");

    s_heard_row = dos_box(s_col, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(s_heard_row, 8, 0);
    lv_obj_set_flex_align(s_heard_row, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_max_width(s_heard_row, 760, 0);
    dos_chip(s_heard_row, DOS_CHIP_ACCENT, -1, "YOU", true);
    s_heard = dos_text(s_heard_row, DOS_F13, DOS_INK_SOFT, "");
    lv_obj_set_style_max_width(s_heard, 680, 0);
    lv_label_set_long_mode(s_heard, LV_LABEL_LONG_MODE_DOTS);

    s_reply = dos_text(s_col, DOS_F17, DOS_INK_SOFT, "");
    lv_obj_set_width(s_reply, 760);
    lv_obj_set_style_max_width(s_reply, lv_pct(100), 0);
    lv_obj_set_style_text_align(s_reply, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_line_space(s_reply, 4, 0);

    lv_obj_t *end = dos_btn(s_col, DOS_BTN_DANGER, true, DOS_IC_X, "End");
    lv_obj_add_event_cb(end, cmd_end, LV_EVENT_CLICKED, NULL);
}

void agent_update(const ui_state_t *s)
{
    const char *st = s->voice.state;
    orb_set(s_orb, st, s->voice.level);
    const char *cap = !strcmp(st, "listening") ? "Listening…" : !strcmp(st, "thinking") ? "Thinking…"
                    : !strcmp(st, "speaking") ? "Speaking…" : "Say “Hey Nova”, or hold the voice button";
    dos_label_set(s_caption, cap);
    bool heard = s->voice.heard[0] != 0;
    dos_show(s_heard_row, heard);
    dos_label_set(s_heard, s->voice.heard);
    bool reply = s->voice.reply[0] != 0;
    dos_show(s_reply, reply);
    dos_label_set(s_reply, s->voice.reply);
    lv_obj_set_style_pad_top(s_col, reply || heard ? 48 : 120, 0);
}
