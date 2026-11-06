#include "orb.h"
#include "theme.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

typedef enum { T_IDLE, T_LISTEN, T_THINK, T_SPEAK } tone_t;

typedef struct {
    int32_t size;
    tone_t tone;
    float level, level_s;
    lv_obj_t *ring[3], *halo, *core, *spec, *arc, *dot;
    lv_grad_dsc_t halo_g, core_g;
    tone_t painted;
    lv_timer_t *timer;
} orb_t;

static void circle(lv_obj_t *o, int32_t d)
{
    lv_obj_set_size(o, d, d);
    lv_obj_align(o, LV_ALIGN_CENTER, 0, 0);
}

static lv_obj_t *mk_circle(lv_obj_t *parent)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_set_scrollable(o, false); lv_obj_set_clickable(o, false);
    lv_obj_set_style_radius(o, LV_RADIUS_CIRCLE, 0);
    return o;
}

static void paint_colors(orb_t *o)
{
    lv_color_t c1 = o->tone == T_LISTEN ? DOS_LIVE : DOS_ACCENT;
    lv_color_t c2 = o->tone == T_LISTEN ? DOS_ACCENT : DOS_LIVE;
    for(int i = 0; i < 3; i++) lv_obj_set_style_border_color(o->ring[i], c1, 0);

    lv_color_t hc[2] = { c1, c2 };
    lv_opa_t ho[2] = { 56, 0 };   /* 0.22 -> 0 */
    lv_grad_init_stops(&o->halo_g, hc, ho, NULL, 2);
    lv_grad_radial_init(&o->halo_g, LV_PCT(50), LV_PCT(50), LV_PCT(100), LV_PCT(50), LV_GRAD_EXTEND_PAD);
    lv_obj_set_style_bg_grad(o->halo, &o->halo_g, 0);

    lv_color_t cc[3] = { dos_mix(c1, DOS_WHITE, 52), c1, dos_mix(c2, lv_color_black(), 100) };
    uint8_t fr[3] = { 0, 140, 255 };
    lv_grad_init_stops(&o->core_g, cc, NULL, fr, 3);
    lv_grad_vertical_init(&o->core_g);
    lv_obj_set_style_bg_grad(o->core, &o->core_g, 0);
    lv_obj_set_style_bg_color(o->core, c1, 0);
    lv_obj_set_style_bg_opa(o->core, LV_OPA_COVER, 0);
    o->painted = o->tone;
}

static void tick_cb(lv_timer_t *t)
{
    orb_t *o = lv_timer_get_user_data(t);
    if(!lv_obj_is_visible(o->core)) return;
    if(o->tone != o->painted) paint_colors(o);
    float tt = (float)lv_tick_get() / 1000.0f;
    int32_t S = o->size;
    bool ripple = o->tone == T_SPEAK || o->tone == T_LISTEN;
    o->level_s += (o->level - o->level_s) * 0.4f;
    float lv = o->tone == T_LISTEN ? o->level_s : 0;

    for(int i = 0; i < 3; i++) {
        if(!ripple) { lv_obj_set_hidden(o->ring[i], true); continue; }
        float p = fmodf(tt + 10.0f - 0.42f * (float)i, 1.68f);   /* 1260 ms ripple, 420 ms rest, rings staggered by 420 ms */
        float u = p / 1.26f;
        if(u > 1.0f) { lv_obj_set_hidden(o->ring[i], true); continue; }
        float e = 1.0f - (1.0f - u) * (1.0f - u) * (1.0f - u);   /* OutCubic */
        int32_t d = (int32_t)((float)S * 0.5f * (1.0f + e));
        lv_obj_set_hidden(o->ring[i], false);
        circle(o->ring[i], d);
        lv_obj_set_style_border_opa(o->ring[i], (lv_opa_t)(115.0f * (1.0f - u)), 0);
    }
    circle(o->halo, (int32_t)((float)S * (0.8f + lv * 0.25f)));
    int32_t cd = (int32_t)((float)S * 0.46f * (1.0f + lv * 0.18f));
    circle(o->core, cd);
    lv_opa_t co = LV_OPA_COVER;
    if(o->tone == T_IDLE) co = (lv_opa_t)(255.0f * (0.89f + 0.11f * sinf(tt * 2.0f * 3.14159f / 3.6f)));
    lv_obj_set_style_opa(o->core, co, 0);
    lv_obj_set_pos(o->spec, 0, 0);
    int32_t sw = cd * 36 / 100;
    lv_obj_set_size(o->spec, sw, sw * 6 / 10);
    lv_obj_align_to(o->spec, o->core, LV_ALIGN_TOP_LEFT, cd * 20 / 100, cd * 14 / 100);
    if(o->tone == T_THINK) {
        lv_obj_set_hidden(o->arc, false);
        lv_obj_set_hidden(o->dot, false);
        int32_t ad = (int32_t)((float)S * 0.6f);
        circle(o->arc, ad);
        float ang = tt * 2.0f * 3.14159f / 1.1f;
        lv_obj_align(o->dot, LV_ALIGN_CENTER, (int32_t)(sinf(ang) * (float)ad / 2), (int32_t)(-cosf(ang) * (float)ad / 2));
    } else {
        lv_obj_set_hidden(o->arc, true);
        lv_obj_set_hidden(o->dot, true);
    }
}

static void del_cb(lv_event_t *e)
{
    orb_t *o = lv_event_get_user_data(e);
    lv_timer_delete(o->timer);
    free(o);
}

lv_obj_t *orb_create(lv_obj_t *parent, int32_t size)
{
    orb_t *o = calloc(1, sizeof *o);
    o->size = size;
    o->tone = T_IDLE;
    o->painted = T_SPEAK;
    lv_obj_t *root = lv_obj_create(parent);
    lv_obj_remove_style_all(root);
    lv_obj_set_scrollable(root, false); lv_obj_set_clickable(root, false);
    lv_obj_set_size(root, size, size);
    lv_obj_add_event_cb(root, del_cb, LV_EVENT_DELETE, o);
    lv_obj_set_user_data(root, o);

    for(int i = 0; i < 3; i++) {
        o->ring[i] = mk_circle(root);
        lv_obj_set_style_bg_opa(o->ring[i], LV_OPA_TRANSP, 0);
        lv_obj_set_style_border_width(o->ring[i], 2, 0);
        lv_obj_set_hidden(o->ring[i], true);
    }
    o->halo = mk_circle(root);
    lv_obj_set_style_bg_opa(o->halo, LV_OPA_COVER, 0);
    o->core = mk_circle(root);
    o->spec = lv_obj_create(o->core);
    lv_obj_remove_style_all(o->spec);
    lv_obj_set_scrollable(o->spec, false); lv_obj_set_clickable(o->spec, false); lv_obj_set_ignore_layout(o->spec, false);
    lv_obj_set_style_radius(o->spec, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(o->spec, DOS_WHITE, 0);
    lv_obj_set_style_bg_opa(o->spec, 72, 0);
    lv_obj_set_style_transform_rotation(o->spec, -280, 0);
    o->arc = mk_circle(root);
    lv_obj_set_style_bg_opa(o->arc, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(o->arc, 3, 0);
    lv_obj_set_style_border_color(o->arc, DOS_LIVE, 0);
    lv_obj_set_style_border_opa(o->arc, 64, 0);
    o->dot = mk_circle(root);
    lv_obj_set_size(o->dot, 12, 12);
    lv_obj_set_style_bg_color(o->dot, DOS_LIVE, 0);
    lv_obj_set_style_bg_opa(o->dot, LV_OPA_COVER, 0);
    paint_colors(o);
    o->timer = lv_timer_create(tick_cb, 33, o);
    return root;
}

void orb_set(lv_obj_t *root, const char *state, float level)
{
    orb_t *o = lv_obj_get_user_data(root);
    if(!o) return;
    o->tone = !strcmp(state, "listening") ? T_LISTEN : !strcmp(state, "thinking") ? T_THINK : !strcmp(state, "speaking") ? T_SPEAK : T_IDLE;
    o->level = level < 0 ? 0 : level > 1 ? 1 : level;
}
