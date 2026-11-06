#include "display.h"
#include "theme.h"
#include <SDL2/SDL.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static char s_name[32] = "?";
const char *dos_display_backend_name(void) { return s_name; }

static void headless_flush(lv_display_t *d, const lv_area_t *a, uint8_t *px)
{
    (void)a;
    (void)px;
    lv_display_flush_ready(d);
}

static lv_display_t *headless_create(int32_t w, int32_t h)
{
    lv_display_t *d = lv_display_create(w, h);
    uint32_t sz = (uint32_t)w * (uint32_t)h * 4u;
    void *buf = malloc(sz);
    if(!d || !buf) return NULL;
    lv_display_set_color_format(d, LV_COLOR_FORMAT_XRGB8888);
    lv_display_set_buffers(d, buf, NULL, sz, LV_DISPLAY_RENDER_MODE_FULL);
    lv_display_set_flush_cb(d, headless_flush);
    snprintf(s_name, sizeof s_name, "headless");
    return d;
}

static lv_display_t *sdl_create(const dos_display_cfg_t *c)
{
    /* A Wayland session first (the Pi's labwc), X11/Xwayland as the fallback; SDL ignores unknown names. */
    if(getenv("WAYLAND_DISPLAY") && !getenv("SDL_VIDEODRIVER")) SDL_SetHint(SDL_HINT_VIDEODRIVER, "wayland,x11");
    lv_display_t *d = lv_sdl_window_create(c->w, c->h);
    if(!d) return NULL;
    lv_sdl_window_set_title(d, "DriveOS");
    lv_sdl_window_set_resizeable(d, true);
    if(c->fullscreen) SDL_SetWindowFullscreen(lv_sdl_window_get_window(d), SDL_WINDOW_FULLSCREEN_DESKTOP);
    lv_indev_t *mouse = lv_sdl_mouse_create();
    lv_indev_set_display(mouse, d);
    lv_indev_t *kb = lv_sdl_keyboard_create();
    lv_indev_set_display(kb, d);
    const char *drv = SDL_GetCurrentVideoDriver();
    snprintf(s_name, sizeof s_name, "%s", drv ? drv : "sdl");
    return d;
}

#if DOS_WITH_DRM
static lv_display_t *drm_create(const dos_display_cfg_t *c)
{
    lv_display_t *d = lv_linux_drm_create();
    if(!d) return NULL;
    if(lv_linux_drm_set_file(d, c->drm_dev ? c->drm_dev : "/dev/dri/card0", -1) != LV_RESULT_OK) return NULL;
    lv_indev_t *t = lv_evdev_create(LV_INDEV_TYPE_POINTER, c->touch_dev ? c->touch_dev : "/dev/input/event0");
    if(t) lv_indev_set_display(t, d);
    snprintf(s_name, sizeof s_name, "drm");
    return d;
}
#endif

lv_display_t *dos_display_create(const dos_display_cfg_t *cfg)
{
    dos_display_cfg_t c = *cfg;
    if(c.w <= 0 || c.h <= 0) { c.w = 1280; c.h = 800; }
    dos_backend_t b = c.backend;
    if(b == DOS_BACKEND_AUTO) {
        bool session = getenv("WAYLAND_DISPLAY") || getenv("DISPLAY");
        b = (session || !DOS_WITH_DRM) ? DOS_BACKEND_SDL : DOS_BACKEND_DRM;
    }
    lv_display_t *d = NULL;
    if(b == DOS_BACKEND_HEADLESS) d = headless_create(c.w, c.h);
#if DOS_WITH_DRM
    else if(b == DOS_BACKEND_DRM) d = drm_create(&c);
#endif
    else if(b == DOS_BACKEND_SDL) d = sdl_create(&c);
    else fprintf(stderr, "driveos_hmi: this build has no DRM backend (libdrm-dev missing)\n");
    if(!d) return NULL;

    lv_obj_t *scr = lv_display_get_screen_active(d);
    lv_obj_set_style_bg_color(scr, DOS_BG0, 0);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);
    lv_obj_set_scrollable(scr, false);
    return d;
}
