#pragma once
#include "lvgl.h"

/* The only module that knows about the window system. Backends:
   sdl      SDL2 window; SDL picks native Wayland on the Pi's labwc session (WAYLAND_DISPLAY set), else X11 (laptop dev)
   drm      LVGL's DRM/KMS + evdev touch/mouse on a bare console (needs libdrm at build time)
   headless an off-screen full-frame buffer, for --screenshot and tests
   auto     sdl when a Wayland/X11 session is present, else drm when built in, else sdl */
typedef enum { DOS_BACKEND_AUTO, DOS_BACKEND_SDL, DOS_BACKEND_DRM, DOS_BACKEND_HEADLESS } dos_backend_t;

typedef struct {
    dos_backend_t backend;
    int32_t w, h;          /* 0 = 1280x800 */
    bool fullscreen;
    const char *drm_dev;   /* default /dev/dri/card0 */
    const char *touch_dev; /* evdev for drm, default /dev/input/event0 */
} dos_display_cfg_t;

lv_display_t *dos_display_create(const dos_display_cfg_t *cfg);
const char *dos_display_backend_name(void);   /* "wayland" | "x11" | "drm" | "headless" | SDL driver name */
