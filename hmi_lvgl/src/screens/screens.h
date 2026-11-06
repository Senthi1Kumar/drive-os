#pragma once
#include "lvgl.h"
#include "theme.h"
#include "ui_state.h"

/* Each screen builds once into its slot (a full-size scrollable column) and is refreshed from the state mirror. */
void home_build(lv_obj_t *slot);
void home_update(const ui_state_t *s);
void agent_build(lv_obj_t *slot);
void agent_update(const ui_state_t *s);
void about_build(lv_obj_t *slot);
void about_update(const ui_state_t *s);
/* Shop / Nav: empty until their tasks land. */
void placeholder_build(lv_obj_t *slot, const char *label, const char *title, const char *sub, dos_icon_t icon);

/* the slot every screen starts from: column flex, 16 px side padding, room for the dock below */
lv_obj_t *screen_column(lv_obj_t *slot, int32_t pad_top);

/* Shop tab: places, menu, cart, order (switched by shop.view). Local UI state: active menu section. */
void shop_build(lv_obj_t *slot);
void shop_update(const ui_state_t *s);
void order_fill(lv_obj_t *col, const ui_state_t *s);   /* the order status view, rebuilt by shop.c */
void nav_build(lv_obj_t *slot);
void nav_update(const ui_state_t *s);
void nav_set_visible(bool on);   /* the map renders only while the Nav tab is shown */
/* Overlays above the dock: item sheet and checkout sheet. */
void sheets_build(lv_obj_t *stage);
void sheets_update(const ui_state_t *s);
void app_refresh_async(void);
void enroll_build(lv_obj_t *slot);
void enroll_update(const ui_state_t *s);
void enroll_reset(void);
