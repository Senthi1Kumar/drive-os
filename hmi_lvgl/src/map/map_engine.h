#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* A MapLibre Native map rendered off-screen (own EGL context, OpenGL ES 3, session-owned texture) and read
   back into a CPU buffer in LVGL's ARGB8888 byte order (B, G, R, A). Everything runs on the calling thread:
   dos_map_step() pumps the runtime with a small budget and renders only when the map has something new, so it
   fits inside an LVGL timer. Mapbox styles (mapbox://…) need an access token. */
typedef struct dos_map dos_map_t;

typedef struct {
    uint32_t width, height;     /* logical = physical pixels */
    double lat, lon, zoom, bearing, pitch;
    const char *style_url;      /* mapbox://styles/…, https://…, or file://… */
    const char *access_token;   /* Mapbox token, or NULL */
    const char *cache_path;     /* NULL = in memory */
} dos_map_cfg_t;

dos_map_t *dos_map_create(const dos_map_cfg_t *cfg);   /* NULL on failure; reason in dos_map_last_error() */
const char *dos_map_last_error(void);
void dos_map_destroy(dos_map_t *m);

bool dos_map_step(dos_map_t *m);   /* true when a new frame is in the buffer */
bool dos_map_idle(const dos_map_t *m);   /* nothing to render and no frame still in flight */
/* Hands the finished frame over without a copy: swaps *buf (capacity *cap) with the engine's buffer. */
bool dos_map_take_pixels(dos_map_t *m, uint8_t **buf, size_t *cap, uint32_t *w, uint32_t *h, uint32_t *stride);

/* What a frame shows besides its pixels. The tag function runs right after the render that drew the frame, and
   its result travels with that frame through the readback, so a caller never pairs pixels with another frame's
   car position. */
typedef struct {
    bool car_on;                   /* the car is in view */
    float car_x, car_y, car_rot;   /* px in the frame; degrees clockwise from the frame's up */
    double bearing;                /* the map's bearing in this frame */
} dos_map_tag_t;
typedef void (*dos_map_tag_fn)(dos_map_t *m, void *user, dos_map_tag_t *out);
void dos_map_set_tag_fn(dos_map_t *m, dos_map_tag_fn fn, void *user);
const dos_map_tag_t *dos_map_frame_tag(const dos_map_t *m);   /* of the frame dos_map_pixels / take_pixels return */
void dos_map_pump(dos_map_t *m);   /* runtime work only (network, parsing), no render: for a hidden map */
const uint8_t *dos_map_pixels(const dos_map_t *m, uint32_t *w, uint32_t *h, uint32_t *stride);
bool dos_map_style_ready(const dos_map_t *m);   /* sources and layers can be added */
bool dos_map_set_style(dos_map_t *m, const char *style_url);   /* our sources and layers must be added again */
bool dos_map_resize(dos_map_t *m, uint32_t width, uint32_t height);

void dos_map_move_by(dos_map_t *m, double dx, double dy);
void dos_map_glide(dos_map_t *m, double dx, double dy, double duration_ms);   /* inertia pan, ease-out */
void dos_map_scale_by(dos_map_t *m, double scale, double anchor_x, double anchor_y, double duration_ms);
void dos_map_gesture(dos_map_t *m, bool in_progress);
void dos_map_jump_to(dos_map_t *m, double lat, double lon, double zoom /* < 0 keeps */, double bearing /* NAN keeps */);
void dos_map_ease_to(dos_map_t *m, double lat, double lon, double bearing /* NAN keeps */, double duration_ms);
bool dos_map_camera(dos_map_t *m, double *lat, double *lon, double *zoom, double *bearing);
/* The whole camera at once, no animation (a follow camera sets it every frame); get_view reads it back. */
void dos_map_set_view(dos_map_t *m, double lat, double lon, double zoom, double bearing, double pitch, double pad_top_px);
bool dos_map_get_view(dos_map_t *m, double *lat, double *lon, double *zoom, double *bearing, double *pitch);
/* Centre, zoom, bearing and pitch in one eased move; NAN keeps a field, pad_top_px < 0 keeps the padding.
   Navigation looks ahead by padding the top, which puts the car in the lower part of the view. */
void dos_map_ease_camera(dos_map_t *m, double lat, double lon, double zoom, double bearing, double pitch,
                        double pad_top_px, double duration_ms);
/* Eases to the camera that shows the box with pad_px to spare, north up and flat. */
void dos_map_fit(dos_map_t *m, double south, double west, double north, double east, double pad_px, double duration_ms);
/* A runtime style image from premultiplied RGBA8 (stride w * 4), e.g. the car puck; a restyle drops it. */
bool dos_map_set_image(dos_map_t *m, const char *id, const uint8_t *rgba, uint32_t w, uint32_t h);
bool dos_map_pixel_for(dos_map_t *m, double lat, double lon, double *x, double *y);

typedef struct {
    uint32_t frames;
    double pump_ms, render_ms, read_ms, convert_ms;   /* wall time on the calling thread, totals since create */
} dos_map_stats_t;
void dos_map_stats(const dos_map_t *m, dos_map_stats_t *out);

bool dos_map_set_geojson(dos_map_t *m, const char *source_id, const char *geojson);   /* adds or replaces */
bool dos_map_add_layer_json(dos_map_t *m, const char *layer_json);
/* Removes the style's layers that read from these sources, then the sources, so no tile of them is requested. A
   source the style does not have is skipped. Returns how many sources were removed. */
int dos_map_drop_sources(dos_map_t *m, const char *const *source_ids, int n);
/* Edits to the style's own layers (all false when the layer is missing or the value is rejected). */
bool dos_map_layer_exists(dos_map_t *m, const char *layer_id);
bool dos_map_set_filter(dos_map_t *m, const char *layer_id, const char *filter_json);
bool dos_map_set_layer_property(dos_map_t *m, const char *layer_id, const char *property, const char *value_json);
bool dos_map_set_layer_max_zoom(dos_map_t *m, const char *layer_id, double max_zoom);
/* The id of the lowest layer of this type (e.g. "symbol"), so a layer can be inserted below the labels. */
bool dos_map_first_layer_of_type(dos_map_t *m, const char *type, char *id, size_t cap);
/* Like dos_map_add_layer_json, inserted below before_id ("" appends). */
bool dos_map_add_layer_json_before(dos_map_t *m, const char *layer_json, const char *before_id);
