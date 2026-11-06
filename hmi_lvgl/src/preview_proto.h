#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* Face preview frames from DriveOS (binary WS messages, see CHECKOUT_CONTRACT.md): a 24-byte little-endian header,
   then width * height RGB565 pixels. Pure parsing, no LVGL, so it is unit tested on its own. */
#define PV_HEADER_SIZE 24
#define PV_MAX_SIDE 320

typedef struct {
    uint32_t seq;
    uint16_t w, h;
    bool has_rect;
    uint16_t rx, ry, rw, rh;   /* the face rectangle in preview pixels, valid when has_rect */
    size_t payload;            /* bytes of pixels after the header */
} pv_hdr_t;

/* true when buf is a whole, well-formed version-1 frame of at most PV_MAX_SIDE square; false otherwise (never reads
   past len). */
bool pv_parse(const uint8_t *buf, size_t len, pv_hdr_t *out);

/* Exponential smoothing of a face rectangle so the brackets glide: a = 0..1 (1 = no smoothing). */
typedef struct { float x, y, w, h; bool valid; } pv_rect_t;
void pv_rect_follow(pv_rect_t *cur, const pv_hdr_t *hdr, float a);
