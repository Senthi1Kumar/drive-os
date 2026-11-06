#pragma once
#include "lvgl.h"
#include <stdbool.h>

/* Snapshot an LVGL object and write it as an (uncompressed-deflate) PNG: no libpng needed. */
bool png_snapshot(lv_obj_t *obj, const char *path);
