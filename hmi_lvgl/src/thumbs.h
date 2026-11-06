#pragma once
#include "lvgl.h"

/* Photo thumbnails from the back-end's image cache (hello.image_root + a relative path such as "food/latte_160.jpg").
   Decoding goes through LVGL's file system ('A:' = POSIX) and TJPGD; decoded thumbnails live in LVGL's image cache. */

/* Lay a cover-fit photo over `thumb` (a dos_thumb box of w x h). `small` is the 160 px file, `large` the 320 px one:
   the large one is used once the box is wider than 120 px. Returns the image object, or NULL (icon stays) when both
   paths are empty or the file cannot be read. */
lv_obj_t *dos_thumb_photo(lv_obj_t *thumb, const char *small, const char *large, int32_t w, int32_t h);

/* A thumb box that already holds the photo (or the icon when there is none). */
lv_obj_t *dos_thumb_img(lv_obj_t *parent, int32_t w, int32_t h, int icon, int icon_px, int32_t radius,
                        const char *small, const char *large);
