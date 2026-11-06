#pragma once
#include "lvgl.h"
#include "preview_proto.h"

/* The latest face preview frame, kept in RAM (never saved) and handed to LVGL as an RGB565 image. Fed from the
   WebSocket's binary callback on the LVGL thread. */
void preview_ingest(const uint8_t *data, size_t len);        /* a binary WS message; ignored unless a valid frame */
const lv_image_dsc_t *preview_image(void);                   /* NULL until a frame has arrived */
const pv_rect_t *preview_rect(void);                         /* smoothed face rectangle, .valid false when none */
uint32_t preview_frames(void);
void preview_clear(void);                                    /* forget the image (scan over, sheet closed) */
void preview_on_frame(void (*cb)(void *ud), void *ud);       /* called after each frame; NULL to clear */
