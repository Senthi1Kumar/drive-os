#include "ui_state.h"
#include "json.h"
#include "map/lane_layout.h"
#include "map/polyline.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static ui_state_t S;
static char s_err[128];

const ui_state_t *ui_state(void) { return &S; }
const char *ui_state_last_error(void) { return s_err; }

static void cp(char *dst, size_t cap, const cbj_t *n, const char *def)
{
    const char *s = cbj_str(n, def);
    snprintf(dst, cap, "%s", s ? s : "");
}

/* money arrives as dollars plus a "<key>_text" display string; render the string, fall back to the number */
static void money(char *dst, size_t cap, const cbj_t *obj, const char *key)
{
    char k[32];
    snprintf(k, sizeof k, "%s_text", key);
    const cbj_t *t = cbj_get(obj, k);
    if(t && t->type == CBJ_STR) snprintf(dst, cap, "%s", t->s);
    else snprintf(dst, cap, "$%.2f", cbj_num(cbj_get(obj, key), 0));
}

static void parse_voice(const cbj_t *v)
{
    cp(S.voice.state, sizeof S.voice.state, cbj_get(v, "state"), "idle");
    S.voice.level = (float)cbj_num(cbj_get(v, "level"), 0);
    cp(S.voice.partial, sizeof S.voice.partial, cbj_get(v, "partial"), "");
    cp(S.voice.heard, sizeof S.voice.heard, cbj_get(v, "heard"), "");
    cp(S.voice.reply, sizeof S.voice.reply, cbj_get(v, "reply"), "");
    S.voice.agent_offline = cbj_bool(cbj_get(cbj_get(v, "agent"), "offline"), false);
}

static void parse_item_detail(const cbj_t *it)
{
    S.shop.item.open = it && it->type == CBJ_OBJ;
    if(!S.shop.item.open) return;
    cp(S.shop.item.id, sizeof S.shop.item.id, cbj_get(it, "id"), "");
    cp(S.shop.item.name, sizeof S.shop.item.name, cbj_get(it, "name"), "");
    cp(S.shop.item.desc, sizeof S.shop.item.desc, cbj_get(it, "desc"), "");
    cp(S.shop.item.price_text, sizeof S.shop.item.price_text, cbj_get(it, "price_text"), "");
    cp(S.shop.item.total_text, sizeof S.shop.item.total_text, cbj_get(it, "total_text"), "");
    cp(S.shop.item.img_lg, sizeof S.shop.item.img_lg, cbj_get(it, "img_lg"), "");
    S.shop.item.qty = (int)cbj_num(cbj_get(it, "qty"), 1);
    const cbj_t *gs = cbj_get(it, "groups");
    for(int i = 0; gs && i < gs->count && i < UI_GROUPS_MAX; i++) {
        const cbj_t *g = cbj_at(gs, i);
        ui_group_t *o = &S.shop.item.groups[S.shop.item.n_groups++];
        cp(o->id, sizeof o->id, cbj_get(g, "id"), "");
        cp(o->name, sizeof o->name, cbj_get(g, "name"), "");
        const cbj_t *os = cbj_get(g, "options");
        for(int j = 0; os && j < os->count && j < UI_OPTIONS_MAX; j++) {
            const cbj_t *op = cbj_at(os, j);
            ui_option_t *p = &o->options[o->n++];
            cp(p->id, sizeof p->id, cbj_get(op, "id"), "");
            cp(p->name, sizeof p->name, cbj_get(op, "name"), "");
            cp(p->delta, sizeof p->delta, cbj_get(op, "delta"), "");
            p->on = cbj_bool(cbj_get(op, "on"), false);
            cp(p->img, sizeof p->img, cbj_get(op, "img"), "");
        }
    }
}

static ui_auth_stage_t auth_stage(const char *s)
{
    if(!strcmp(s, "challenge")) return UI_AUTH_CHALLENGE;
    if(!strcmp(s, "face")) return UI_AUTH_FACE;
    if(!strcmp(s, "otp")) return UI_AUTH_OTP;
    if(!strcmp(s, "verifying")) return UI_AUTH_VERIFYING;
    if(!strcmp(s, "accepted")) return UI_AUTH_ACCEPTED;
    if(!strcmp(s, "rejected")) return UI_AUTH_REJECTED;
    return UI_AUTH_NONE;
}

static void parse_home(const cbj_t *h)
{
    memset(&S.home, 0, sizeof S.home);
    const cbj_t *pl = cbj_get(h, "places");
    for(int i = 0; pl && i < pl->count && i < 8; i++) {
        const cbj_t *p = cbj_at(pl, i);
        ui_home_place_t *o = &S.home.places[S.home.n++];
        cp(o->id, sizeof o->id, cbj_get(p, "id"), "");
        cp(o->name, sizeof o->name, cbj_get(p, "name"), "");
        cp(o->category, sizeof o->category, cbj_get(p, "category"), "");
        cp(o->distance_text, sizeof o->distance_text, cbj_get(p, "distance_text"), "");
        cp(o->img, sizeof o->img, cbj_get(p, "img"), "");
        cp(o->img_lg, sizeof o->img_lg, cbj_get(p, "img_lg"), "");
        o->orderable = cbj_bool(cbj_get(p, "orderable"), true);
        o->sample = cbj_bool(cbj_get(p, "sample"), false);
    }
}

static void parse_shop(const cbj_t *sh)
{
    memset(&S.shop, 0, sizeof S.shop);
    cp(S.shop.view, sizeof S.shop.view, cbj_get(sh, "view"), "idle");
    cp(S.shop.status, sizeof S.shop.status, cbj_get(sh, "status"), "");
    const cbj_t *pl = cbj_get(sh, "places");
    for(int i = 0; pl && i < pl->count && i < UI_PLACES_MAX; i++) {
        const cbj_t *p = cbj_at(pl, i);
        ui_place_t *o = &S.shop.places[S.shop.n_places++];
        cp(o->id, sizeof o->id, cbj_get(p, "id"), "");
        cp(o->name, sizeof o->name, cbj_get(p, "name"), "");
        cp(o->category, sizeof o->category, cbj_get(p, "category"), "");
        cp(o->address, sizeof o->address, cbj_get(p, "address"), "");
        cp(o->distance_text, sizeof o->distance_text, cbj_get(p, "distance_text"), "");
        memcpy(o->meta, o->distance_text, sizeof o->meta < sizeof o->distance_text ? sizeof o->meta : sizeof o->distance_text);
        o->eta_min = (int)cbj_num(cbj_get(p, "eta_min"), 0);
        if(o->eta_min > 0) {
            size_t l = strlen(o->meta);
            snprintf(o->meta + l, sizeof o->meta - l, "%s%d min", l ? "  ·  " : "", o->eta_min);
        }
        o->orderable = cbj_bool(cbj_get(p, "orderable"), true);
        o->sample = cbj_bool(cbj_get(p, "sample"), false);
        cp(o->img, sizeof o->img, cbj_get(p, "img"), "");
        cp(o->img_lg, sizeof o->img_lg, cbj_get(p, "img_lg"), "");
    }
    S.shop.sample = cbj_bool(cbj_get(sh, "sample"), false);
    const cbj_t *pc = cbj_get(sh, "place");
    cp(S.shop.place_id, sizeof S.shop.place_id, cbj_get(pc, "id"), "");
    cp(S.shop.place_name, sizeof S.shop.place_name, cbj_get(pc, "name"), "");

    const cbj_t *menu = cbj_get(sh, "menu");
    for(int i = 0; menu && i < menu->count && i < UI_SECTIONS_MAX; i++) {
        const cbj_t *sec = cbj_at(menu, i);
        int si = S.shop.n_sections++;
        cp(S.shop.sections[si], sizeof S.shop.sections[0], cbj_get(sec, "section"), "Menu");
        const cbj_t *its = cbj_get(sec, "items");
        for(int j = 0; its && j < its->count && S.shop.n_items < UI_ITEMS_MAX; j++) {
            const cbj_t *it = cbj_at(its, j);
            ui_menu_item_t *o = &S.shop.items[S.shop.n_items++];
            o->section = si;
            cp(o->id, sizeof o->id, cbj_get(it, "id"), "");
            cp(o->name, sizeof o->name, cbj_get(it, "name"), "");
            cp(o->price_text, sizeof o->price_text, cbj_get(it, "price_text"), "");
            cp(o->desc, sizeof o->desc, cbj_get(it, "desc"), "");
            o->addable = cbj_bool(cbj_get(it, "addable"), true);
            cp(o->img, sizeof o->img, cbj_get(it, "img"), "");
            cp(o->img_lg, sizeof o->img_lg, cbj_get(it, "img_lg"), "");
        }
    }
    parse_item_detail(cbj_get(sh, "item"));

    const cbj_t *cart = cbj_get(sh, "cart");
    const cbj_t *lines = cbj_get(cart, "lines");
    for(int i = 0; lines && i < lines->count && i < UI_LINES_MAX; i++) {
        const cbj_t *l = cbj_at(lines, i);
        ui_line_t *o = &S.shop.lines[S.shop.cart_lines++];
        cp(o->id, sizeof o->id, cbj_get(l, "id"), "");
        cp(o->name, sizeof o->name, cbj_get(l, "name"), "");
        money(o->price_text, sizeof o->price_text, l, "price");
        o->qty = (int)cbj_num(cbj_get(l, "qty"), 1);
        cp(o->img, sizeof o->img, cbj_get(l, "img"), "");
        cp(o->opts, sizeof o->opts, cbj_get(l, "opts"), "");
        S.shop.cart_count += o->qty;
    }
    cp(S.shop.cart_place, sizeof S.shop.cart_place, cbj_get(cart, "place"), "");
    S.shop.subtotal = cbj_num(cbj_get(cart, "subtotal"), 0);
    S.shop.tax = cbj_num(cbj_get(cart, "tax"), 0);
    S.shop.total = cbj_num(cbj_get(cart, "total"), 0);
    money(S.shop.cart_total_text, sizeof S.shop.cart_total_text, cart, "total");

    const cbj_t *co = cbj_get(sh, "checkout");
    S.shop.checkout_pending = cbj_bool(cbj_get(co, "pending"), false);
    money(S.shop.checkout_amount_text, sizeof S.shop.checkout_amount_text, co, "amount");
    cp(S.shop.checkout_merchant, sizeof S.shop.checkout_merchant, cbj_get(co, "merchant"), "");
    const cbj_t *ms = cbj_get(co, "methods");
    for(int i = 0; ms && i < ms->count; i++) {
        const char *m = cbj_str(cbj_at(ms, i), "");
        if(!strcmp(m, "voice")) S.shop.method_voice = true;
        else if(!strcmp(m, "face")) S.shop.method_face = true;
        else if(!strcmp(m, "code") || !strcmp(m, "otp")) S.shop.method_code = true;
    }
    const cbj_t *mi = cbj_get(co, "method_info");
    S.shop.info_present = mi && mi->type == CBJ_OBJ;
    S.shop.face_available = true;
    if(S.shop.info_present) {
        const cbj_t *v = cbj_get(mi, "voice"), *f = cbj_get(mi, "face"), *c = cbj_get(mi, "code");
        S.shop.voice_consent = !strcmp(cbj_str(cbj_get(v, "mode"), ""), "consent");
        cp(S.shop.voice_sub, sizeof S.shop.voice_sub, cbj_get(v, "subtitle"), "");
        S.shop.face_available = cbj_bool(cbj_get(f, "available"), true);
        cp(S.shop.face_sub, sizeof S.shop.face_sub, cbj_get(f, "subtitle"), "");
        cp(S.shop.code_sub, sizeof S.shop.code_sub, cbj_get(c, "subtitle"), "");
    }
    const cbj_t *au = cbj_get(co, "auth");
    S.shop.auth_stage = auth_stage(cbj_str(cbj_get(au, "stage"), "none"));
    cp(S.shop.auth_challenge, sizeof S.shop.auth_challenge, cbj_get(au, "challenge"), "");
    cp(S.shop.auth_detail, sizeof S.shop.auth_detail, cbj_get(au, "detail"), "");
    S.shop.auth_preview = cbj_bool(cbj_get(au, "preview"), false);
    S.shop.voice_entry = cbj_bool(cbj_get(au, "voice_entry"), false);
    cp(S.shop.face_state, sizeof S.shop.face_state, cbj_get(au, "face_state"), "none");
    cp(S.shop.face_name, sizeof S.shop.face_name, cbj_get(au, "face_name"), "");
    {   /* the coarse rectangle lives outside shop (and its hash): it changes too often to rebuild the sheet for */
        const cbj_t *fr = cbj_get(au, "face_rect");
        S.face_rect.valid = fr && fr->type == CBJ_OBJ;
        if(S.face_rect.valid) {
            S.face_rect.x = (float)cbj_num(cbj_get(fr, "x"), 0);
            S.face_rect.y = (float)cbj_num(cbj_get(fr, "y"), 0);
            S.face_rect.w = (float)cbj_num(cbj_get(fr, "w"), 0);
            S.face_rect.h = (float)cbj_num(cbj_get(fr, "h"), 0);
        }
    }

    const cbj_t *od = cbj_get(sh, "order");
    S.shop.order.present = od && od->type == CBJ_OBJ;
    if(S.shop.order.present) {
        cp(S.shop.order.id, sizeof S.shop.order.id, cbj_get(od, "id"), "");
        cp(S.shop.order.status, sizeof S.shop.order.status, cbj_get(od, "status"), "");
        cp(S.shop.order.status_text, sizeof S.shop.order.status_text, cbj_get(od, "status_text"), "");
        cp(S.shop.order.pickup_code, sizeof S.shop.order.pickup_code, cbj_get(od, "pickup_code"), "");
        cp(S.shop.order.merchant, sizeof S.shop.order.merchant, cbj_get(od, "merchant"), "");
        S.shop.order.terminal = cbj_bool(cbj_get(od, "terminal"), false);
        S.shop.order.eta_min = cbj_num(cbj_get(od, "eta_min"), -1);
        const cbj_t *tl = cbj_get(od, "timeline");
        for(int i = 0; tl && i < tl->count && i < UI_STEPS_MAX; i++) {
            const cbj_t *t = cbj_at(tl, i);
            ui_step_t *o = &S.shop.order.steps[S.shop.order.n_steps++];
            cp(o->state, sizeof o->state, cbj_get(t, "state"), "");
            cp(o->label, sizeof o->label, cbj_get(t, "label"), "");
            cp(o->phase, sizeof o->phase, cbj_get(t, "phase"), "next");
            cp(o->at, sizeof o->at, cbj_get(t, "at"), "");
        }
    }
}

/* The route geometry rides along on every snapshot; decode it only when the text changed. */
static void parse_route(const cbj_t *nv)
{
    const cbj_t *ro = cbj_get(nv, "route");
    S.nav.route_id = (uint32_t)cbj_num(cbj_get(ro, "id"), 0);
    cp(S.nav.route_reason, sizeof S.nav.route_reason, cbj_get(ro, "reason"), "");
    const char *enc = cbj_str(cbj_get(ro, "polyline6"), "");
    uint32_t h = 2166136261u;
    for(const char *c = enc; *c; c++) h = (h ^ (unsigned char)*c) * 16777619u;
    if(!*enc) h = 0;
    if(h != S.nav.route_hash || (h && !S.nav.route_ll)) {
        free(S.nav.route_ll);
        S.nav.route_ll = NULL;
        S.nav.route_n = 0;
        if(*enc) {
            S.nav.route_ll = malloc(sizeof *S.nav.route_ll * UI_ROUTE_MAX);
            if(S.nav.route_ll) S.nav.route_n = dos_polyline6_decode(enc, strlen(enc), S.nav.route_ll, UI_ROUTE_MAX);
            if(S.nav.route_n < 2) { free(S.nav.route_ll); S.nav.route_ll = NULL; S.nav.route_n = 0; }
        }
        S.nav.route_hash = h;
        S.nav.route_ver++;
    }
    const cbj_t *cg = cbj_get(nv, "congestion");
    S.nav.congestion[0] = 0;
    if(cg && cg->type == CBJ_STR) cp(S.nav.congestion, sizeof S.nav.congestion, cg, "");
    else if(cg && cg->type == CBJ_ARR) {   /* ["low","heavy",...] or ["l","h",...]: the first letter of each */
        int n = 0;
        for(int i = 0; i < cg->count && n < UI_CONGESTION_MAX - 1; i++) {
            const char *w = cbj_str(cbj_at(cg, i), "");
            S.nav.congestion[n++] = *w ? *w : 'u';
        }
        S.nav.congestion[n] = 0;
    }
}

static void parse_state(const cbj_t *st)
{
    cp(S.tab, sizeof S.tab, cbj_get(st, "tab"), "home");
    parse_voice(cbj_get(st, "voice"));
    const cbj_t *ve = cbj_get(st, "vehicle");
    S.vehicle.speed_mps = (float)cbj_num(cbj_get(ve, "speed_mps"), 0);
    S.vehicle.parked = cbj_bool(cbj_get(ve, "parked"), true);
    S.vehicle.lat = cbj_num(cbj_get(ve, "lat"), 0);
    S.vehicle.lon = cbj_num(cbj_get(ve, "lon"), 0);
    S.vehicle.heading = (float)cbj_num(cbj_get(ve, "heading"), 0);
    S.vehicle.t_ms = cbj_num(cbj_get(ve, "t_ms"), 0);
    cp(S.vehicle.label, sizeof S.vehicle.label, cbj_get(ve, "label"), "");
    cp(S.vehicle.source, sizeof S.vehicle.source, cbj_get(ve, "source"), "");
    cp(S.vehicle.source_text, sizeof S.vehicle.source_text, cbj_get(ve, "source_text"), "");
    parse_home(cbj_get(st, "home"));
    parse_shop(cbj_get(st, "shop"));
    const cbj_t *nv = cbj_get(st, "nav");
    S.nav.active = cbj_bool(cbj_get(nv, "active"), false);
    cp(S.nav.dest, sizeof S.nav.dest, cbj_get(nv, "dest"), "");
    cp(S.nav.maneuver, sizeof S.nav.maneuver, cbj_get(nv, "maneuver"), "");
    S.nav.eta_min = cbj_num(cbj_get(nv, "eta_min"), -1);
    cp(S.nav.mode, sizeof S.nav.mode, cbj_get(nv, "mode"), "idle");
    parse_route(nv);
    const cbj_t *sp = cbj_get(nv, "step");
    cp(S.nav.step_icon, sizeof S.nav.step_icon, cbj_get(sp, "icon"), "");
    cp(S.nav.step_text, sizeof S.nav.step_text, cbj_get(sp, "text"), "");
    S.nav.step_dist_m = cbj_num(cbj_get(sp, "dist_m"), -1);
    const cbj_t *ln = cbj_get(sp, "lanes");
    S.nav.n_lanes = 0;
    for(int i = 0; ln && ln->type == CBJ_ARR && i < ln->count && S.nav.n_lanes < UI_LANES_MAX; i++) {
        const cbj_t *l = cbj_at(ln, i), *ind = cbj_get(l, "indications");
        int k = S.nav.n_lanes++;
        S.nav.lanes[k].ind = 0;
        for(int j = 0; ind && ind->type == CBJ_ARR && j < ind->count; j++) {
            int d = dos_lane_dir(cbj_str(cbj_at(ind, j), ""));
            if(d >= 0) S.nav.lanes[k].ind |= (uint16_t)(1u << d);
        }
        S.nav.lanes[k].active = (int8_t)dos_lane_dir(cbj_str(cbj_get(l, "active_indication"), ""));
        S.nav.lanes[k].valid = cbj_bool(cbj_get(l, "valid"), false);
    }
    const cbj_t *to = cbj_get(st, "toast");
    S.toast.active = to && to->type == CBJ_OBJ;
    cp(S.toast.text, sizeof S.toast.text, cbj_get(to, "text"), "");
    cp(S.toast.kind, sizeof S.toast.kind, cbj_get(to, "kind"), "info");
    const cbj_t *ph = cbj_get(st, "phone");
    memset(&S.phone, 0, sizeof S.phone);
    S.phone.active = ph && ph->type == CBJ_OBJ;
    if(S.phone.active) {
        cp(S.phone.title, sizeof S.phone.title, cbj_get(ph, "title"), "Messages");
        cp(S.phone.text, sizeof S.phone.text, cbj_get(ph, "text"), "");
        cp(S.phone.code, sizeof S.phone.code, cbj_get(ph, "code"), "");
    }
    memset(&S.enroll, 0, sizeof S.enroll);
    const cbj_t *en = cbj_get(st, "enroll");
    S.enroll.active = en && en->type == CBJ_OBJ;
    if(S.enroll.active) {
        cp(S.enroll.stage, sizeof S.enroll.stage, cbj_get(en, "stage"), "ready");
        S.enroll.take = (int)cbj_num(cbj_get(en, "take"), 0);
        S.enroll.total = (int)cbj_num(cbj_get(en, "total"), 0);
        S.enroll.kept = (int)cbj_num(cbj_get(en, "kept"), 0);
        cp(S.enroll.prompt, sizeof S.enroll.prompt, cbj_get(en, "prompt"), "");
        cp(S.enroll.error, sizeof S.enroll.error, cbj_get(en, "error"), "");
        const cbj_t *q = cbj_get(en, "quality");
        for(int i = 0; q && i < q->count && i < 6; i++) {
            const cbj_t *t = cbj_at(q, i);
            S.enroll.q[i].duration_s = (float)cbj_num(cbj_get(t, "duration_s"), 0);
            S.enroll.q[i].snr_db = (float)cbj_num(cbj_get(t, "snr_db"), 0);
            S.enroll.q[i].clipping_pct = (float)cbj_num(cbj_get(t, "clipping_pct"), 0);
            S.enroll.q[i].outlier = cbj_bool(cbj_get(t, "outlier"), false);
            S.enroll.q[i].ok = cbj_bool(cbj_get(t, "ok"), true);
            S.enroll.n_q++;
        }
    }
    memset(&S.face_id, 0, sizeof S.face_id);
    const cbj_t *fid = cbj_get(st, "face_id");
    S.face_id.enrolled = cbj_bool(cbj_get(fid, "enrolled"), false);
    S.face_id.linked = cbj_bool(cbj_get(fid, "linked"), false);
    cp(S.face_id.name, sizeof S.face_id.name, cbj_get(fid, "name"), "");
    memset(&S.enroll_face, 0, sizeof S.enroll_face);
    const cbj_t *ef = cbj_get(st, "enroll_face");
    S.enroll_face.active = ef && ef->type == CBJ_OBJ;
    if(S.enroll_face.active) {
        cp(S.enroll_face.operation, sizeof S.enroll_face.operation, cbj_get(ef, "operation"), "enroll");
        cp(S.enroll_face.stage, sizeof S.enroll_face.stage, cbj_get(ef, "stage"), "capturing");
        cp(S.enroll_face.name, sizeof S.enroll_face.name, cbj_get(ef, "name"), "");
        cp(S.enroll_face.error, sizeof S.enroll_face.error, cbj_get(ef, "error"), "");
    }
    memset(&S.face_profiles, 0, sizeof S.face_profiles);
    const cbj_t *fps = cbj_get(st, "face_profiles");
    cp(S.face_profiles.selected_id, sizeof S.face_profiles.selected_id, cbj_get(fps, "selected_id"), "");
    S.face_profiles.max = (int)cbj_num(cbj_get(fps, "max"), 5);
    const cbj_t *items = cbj_get(fps, "items");
    for(int i = 0; items && i < items->count && i < 5; i++) {
        const cbj_t *item = cbj_at(items, i);
        if(!item || item->type != CBJ_OBJ) continue;
        __typeof__(S.face_profiles.items[0]) *p = &S.face_profiles.items[S.face_profiles.n++];
        cp(p->id, sizeof p->id, cbj_get(item, "id"), "");
        cp(p->name, sizeof p->name, cbj_get(item, "name"), "");
        cp(p->role, sizeof p->role, cbj_get(item, "role"), "driver");
        p->linked = cbj_bool(cbj_get(item, "linked"), false);
    }
    const cbj_t *up = cbj_get(st, "update");
    S.update.available = cbj_bool(cbj_get(up, "available"), false);
    cp(S.update.version, sizeof S.update.version, cbj_get(up, "version"), "");
    const cbj_t *hl = cbj_get(up, "highlights");
    S.update.n = 0;
    for(int i = 0; hl && i < hl->count && i < UI_HIGHLIGHTS_MAX; i++)
        cp(S.update.highlights[S.update.n++], sizeof S.update.highlights[0], cbj_at(hl, i), "");
}

static void parse_hello(const cbj_t *h)
{
    cp(S.hello.app, sizeof S.hello.app, cbj_get(h, "app"), "DriveOS");
    cp(S.hello.version, sizeof S.hello.version, cbj_get(h, "version"), "");
    cp(S.hello.build, sizeof S.hello.build, cbj_get(h, "build"), "");
    cp(S.hello.image_root, sizeof S.hello.image_root, cbj_get(h, "image_root"), "");
    S.hello.have = true;
}

static struct { unsigned n; double sum_ms, max_ms; } s_parse;

void ui_state_take_parse_stats(unsigned *n, double *avg_ms, double *max_ms)
{
    *n = s_parse.n;
    *avg_ms = s_parse.n ? s_parse.sum_ms / s_parse.n : 0;
    *max_ms = s_parse.max_ms;
    s_parse.n = 0;
    s_parse.sum_ms = s_parse.max_ms = 0;
}

static bool apply(const char *text, size_t len);

bool ui_state_apply(const char *text, size_t len)
{
    struct timespec a, b;
    clock_gettime(CLOCK_MONOTONIC, &a);
    bool r = apply(text, len);
    clock_gettime(CLOCK_MONOTONIC, &b);
    double ms = (b.tv_sec - a.tv_sec) * 1e3 + (b.tv_nsec - a.tv_nsec) / 1e6;
    s_parse.n++;
    s_parse.sum_ms += ms;
    if(ms > s_parse.max_ms) s_parse.max_ms = ms;
    return r;
}

static bool apply(const char *text, size_t len)
{
    cbj_t *root = cbj_parse(text, len);
    if(!root) return false;
    bool changed = false;
    const char *type = cbj_str(cbj_get(root, "type"), "");
    if(!strcmp(type, "hello")) {
        parse_hello(root);
        changed = true;
    }
    else if(!strcmp(type, "state")) {
        const cbj_t *st = cbj_get(root, "state");
        if(st) {
            S.seq = (uint32_t)cbj_num(cbj_get(root, "seq"), S.seq);
            parse_state(st);
            changed = true;
        }
    }
    else if(!strcmp(type, "ack")) {
        if(!cbj_bool(cbj_get(root, "ok"), true)) {
            cp(s_err, sizeof s_err, cbj_get(root, "error"), "command failed");
            snprintf(S.toast.text, sizeof S.toast.text, "%s", s_err);
            snprintf(S.toast.kind, sizeof S.toast.kind, "danger");
            S.toast.active = true;
            changed = true;
        }
    }
    if(changed) S.rev++;
    cbj_free(root);
    return changed;
}

bool ui_state_load_fixture(const char *path)
{
    FILE *f = fopen(path, "rb");
    if(!f) return false;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = malloc((size_t)n + 1);
    if(!buf || fread(buf, 1, (size_t)n, f) != (size_t)n) {
        fclose(f);
        free(buf);
        return false;
    }
    fclose(f);
    buf[n] = 0;
    cbj_t *root = cbj_parse(buf, (size_t)n);
    free(buf);
    if(!root) return false;
    const cbj_t *hello = cbj_get(root, "hello");
    if(hello) parse_hello(hello);
    const cbj_t *st = cbj_get(root, "state");
    parse_state(st ? st : root);
    S.fixture = true;
    S.connected = true;
    S.rev++;
    cbj_free(root);
    return true;
}

void ui_state_set_connected(bool on)
{
    if(S.connected != on) {
        S.connected = on;
        S.rev++;
    }
}

void ui_state_set_tab(const char *tab)
{
    if(strcmp(S.tab, tab)) {
        snprintf(S.tab, sizeof S.tab, "%s", tab);
        S.rev++;
    }
}

uint32_t ui_shop_hash(void)
{
    const uint8_t *p = (const uint8_t *)&S.shop;
    uint32_t h = 2166136261u;
    for(size_t i = 0; i < sizeof S.shop; i++) h = (h ^ p[i]) * 16777619u;
    return h;
}

uint32_t ui_enroll_hash(void)
{
    uint32_t h = 2166136261u;
    const uint8_t *blocks[] = { (const uint8_t *)&S.enroll, (const uint8_t *)&S.face_id,
                                (const uint8_t *)&S.face_profiles, (const uint8_t *)&S.enroll_face };
    const size_t sizes[] = { sizeof S.enroll, sizeof S.face_id, sizeof S.face_profiles, sizeof S.enroll_face };
    for(size_t b = 0; b < 4; b++)
        for(size_t i = 0; i < sizes[b]; i++) h = (h ^ blocks[b][i]) * 16777619u;
    return h;
}
