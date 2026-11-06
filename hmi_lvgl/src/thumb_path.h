#pragma once
#include <stdbool.h>
#include <stddef.h>

/* Resolve an image-cache path for LVGL ("A:<root>/<rel>"). Returns false, and writes nothing usable, unless both
   parts are non-empty, the path fits `cap`, and the file exists, is a regular file and is not empty. An image
   source that cannot be opened must never reach LVGL (it logs "Couldn't get info about the image" on every draw). */
bool dos_thumb_path(char *out, size_t cap, const char *root, const char *rel);
