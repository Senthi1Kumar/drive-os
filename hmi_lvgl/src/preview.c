#include "preview.h"
#include <string.h>

static uint16_t s_px[PV_MAX_SIDE * PV_MAX_SIDE];
static lv_image_dsc_t s_dsc;
static pv_rect_t s_rect;
static uint32_t s_frames;
static void (*s_cb)(void *);
static void *s_cb_ud;

void preview_ingest(const uint8_t *data, size_t len)
{
    pv_hdr_t h;
    if(!pv_parse(data, len, &h)) return;
    memcpy(s_px, data + PV_HEADER_SIZE, h.payload);
    memset(&s_dsc, 0, sizeof s_dsc);
    s_dsc.header.magic = LV_IMAGE_HEADER_MAGIC;
    s_dsc.header.cf = LV_COLOR_FORMAT_RGB565;
    s_dsc.header.w = h.w;
    s_dsc.header.h = h.h;
    s_dsc.header.stride = (uint32_t)h.w * 2;
    s_dsc.data_size = (uint32_t)h.payload;
    s_dsc.data = (const uint8_t *)s_px;
    pv_rect_follow(&s_rect, &h, 0.5f);
    s_frames++;
    if(s_cb) s_cb(s_cb_ud);
}

const lv_image_dsc_t *preview_image(void) { return s_frames ? &s_dsc : NULL; }
const pv_rect_t *preview_rect(void) { return &s_rect; }
uint32_t preview_frames(void) { return s_frames; }

void preview_clear(void)
{
    s_frames = 0;
    s_rect.valid = false;
    memset(&s_dsc, 0, sizeof s_dsc);
}

void preview_on_frame(void (*cb)(void *ud), void *ud)
{
    s_cb = cb;
    s_cb_ud = ud;
}
