#include "app.h"
#include "display.h"
#include "png_out.h"
#include "ui_state.h"
#include "ws_client.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <time.h>

typedef struct {
    dos_display_cfg_t disp;
    const char *url, *fixture, *screenshot;
    int screen;
    uint32_t quit_after_s, shot_after_ms;
} args_t;

static void usage(const char *a)
{
    fprintf(stderr,
            "usage: %s [--url ws://127.0.0.1:8790/v1/ui] [--fixture state.json] [--screen home|agent|shop|nav|about]\n"
            "          [--screenshot out.png [--shot-after MS]] [--backend auto|sdl|drm|headless] [--fullscreen] [--size WxH]\n"
            "          [--drm-dev /dev/dri/card0] [--touch-dev /dev/input/event0] [--quit-after SEC] [--version]\n"
            "  --fixture loads a state snapshot instead of connecting; --screenshot renders off-screen (headless) and exits.\n",
            a);
}

static bool parse_args(int argc, char **argv, args_t *a)
{
    const char *env = getenv("DRIVEOS_UI_URL");
    *a = (args_t){ .url = env ? env : "ws://127.0.0.1:8790/v1/ui", .screen = -1, .shot_after_ms = 700 };
    for(int i = 1; i < argc; i++) {
        const char *s = argv[i];
        bool more = i + 1 < argc;
        if(!strcmp(s, "--url") && more) a->url = argv[++i];
        else if(!strcmp(s, "--fixture") && more) a->fixture = argv[++i];
        else if(!strcmp(s, "--screenshot") && more) a->screenshot = argv[++i];
        else if(!strcmp(s, "--shot-after") && more) a->shot_after_ms = (uint32_t)strtoul(argv[++i], NULL, 10);
        else if(!strcmp(s, "--quit-after") && more) a->quit_after_s = (uint32_t)strtoul(argv[++i], NULL, 10);
        else if(!strcmp(s, "--fullscreen")) a->disp.fullscreen = true;
        else if(!strcmp(s, "--drm-dev") && more) a->disp.drm_dev = argv[++i];
        else if(!strcmp(s, "--touch-dev") && more) a->disp.touch_dev = argv[++i];
        else if(!strcmp(s, "--screen") && more) {
            a->screen = app_screen_from_name(argv[++i]);
            if(a->screen < 0) return false;
        }
        else if(!strcmp(s, "--backend") && more) {
            const char *b = argv[++i];
            a->disp.backend = !strcmp(b, "sdl") ? DOS_BACKEND_SDL : !strcmp(b, "drm") ? DOS_BACKEND_DRM
                            : !strcmp(b, "headless") ? DOS_BACKEND_HEADLESS : DOS_BACKEND_AUTO;
        }
        else if(!strcmp(s, "--size") && more) {
            int w, h;
            if(sscanf(argv[++i], "%dx%d", &w, &h) != 2 || w < 640 || h < 480) return false;
            a->disp.w = w;
            a->disp.h = h;
        }
        else if(!strcmp(s, "--version")) {
            printf("DriveOS HMI %s (build %s)\n", DRIVEOS_VERSION, DRIVEOS_BUILD);
            exit(0);
        }
        else return false;
    }
    return true;
}

static uint32_t tick_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)(ts.tv_sec * 1000u + ts.tv_nsec / 1000000u);
}

/* DOS_MAP_STATS=1: what the UI thread spends on drawing, next to the map worker's own line. */
static struct { uint32_t t0, start, frames; double busy_ms; } ui_st;

static void ui_refr_cb(lv_event_t *e)
{
    if(lv_event_get_code(e) == LV_EVENT_REFR_START) ui_st.start = lv_tick_get();
    else {
        ui_st.busy_ms += lv_tick_elaps(ui_st.start);
        ui_st.frames++;
    }
}

static void ui_stats_tick(void)
{
    uint32_t span = lv_tick_elaps(ui_st.t0);
    if(span < 5000) return;
    unsigned sn;
    double savg, smax;
    ui_state_take_parse_stats(&sn, &savg, &smax);
    struct rusage ru;
    getrusage(RUSAGE_SELF, &ru);
    double cpu_s = ru.ru_utime.tv_sec + ru.ru_stime.tv_sec + (ru.ru_utime.tv_usec + ru.ru_stime.tv_usec) / 1e6;
    static double cpu_prev;
    fprintf(stderr, "ui: %.1f refresh/s, %.1f ms each | snapshots %.1f/s, parse %.2f ms avg %.2f max | cpu %.0f%% of a core\n",
            ui_st.frames * 1000.0 / span, ui_st.frames ? ui_st.busy_ms / ui_st.frames : 0.0, sn * 1000.0 / span, savg,
            smax, 100.0 * (cpu_s - cpu_prev) / (span / 1000.0));
    cpu_prev = cpu_s;
    ui_st.t0 = lv_tick_get();
    ui_st.frames = 0;
    ui_st.busy_ms = 0;
}

int main(int argc, char **argv)
{
    args_t a;
    if(!parse_args(argc, argv, &a)) {
        usage(argv[0]);
        return 2;
    }
    if(a.screenshot) a.disp.backend = DOS_BACKEND_HEADLESS;
    if(a.fixture && !ui_state_load_fixture(a.fixture)) {
        fprintf(stderr, "driveos_hmi: cannot load fixture '%s'\n", a.fixture);
        return 2;
    }

    lv_init();
    lv_tick_set_cb(tick_ms);
    lv_display_t *disp = dos_display_create(&a.disp);
    if(!disp) {
        fprintf(stderr, "driveos_hmi: display init failed\n");
        return 1;
    }
    printf("driveos_hmi %s (build %s) backend=%s display=%dx%d %s\n", DRIVEOS_VERSION, DRIVEOS_BUILD, dos_display_backend_name(),
           (int)lv_display_get_horizontal_resolution(disp), (int)lv_display_get_vertical_resolution(disp),
           a.fixture ? a.fixture : a.url);
    fflush(stdout);

    if(!a.fixture && !link_start(a.url)) {
        fprintf(stderr, "driveos_hmi: bad --url '%s'\n", a.url);
        return 2;
    }
    app_build(lv_screen_active(), &(app_cfg_t){ .url = a.fixture ? NULL : a.url,
                                                .initial = a.screen < 0 ? SCR_HOME : (screen_id_t)a.screen,
                                                .pin_initial = a.screen >= 0 });

    const char *stats = getenv("DOS_MAP_STATS");
    bool ui_stats = stats && *stats && strcmp(stats, "0") != 0;
    if(ui_stats) {
        ui_st.t0 = lv_tick_get();
        lv_display_add_event_cb(disp, ui_refr_cb, LV_EVENT_REFR_START, NULL);
        lv_display_add_event_cb(disp, ui_refr_cb, LV_EVENT_REFR_READY, NULL);
    }
    uint32_t start = lv_tick_get();
    bool shot_done = false;
    const char *shot_dir = getenv("DOS_SHOT_DIR");
    const char *shot_ms = getenv("DOS_SHOT_EVERY_MS");
    uint32_t shot_every = shot_ms ? (uint32_t)strtoul(shot_ms, NULL, 10) : 1000, last_shot = start;
    for(;;) {
        uint32_t t = lv_timer_handler();
        if(ui_stats) ui_stats_tick();
        if(shot_dir && lv_tick_elaps(last_shot) >= shot_every) {   /* DOS_SHOT_DIR + DOS_SHOT_EVERY_MS: a run's frames */
            char path[512];
            snprintf(path, sizeof path, "%s/%06u.png", shot_dir, (unsigned)lv_tick_elaps(start));
            png_snapshot(lv_screen_active(), path);
            last_shot = lv_tick_get();
        }
        if(a.screenshot && !shot_done && lv_tick_elaps(start) >= a.shot_after_ms) {
            bool ok = png_snapshot(lv_screen_active(), a.screenshot);
            printf("screenshot %s %s\n", a.screenshot, ok ? "written" : "FAILED");
            fflush(stdout);
            return ok ? 0 : 1;
        }
        if(app_quit_requested() || (a.quit_after_s && lv_tick_elaps(start) >= a.quit_after_s * 1000u)) break;
        lv_delay_ms(t < 3 ? 3 : (t > 20 ? 20 : t));
    }
    link_stop();
    return 0;
}
