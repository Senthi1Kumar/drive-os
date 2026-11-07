#include "app.h"
#include "preview.h"
#include "screens.h"
#include "ws_client.h"
#include <stdio.h>
#include <string.h>

static lv_obj_t *s_col, *s_ring, *s_face_img, *s_face_icon;
static uint32_t s_built, s_gen = 1, s_built_gen;
static bool s_valid, s_consent, s_face;
typedef enum { FACE_NONE, FACE_CONSENT, FACE_DELETE, FACE_ADD, FACE_EDIT } face_action_t;
static face_action_t s_face_action;
static lv_obj_t *s_name_input;
static char s_pending_name[48], s_pending_role[12], s_edit_id[48], s_delete_id[48];
static bool s_delete_linked, s_adding;

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

void enroll_open_voice(void)
{
    s_face = false;
    s_face_action = FACE_NONE;
}

void enroll_open_face(void)
{
    s_face = true;
    s_face_action = FACE_NONE;
    s_pending_name[0] = s_pending_role[0] = s_edit_id[0] = s_delete_id[0] = 0;
    s_delete_linked = false;
    s_adding = false;
}

void enroll_open_face_consent(void)
{
    enroll_open_face();
    s_face_action = FACE_CONSENT;
}

void enroll_open_face_delete(void)
{
    enroll_open_face();
    s_face_action = FACE_DELETE;
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

static void face_choose(lv_event_t *e)
{
    s_face_action = (face_action_t)(intptr_t)lv_event_get_user_data(e);
    s_gen++;
    app_refresh_async();
}

void enroll_leave_face(void)
{
    if(!s_face) return;
    link_cmd("enroll_face_cancel", NULL);
    preview_clear();
    s_face_action = FACE_NONE;
}

static void face_leave(lv_event_t *e)
{
    enroll_leave_face();
    go_about(e);
}

static const char *face_selected_role(const ui_state_t *s)
{
    if(s_pending_role[0]) return s_pending_role;
    for(int i = 0; i < s->face_profiles.n; i++)
        if(!strcmp(s->face_profiles.items[i].id, s->face_profiles.selected_id)) return s->face_profiles.items[i].role;
    return "driver";
}

static void face_start(lv_event_t *e)
{
    (void)e;
    const ui_state_t *s = ui_state();
    const char *name = s_pending_name[0] ? s_pending_name : (s->face_id.name[0] ? s->face_id.name : "Driver");
    const char *pid = s_adding ? "new" : s->face_profiles.selected_id;
    link_cmd_str3_bool("enroll_face_start", "profile_id", pid, "name", name, "role", face_selected_role(s),
                       "consent", true);
    s_face_action = FACE_NONE;
    s_gen++;
    app_refresh_async();
}

static void face_link(lv_event_t *e)
{
    (void)e;
    link_cmd_bool("enroll_face_link", "confirm", true);
}

static void face_delete(lv_event_t *e)
{
    (void)e;
    const char *pid = s_delete_id[0] ? s_delete_id : ui_state()->face_profiles.selected_id;
    link_cmd_str_bool("enroll_face_delete", "profile_id", pid, "confirm", true);
    s_face_action = FACE_NONE;
    s_gen++;
    app_refresh_async();
}

static void face_delete_choose(lv_event_t *e)
{
    const ui_state_t *s = ui_state();
    const char *pid = lv_event_get_user_data(e);
    snprintf(s_delete_id, sizeof s_delete_id, "%s", pid);
    s_delete_linked = false;
    for(int i = 0; i < s->face_profiles.n; i++)
        if(!strcmp(pid, s->face_profiles.items[i].id)) s_delete_linked = s->face_profiles.items[i].linked;
    s_face_action = FACE_DELETE;
    s_gen++;
    app_refresh_async();
}

static void face_retry(lv_event_t *e)
{
    (void)e;
    s_face_action = !strcmp(ui_state()->enroll_face.operation, "delete") ? FACE_DELETE : FACE_CONSENT;
    s_gen++;
    app_refresh_async();
}

static void face_preview(lv_obj_t *p)
{
    lv_obj_t *wrap = lv_obj_create(p);
    lv_obj_remove_style_all(wrap);
    lv_obj_set_scrollable(wrap, false);
    lv_obj_set_size(wrap, 120, 120);
    lv_obj_set_style_radius(wrap, 22, 0);
    lv_obj_set_style_clip_corner(wrap, true, 0);
    lv_obj_set_style_bg_color(wrap, DOS_CARD, 0);
    lv_obj_set_style_bg_opa(wrap, LV_OPA_COVER, 0);
    s_face_icon = dos_icon(wrap, DOS_IC_SCAN_FACE, 48, DOS_FAINT);
    lv_obj_center(s_face_icon);
    s_face_img = lv_image_create(wrap);
    lv_obj_set_size(s_face_img, 120, 120);
    lv_obj_set_pos(s_face_img, 0, 0);
    lv_image_set_inner_align(s_face_img, LV_IMAGE_ALIGN_STRETCH);
    if(preview_image()) {
        lv_image_set_src(s_face_img, preview_image());
        lv_obj_set_hidden(s_face_icon, true);
    }
}

static void face_consent_view(lv_obj_t *p)
{
    dos_icon(p, DOS_IC_SCAN_FACE, 44, DOS_LIVE);
    dos_text(p, DOS_F17, DOS_INK, "Enroll your face for payments");
    wrapped(p, DOS_F13, DOS_INK_SOFT,
            "Look at the F455 camera while it creates an encrypted face template inside the camera. "
            "DriveOS stores only a private user mapping. Camera previews stay in memory and are never saved.", 640);
    lv_obj_t *row = dos_box(p, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(row, 10, 0);
    lv_obj_t *c = dos_btn(row, DOS_BTN_GHOST, false, -1, "Not now");
    lv_obj_add_event_cb(c, face_leave, LV_EVENT_CLICKED, NULL);
    lv_obj_t *a = dos_btn(row, DOS_BTN_PRIMARY, false, DOS_IC_CHECK, "I agree, start");
    lv_obj_add_event_cb(a, face_start, LV_EVENT_CLICKED, NULL);
}

static void face_delete_view(lv_obj_t *p, const ui_state_t *s)
{
    dos_icon(p, DOS_IC_TRASH_2, 44, DOS_DANGER);
    dos_text(p, DOS_F17, DOS_INK, "Delete Face ID?");
    wrapped(p, DOS_F13, DOS_INK_SOFT,
            s_delete_linked ?
                "DriveOS will unlink this face. The existing camera entry belongs to another app and will be kept." :
                "DriveOS will delete this face template from the F455 and remove its private user mapping.", 640);
    lv_obj_t *row = dos_box(p, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(row, 10, 0);
    lv_obj_t *c = dos_btn(row, DOS_BTN_GHOST, false, -1, "Keep Face ID");
    lv_obj_add_event_cb(c, face_leave, LV_EVENT_CLICKED, NULL);
    lv_obj_t *d = dos_btn(row, DOS_BTN_DANGER, false, DOS_IC_TRASH_2, "Delete Face ID");
    lv_obj_add_event_cb(d, face_delete, LV_EVENT_CLICKED, NULL);
}

static void face_form_open(lv_event_t *e)
{
    const char *pid = lv_event_get_user_data(e);
    const ui_state_t *s = ui_state();
    s_pending_name[0] = 0;
    snprintf(s_pending_role, sizeof s_pending_role, "driver");
    if(!strcmp(pid, "new")) {
        s_adding = true;
        s_face_action = FACE_ADD;
        s_edit_id[0] = 0;
    }
    else {
        s_adding = false;
        s_face_action = FACE_EDIT;
        snprintf(s_edit_id, sizeof s_edit_id, "%s", pid);
        for(int i = 0; i < s->face_profiles.n; i++) {
            const __typeof__(s->face_profiles.items[0]) *p = &s->face_profiles.items[i];
            if(strcmp(pid, p->id)) continue;
            snprintf(s_pending_name, sizeof s_pending_name, "%s", p->name);
            snprintf(s_pending_role, sizeof s_pending_role, "%s", p->role);
        }
    }
    s_gen++;
    app_refresh_async();
}

static void face_role_choose(lv_event_t *e)
{
    snprintf(s_pending_role, sizeof s_pending_role, "%s", (const char *)lv_event_get_user_data(e));
    s_gen++;
    app_refresh_async();
}

static void face_select(lv_event_t *e)
{
    link_cmd_str("face_profile_select", "profile_id", lv_event_get_user_data(e));
}

static void face_form_save(lv_event_t *e)
{
    (void)e;
    const char *name = s_name_input ? lv_textarea_get_text(s_name_input) : "";
    name += strspn(name, " \t\r\n");
    if(!*name) return;
    if(s_face_action == FACE_ADD) {
        snprintf(s_pending_name, sizeof s_pending_name, "%s", name);
        s_face_action = FACE_CONSENT;
    }
    else {
        link_cmd_str3("face_profile_edit", "profile_id", s_edit_id, "name", name, "role", s_pending_role);
        s_face_action = FACE_NONE;
    }
    s_gen++;
    app_refresh_async();
}

void enroll_open_face_add(void)
{
    enroll_open_face();
    s_adding = true;
    s_face_action = FACE_ADD;
    s_pending_name[0] = 0;
    snprintf(s_pending_role, sizeof s_pending_role, "driver");
}

void enroll_open_face_edit(void)
{
    enroll_open_face();
    s_face_action = FACE_EDIT;
}

static void face_form_view(lv_obj_t *p, const ui_state_t *s)
{
    if(s_face_action == FACE_EDIT && !s_edit_id[0] && s->face_profiles.n) {
        snprintf(s_edit_id, sizeof s_edit_id, "%s", s->face_profiles.items[0].id);
        snprintf(s_pending_name, sizeof s_pending_name, "%s", s->face_profiles.items[0].name);
        snprintf(s_pending_role, sizeof s_pending_role, "%s", s->face_profiles.items[0].role);
    }
    dos_text(p, DOS_F17, DOS_INK, s_face_action == FACE_ADD ? "Add a face profile" : "Edit face profile");
    s_name_input = lv_textarea_create(p);
    lv_obj_set_width(s_name_input, lv_pct(100));
    lv_textarea_set_one_line(s_name_input, true);
    lv_textarea_set_max_length(s_name_input, 32);
    lv_textarea_set_placeholder_text(s_name_input, "Name");
    lv_textarea_set_text(s_name_input, s_pending_name);
    lv_obj_t *roles = dos_box(p, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(roles, 10, 0);
    lv_obj_t *d = dos_btn(roles, !strcmp(s_pending_role, "driver") ? DOS_BTN_PRIMARY : DOS_BTN_DEFAULT,
                          false, -1, "Driver");
    lv_obj_add_event_cb(d, face_role_choose, LV_EVENT_CLICKED, "driver");
    lv_obj_t *g = dos_btn(roles, !strcmp(s_pending_role, "passenger") ? DOS_BTN_PRIMARY : DOS_BTN_DEFAULT,
                          false, -1, "Passenger");
    lv_obj_add_event_cb(g, face_role_choose, LV_EVENT_CLICKED, "passenger");
    lv_obj_t *actions = dos_box(p, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(actions, 10, 0);
    lv_obj_t *back = dos_btn(actions, DOS_BTN_GHOST, false, -1, "Back");
    lv_obj_add_event_cb(back, face_leave, LV_EVENT_CLICKED, NULL);
    lv_obj_t *save = dos_btn(actions, DOS_BTN_PRIMARY, false, DOS_IC_CHECK,
                             s_face_action == FACE_ADD ? "Continue" : "Save");
    lv_obj_add_event_cb(save, face_form_save, LV_EVENT_CLICKED, NULL);
    lv_obj_t *keyboard = lv_keyboard_create(p);
    lv_keyboard_set_textarea(keyboard, s_name_input);
}

static void face_idle_view(lv_obj_t *p, const ui_state_t *s)
{
    if(!s->face_profiles.n) {
        dos_icon(p, DOS_IC_SCAN_FACE, 40, DOS_LIVE);
        dos_text(p, DOS_F17, DOS_INK, "No face profiles yet");
        wrapped(p, DOS_F13, DOS_INK_SOFT, "Add a driver or passenger using the F455 camera.", 640);
    }
    for(int i = 0; i < s->face_profiles.n; i++) {
        const __typeof__(s->face_profiles.items[0]) *profile = &s->face_profiles.items[i];
        lv_obj_t *card = dos_card(p);
        lv_obj_set_width(card, lv_pct(100));
        lv_obj_set_height(card, 68);
        lv_obj_set_flex_flow(card, LV_FLEX_FLOW_ROW);
        lv_obj_set_style_pad_all(card, 8, 0);
        lv_obj_set_style_pad_column(card, 8, 0);
        lv_obj_t *name = dos_text(card, DOS_F15, DOS_INK, profile->name[0] ? profile->name : "Driver");
        lv_obj_set_flex_grow(name, 1);
        dos_chip(card, !strcmp(profile->role, "driver") ? DOS_CHIP_LIVE : DOS_CHIP_DEFAULT, -1, profile->role, true);
        lv_obj_t *sel = dos_btn(card, !strcmp(profile->id, s->face_profiles.selected_id) ? DOS_BTN_LIVE : DOS_BTN_DEFAULT,
                                true, -1, !strcmp(profile->id, s->face_profiles.selected_id) ? "Selected" : "Select");
        lv_obj_add_event_cb(sel, face_select, LV_EVENT_CLICKED, (void *)profile->id);
        lv_obj_t *edit = dos_btn(card, DOS_BTN_DEFAULT, true, -1, "Edit");
        lv_obj_add_event_cb(edit, face_form_open, LV_EVENT_CLICKED, (void *)profile->id);
        lv_obj_t *del = dos_btn(card, DOS_BTN_DANGER, true, DOS_IC_TRASH_2, NULL);
        lv_obj_add_event_cb(del, face_delete_choose, LV_EVENT_CLICKED, (void *)profile->id);
    }
    lv_obj_t *row = dos_box(p, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(row, 10, 0);
    if(s->face_profiles.n) {
        lv_obj_t *re = dos_btn(row, DOS_BTN_PRIMARY, false, DOS_IC_SCAN_FACE, "Re-enroll selected");
        lv_obj_add_event_cb(re, face_choose, LV_EVENT_CLICKED, (void *)(intptr_t)FACE_CONSENT);
    }
    if(s->face_profiles.n < s->face_profiles.max) {
        lv_obj_t *add = dos_btn(row, DOS_BTN_LIVE, false, DOS_IC_PLUS, "Add user");
        lv_obj_add_event_cb(add, face_form_open, LV_EVENT_CLICKED, "new");
    }
}

static void fill_face(const ui_state_t *s)
{
    lv_obj_clean(s_col);
    s_ring = NULL;
    s_face_img = s_face_icon = NULL;
    s_name_input = NULL;
    lv_obj_t *act;
    lv_obj_t *hr = dos_head_row(s_col, "Settings", "Face ID", "Approve payments with your face", &act);
    lv_obj_set_width(hr, 760);
    lv_obj_set_style_max_width(hr, lv_pct(100), 0);
    lv_obj_t *bk = dos_btn(act, DOS_BTN_DEFAULT, true, DOS_IC_ARROW_LEFT, NULL);
    lv_obj_add_event_cb(bk, face_leave, LV_EVENT_CLICKED, NULL);

    lv_obj_t *p = panel(s_col);
    if(s_face_action == FACE_CONSENT) { face_consent_view(p); return; }
    if(s_face_action == FACE_DELETE) { face_delete_view(p, s); return; }
    if(s_face_action == FACE_ADD || s_face_action == FACE_EDIT) { face_form_view(p, s); return; }
    if(!s->enroll_face.active) { face_idle_view(p, s); return; }

    const char *st = s->enroll_face.stage;
    if(!strcmp(st, "capturing")) {
        face_preview(p);
        dos_text(p, DOS_F17, DOS_INK, "Look at the camera");
        dos_text(p, DOS_F13, DOS_DIM, "Keep one face in view until enrollment finishes.");
    }
    else if(!strcmp(st, "link_confirm")) {
        face_preview(p);
        dos_text(p, DOS_F17, DOS_INK, "Existing face found");
        wrapped(p, DOS_F13, DOS_INK_SOFT,
                "This face is already stored on the camera. Link it to DriveOS? The existing entry will be kept.", 640);
        lv_obj_t *row = dos_box(p, LV_FLEX_FLOW_ROW);
        lv_obj_set_style_pad_column(row, 10, 0);
        lv_obj_t *c = dos_btn(row, DOS_BTN_GHOST, false, -1, "Cancel");
        lv_obj_add_event_cb(c, face_leave, LV_EVENT_CLICKED, NULL);
        lv_obj_t *a = dos_btn(row, DOS_BTN_PRIMARY, false, DOS_IC_CHECK, "Link existing face");
        lv_obj_add_event_cb(a, face_link, LV_EVENT_CLICKED, NULL);
        return;
    }
    else if(!strcmp(st, "deleting")) {
        lv_obj_t *sp = lv_spinner_create(p);
        lv_obj_set_size(sp, 44, 44);
        dos_text(p, DOS_F17, DOS_INK, "Deleting Face ID…");
    }
    else if(!strcmp(st, "done")) {
        bool del = !strcmp(s->enroll_face.operation, "delete");
        dos_icon(p, del ? DOS_IC_TRASH_2 : DOS_IC_SHIELD_CHECK, 52, DOS_OK);
        dos_text(p, DOS_F17, DOS_OK, del ? "Face ID removed" : "Face ID is ready");
        if(!del && s->enroll_face.name[0]) dos_text(p, DOS_F13, DOS_DIM, s->enroll_face.name);
        lv_obj_t *d = dos_btn(p, DOS_BTN_PRIMARY, false, DOS_IC_CHECK, "Done");
        lv_obj_add_event_cb(d, face_leave, LV_EVENT_CLICKED, NULL);
        return;
    }
    else if(!strcmp(st, "failed")) {
        dos_icon(p, DOS_IC_CIRCLE_X, 44, DOS_DANGER);
        wrapped(p, DOS_F17, DOS_INK, s->enroll_face.error[0] ? s->enroll_face.error : "Face ID operation failed", 640);
        lv_obj_t *row = dos_box(p, LV_FLEX_FLOW_ROW);
        lv_obj_set_style_pad_column(row, 10, 0);
        lv_obj_t *r = dos_btn(row, DOS_BTN_LIVE, false, DOS_IC_REFRESH_CW, "Try again");
        lv_obj_add_event_cb(r, face_retry, LV_EVENT_CLICKED, NULL);
        lv_obj_t *c = dos_btn(row, DOS_BTN_GHOST, false, -1, "Cancel");
        lv_obj_add_event_cb(c, face_leave, LV_EVENT_CLICKED, NULL);
        return;
    }
    lv_obj_t *c = dos_btn(p, DOS_BTN_GHOST, false, -1, "Cancel");
    lv_obj_add_event_cb(c, face_leave, LV_EVENT_CLICKED, NULL);
}

void enroll_update(const ui_state_t *s)
{
    uint32_t h = ui_enroll_hash();
    if(!s_valid || h != s_built || s_gen != s_built_gen) {
        if(s_face) fill_face(s);
        else fill(s);
        s_built = h; s_built_gen = s_gen; s_valid = true;
    }
    if(s_ring) {
        int32_t d = 80 + (int32_t)(s->voice.level * 40);
        lv_obj_set_size(s_ring, d, d);
        lv_obj_center(s_ring);
    }
}

void enroll_preview_frame(void)
{
    if(!s_face || !s_face_img) return;
    if(!lv_obj_is_valid(s_face_img)) { s_face_img = NULL; return; }
    if(!preview_image()) return;
    if(lv_image_get_src(s_face_img) != preview_image()) lv_image_set_src(s_face_img, preview_image());
    else lv_obj_invalidate(s_face_img);
    if(s_face_icon) lv_obj_set_hidden(s_face_icon, true);
}
