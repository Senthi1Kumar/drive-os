#pragma once
#include "lvgl.h"

typedef enum { SCR_HOME, SCR_AGENT, SCR_SHOP, SCR_NAV, SCR_ABOUT, SCR_ENROLL, SCR_COUNT } screen_id_t;

typedef struct {
    const char *url;      /* NULL in fixture mode */
    screen_id_t initial;
    bool pin_initial;     /* ignore the state's tab until the user or the server changes it (screenshots) */
} app_cfg_t;

/* Build the shell (top bar, screens, dock, overlays) on `root`, start the link timers. */
void app_build(lv_obj_t *root, const app_cfg_t *cfg);
void app_show(screen_id_t id);
int app_screen_from_name(const char *name);   /* home|agent|shop|nav|about -> id, -1 unknown */
void app_refresh(void);
void app_refresh_async(void);                 /* from inside event handlers: refresh on the next timer tick */                       /* re-render from the state mirror now */
void app_request_quit(void);
bool app_quit_requested(void);
/* Overlays (item sheet, checkout) live above the dock; built by sheets.c */
