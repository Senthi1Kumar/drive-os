#include "app.h"
#include "screens.h"
#include "ws_client.h"
#include <stdio.h>
#include <string.h>

/* Voice ID enrollment. Server side is PROPOSED, see ENROLL_CONTRACT.md. */
static lv_obj_t *s_col, *s_ring;
static uint32_t s_built, s_gen = 1, s_built_gen;
static bool s_valid, s_consent;

static void go_about(lv_event_t *e)
{
    (void)e;
    s_consent = false;
    s_gen++;
    app_show(SCR_ABOUT);
}
static void cmd_cancel(lv_event_t *e)
{
    link_cmd("enroll_cancel", NULL);
    go_about(e);
}
static void cmd_take(lv_event_t *e) { (void)e; link_cmd("enroll_take", NULL); }
static void cmd_agree(lv_event_t *e)
{
    (void)e;
    s_consent = true;
    s_gen++;
    link_cmd("enroll_start", "{\"name\":\"Driver\",\"consent\":true}");
    app_refresh_async();
}

void enroll_reset(void)
{
    s_consent = false;
    s_gen++;
}

void enroll_build(lv_obj_t *slot)
{
    s_col = screen_column(slot, 18);
    lv_obj_set_flex_align(s_col, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
}

static lv_obj_t *panel(lv_obj_t *col)
{
    lv_obj_t *c = dos_card(col);
    lv_obj_set_width(c, 760);
    lv_obj_set_style_max_width(c, lv_pct(100), 0);
    lv_obj_set_height(c, LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(c, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(c, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_all(c, 22, 0);
    lv_obj_set_style_pad_row(c, 12, 0);
    return c;
}

static lv_obj_t *wrapped(lv_obj_t *p, dos_font_t f, lv_color_t c, const char *t, int32_t w)
{
    lv_obj_t *l = dos_text(p, f, c, t);
    lv_obj_set_width(l, w);
    lv_obj_set_style_text_align(l, LV_TEXT_ALIGN_CENTER, 0);
    return l;
}

static void quality_rows(lv_obj_t *parent, const ui_state_t *s)
{
    for(int i = 0; i < s->enroll.n_q; i++) {
        const typeof(s->enroll.q[0]) *q = &s->enroll.q[i];
        lv_obj_t *r = dos_box(parent, LV_FLEX_FLOW_ROW);
        lv_obj_set_width(r, lv_pct(100));
        lv_obj_set_style_pad_column(r, 8, 0);
        lv_obj_set_flex_align(r, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
        char b[48];
        snprintf(b, sizeof b, "Take %d", i + 1);
        lv_obj_t *t = dos_text(r, DOS_F13, DOS_INK, b);
        lv_obj_set_width(t, 64);
        snprintf(b, sizeof b, "%.1f s", q->duration_s);
        dos_chip(r, q->duration_s >= 2.0f ? DOS_CHIP_QUIET : DOS_CHIP_WARN, -1, b, true);
        snprintf(b, sizeof b, "SNR %.0f dB", q->snr_db);
        dos_chip(r, q->snr_db >= 15 ? DOS_CHIP_QUIET : DOS_CHIP_WARN, -1, b, true);
        snprintf(b, sizeof b, "Clipping %.1f%%", q->clipping_pct);
        dos_chip(r, q->clipping_pct < 1.0f ? DOS_CHIP_QUIET : DOS_CHIP_WARN, -1, b, true);
        if(q->outlier) dos_chip(r, DOS_CHIP_WARN, -1, "Outlier · dropped", true);
        else if(!q->ok) dos_chip(r, DOS_CHIP_DANGER, -1, "Re-record", true);
        else dos_chip(r, DOS_CHIP_LIVE, DOS_IC_CHECK, "Good", true);
    }
}

static void fill(const ui_state_t *s)
{
    lv_obj_clean(s_col);
    s_ring = NULL;
    lv_obj_t *act;
    lv_obj_t *hr = dos_head_row(s_col, "Settings", "Voice ID", "Approve payments with your voice", &act);
    lv_obj_set_width(hr, 760);
    lv_obj_set_style_max_width(hr, lv_pct(100), 0);
    lv_obj_t *bk = dos_btn(act, DOS_BTN_DEFAULT, true, DOS_IC_ARROW_LEFT, NULL);
    lv_obj_add_event_cb(bk, s->enroll.active ? cmd_cancel : go_about, LV_EVENT_CLICKED, NULL);

    lv_obj_t *p = panel(s_col);
    if(!s->enroll.active) {
        if(!s_consent) {
            dos_icon(p, DOS_IC_SHIELD_CHECK, 40, DOS_LIVE);
            dos_text(p, DOS_F17, DOS_INK, "Record your voice for payments");
            wrapped(p, DOS_F13, DOS_INK_SOFT,
                    "DriveOS will ask you to read a few short phrases and build a private voice template on this device. "
                    "The recordings are not kept, only the template is, and you can delete it at any time.", 640);
            lv_obj_t *row = dos_box(p, LV_FLEX_FLOW_ROW);
            lv_obj_set_style_pad_column(row, 10, 0);
            lv_obj_set_style_pad_top(row, 8, 0);
            lv_obj_t *c = dos_btn(row, DOS_BTN_GHOST, false, -1, "Not now");
            lv_obj_add_event_cb(c, go_about, LV_EVENT_CLICKED, NULL);
            lv_obj_t *a = dos_btn(row, DOS_BTN_PRIMARY, false, DOS_IC_CHECK, "I agree, start");
            lv_obj_add_event_cb(a, cmd_agree, LV_EVENT_CLICKED, NULL);
        }
        else {
            dos_text(p, DOS_F17, DOS_INK, "Starting…");
            dos_text(p, DOS_F13, DOS_DIM, "Waiting for DriveOS to open the enrollment session.");
        }
        return;
    }
    const char *st = s->enroll.stage;
    bool done = !strcmp(st, "done"), failed = !strcmp(st, "failed");
    bool rec = !strcmp(st, "recording"), ana = !strcmp(st, "analyzing");

    /* progress dots */
    lv_obj_t *dots = dos_box(p, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(dots, 8, 0);
    for(int i = 0; i < s->enroll.total && i < 8; i++) {
        lv_obj_t *d = lv_obj_create(dots);
        lv_obj_remove_style_all(d);
        lv_obj_set_size(d, i + 1 == s->enroll.take && !done ? 26 : 10, 10);
        lv_obj_set_style_radius(d, 5, 0);
        bool fin = i < s->enroll.n_q || done;
        lv_obj_set_style_bg_color(d, fin ? DOS_LIVE : (i + 1 == s->enroll.take ? DOS_ACCENT : DOS_WHITE), 0);
        lv_obj_set_style_bg_opa(d, fin || i + 1 == s->enroll.take ? LV_OPA_COVER : DOS_OPA_LINE_HI, 0);
    }
    if(done) {
        dos_icon(p, DOS_IC_SHIELD_CHECK, 56, DOS_OK);
        dos_text(p, DOS_F17, DOS_INK, "Voice ID is ready");
        char b[80];
        snprintf(b, sizeof b, "%d of %d takes kept", s->enroll.kept, s->enroll.total);
        dos_text(p, DOS_F13, DOS_OK, b);
    }
    else if(failed) {
        dos_icon(p, DOS_IC_CIRCLE_X, 40, DOS_DANGER);
        wrapped(p, DOS_F17, DOS_INK, s->enroll.error[0] ? s->enroll.error : "Enrollment failed", 640);
    }
    else {
        char b[48];
        snprintf(b, sizeof b, "Take %d of %d", s->enroll.take, s->enroll.total);
        dos_section_label(p, b);
        wrapped(p, DOS_F17, DOS_INK, s->enroll.prompt, 640);
        if(ana) {
            lv_obj_t *sp = lv_spinner_create(p);
            lv_obj_set_size(sp, 40, 40);
            lv_spinner_set_anim_params(sp, 1000, 240);
            lv_obj_set_style_arc_color(sp, DOS_ACCENT, LV_PART_INDICATOR);
            lv_obj_set_style_arc_color(sp, DOS_WHITE, LV_PART_MAIN);
            lv_obj_set_style_arc_opa(sp, DOS_OPA_LINE, LV_PART_MAIN);
            dos_text(p, DOS_F13, DOS_DIM, "Checking quality…");
        }
        else if(rec) {
            lv_obj_t *wrap = lv_obj_create(p);
            lv_obj_remove_style_all(wrap);
            lv_obj_set_size(wrap, 130, 130);
            s_ring = lv_obj_create(wrap);
            lv_obj_remove_style_all(s_ring);
            lv_obj_set_size(s_ring, 90, 90);
            lv_obj_center(s_ring);
            lv_obj_set_style_radius(s_ring, LV_RADIUS_CIRCLE, 0);
            lv_obj_set_style_bg_color(s_ring, DOS_LIVE, 0);
            lv_obj_set_style_bg_opa(s_ring, DOS_OPA_SOFT, 0);
            lv_obj_set_style_border_width(s_ring, 2, 0);
            lv_obj_set_style_border_color(s_ring, DOS_LIVE, 0);
            lv_obj_set_style_border_opa(s_ring, DOS_OPA_LINE_C, 0);
            lv_obj_center(dos_icon(wrap, DOS_IC_MIC, 30, DOS_LIVE));
            dos_text(p, DOS_F13, DOS_LIVE, "Recording · read the phrase now");
        }
        else {
            lv_obj_t *b2 = dos_btn(p, DOS_BTN_LIVE, false, DOS_IC_MIC, "Record take");
            lv_obj_add_event_cb(b2, cmd_take, LV_EVENT_CLICKED, NULL);
        }
    }
    if(s->enroll.n_q) {
        lv_obj_t *dv = lv_obj_create(p);
        lv_obj_remove_style_all(dv);
        lv_obj_set_size(dv, lv_pct(100), 1);
        lv_obj_set_style_bg_color(dv, DOS_WHITE, 0);
        lv_obj_set_style_bg_opa(dv, DOS_OPA_LINE, 0);
        lv_obj_t *rows = dos_box(p, LV_FLEX_FLOW_COLUMN);
        lv_obj_set_width(rows, lv_pct(100));
        lv_obj_set_style_pad_row(rows, 8, 0);
        quality_rows(rows, s);
    }
    lv_obj_t *foot = dos_box(p, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(foot, 8, 0);
    if(done) {
        lv_obj_t *d = dos_btn(foot, DOS_BTN_PRIMARY, false, DOS_IC_CHECK, "Done");
        lv_obj_add_event_cb(d, go_about, LV_EVENT_CLICKED, NULL);
    }
    else {
        if(failed) {
            lv_obj_t *r = dos_btn(foot, DOS_BTN_LIVE, false, DOS_IC_MIC, "Try again");
            lv_obj_add_event_cb(r, cmd_take, LV_EVENT_CLICKED, NULL);
        }
        lv_obj_t *c = dos_btn(foot, DOS_BTN_GHOST, false, -1, "Cancel");
        lv_obj_add_event_cb(c, cmd_cancel, LV_EVENT_CLICKED, NULL);
    }
}

void enroll_update(const ui_state_t *s)
{
    uint32_t h = ui_enroll_hash() ^ (s->enroll.active ? 1u : 0u);
    if(!s_valid || h != s_built || s_gen != s_built_gen) {
        fill(s);
        s_built = h; s_built_gen = s_gen; s_valid = true;
    }
    if(s_ring) {
        int32_t d = 80 + (int32_t)(s->voice.level * 40);
        lv_obj_set_size(s_ring, d, d);
        lv_obj_center(s_ring);
    }
}
