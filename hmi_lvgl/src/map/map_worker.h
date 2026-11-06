#pragma once
#include "map/map_engine.h"
#include <stddef.h>

/* map_engine on its own thread. The engine (EGL context, MapLibre runtime, map, texture session) lives and dies
   on the worker; the UI queues camera and overlay commands and takes finished BGRA frames. The worker renders at
   most once per period, so a 60 Hz finger never costs more than the frame budget, and the UI thread never waits
   on the GPU readback. Frames change hands by pointer: nothing is copied while the lock is held.

   The car is the worker's too. Position samples arrive at the sim's rate; the worker draws the car one sample
   interval behind, interpolated, and while following moves the camera with it on every rendered frame, so
   neither the car nor the camera hops between samples. Each frame carries the car's screen point, computed with
   the camera that drew it, for the UI's puck. */
typedef struct dos_mapw dos_mapw_t;
typedef void (*dos_mapw_style_cb_t)(dos_map_t *m);   /* runs on the worker once the style has loaded */

typedef struct {
    uint32_t w, h, stride;   /* of the pixels returned, when any */
    dos_map_tag_t tag;        /* the newest car point; newer than the pixels when only the car moved */
    uint32_t tag_seq;        /* changes whenever tag does */
} dos_mapw_frame_t;

dos_mapw_t *dos_mapw_create(const dos_map_cfg_t *cfg, dos_mapw_style_cb_t on_style);   /* cfg strings must outlive it */
void dos_mapw_destroy(dos_mapw_t *w);
bool dos_mapw_failed(dos_mapw_t *w, char *why, size_t cap);
/* The newest frame, or NULL when there is none since the last call; f is filled either way. The pixels stay
   valid until the next call. */
const uint8_t *dos_mapw_take(dos_mapw_t *w, dos_mapw_frame_t *f);
void dos_mapw_set_period(dos_mapw_t *w, uint32_t ms);   /* 0: hidden, network only */

void dos_mapw_move_by(dos_mapw_t *w, double dx, double dy);
void dos_mapw_glide(dos_mapw_t *w, double dx, double dy, double ms);
void dos_mapw_scale_by(dos_mapw_t *w, double scale, double ax, double ay, double ms);
void dos_mapw_gesture(dos_mapw_t *w, bool on);   /* a finger on the map: following pauses until it lifts */
void dos_mapw_ease_to(dos_mapw_t *w, double lat, double lon, double ms);
/* dos_map_ease_camera / dos_map_fit on the worker; NAN keeps a field, pad_top_px < 0 keeps the padding. */
void dos_mapw_camera_to(dos_mapw_t *w, double lat, double lon, double zoom, double bearing, double pitch,
                       double pad_top_px, double ms);
void dos_mapw_fit(dos_mapw_t *w, double south, double west, double north, double east, double pad_px, double ms);
double dos_mapw_bearing(dos_mapw_t *w);   /* degrees, as of the newest frame (the compass) */
void dos_mapw_resize(dos_mapw_t *w, uint32_t width, uint32_t height);
void dos_mapw_set_geojson(dos_mapw_t *w, const char *source_id, const char *geojson);   /* latest wins per source */
void dos_mapw_set_style(dos_mapw_t *w, const char *style_url);   /* on_style runs again, overlays come back */

/* The fix, stamped by its sender (src_ms, any monotonic clock) or, when src_ms <= 0, when it arrives. */
void dos_mapw_set_car(dos_mapw_t *w, double lat, double lon, double heading, double src_ms);
/* Follow the car: centred, north up or turned to the heading, at zoom/pitch with pad_top_px of look-ahead (NAN
   keeps the previous value). ease_ms blends from the current camera. off leaves the camera where it is. */
void dos_mapw_follow(dos_mapw_t *w, bool on, bool heading_up, double zoom, double pitch, double pad_top_px,
                    double ease_ms);
void dos_mapw_follow_zoom(dos_mapw_t *w, double delta_levels);   /* while following: the follow camera's zoom */
