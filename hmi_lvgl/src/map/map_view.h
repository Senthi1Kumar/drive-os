#pragma once
#include "lvgl.h"
#include "map/route_geojson.h"
#include <stdbool.h>

/* The live map as an LVGL object: Mapbox vector tiles rendered by MapLibre Native off-screen (map_engine) and
   drawn as an image. Drag pans like Google Maps (with a glide on release), double tap zooms in, and follow mode
   keeps the car centred until the driver drags. Frames are rendered only while something moves: 30 fps while
   touched, animating or following a moving car (the display refreshes at 60 Hz then), none while idle or while
   the page is hidden. The worker moves the car and the follow camera every frame between position samples; the
   puck is drawn by LVGL at the point each frame carries, so it never drifts from the road under it.
   Without MapLibre (no DOS_HAVE_MAP) or without a token for a mapbox:// style it shows a placeholder. */
typedef struct {
    const char *style_url;      /* NULL: DOS_MAP_STYLE env, else the night style (Mapbox with a token, else OpenFreeMap) */
    const char *access_token;   /* NULL: DRIVEOS_MAPBOX__TOKEN env (MAPBOX_API_KEY as a fallback) */
    double lat, lon, zoom;
} map_view_cfg_t;

lv_obj_t *map_view_create(lv_obj_t *parent, const map_view_cfg_t *cfg);
/* The wordmark and credits stay at the map's bottom corners but are drawn on `layer` (a container the map's page sits
   in, such as the stage), above whatever fades the content under a dock there. They show only while the map is active. */
void map_view_lift_footer(lv_obj_t *mv, lv_obj_t *layer);
void map_view_set_active(lv_obj_t *mv, bool visible);
/* The arrow puck. t_ms: the sender's monotonic stamp of this fix (<= 0: unstamped). A fix already seen (same stamp,
   or no stamp and the same place) is not a new sample, so repeated snapshots never stall the interpolation. */
void map_view_set_car(lv_obj_t *mv, double lat, double lon, double heading_deg, double t_ms);
/* n < 2 clears. congestion: Directions' one letter per segment (u l m h s) colors the line, NULL = plain. A new
   route while navigating is first shown whole (overview), then the camera returns to the car. */
/* route_id: nav.route.id (0 = unknown). Only a new id (or, without one, new end points) frames the route whole;
   an ETA refresh or reroute under the same id replaces the line and leaves follow mode and pitch alone. */
void map_view_set_route(lv_obj_t *mv, const double (*lonlat)[2], int n, const char *congestion, uint32_t route_id);
/* The next turn: dist_m is the distance along the route from the car to it (< 0: none). Within a few hundred metres
   the route around it is lit and an arrowhead lies on the road, pointing out of the turn. */
void map_view_set_maneuver(lv_obj_t *mv, double dist_m);
void map_view_set_stops(lv_obj_t *mv, const route_stop_t *stops, int n, const route_stop_t *dest);   /* NULL dest clears */
/* The last place search as numbered pins (n == 0 clears). A new set is framed with the car: while navigating for a
   few seconds before the camera returns to the car, otherwise until the driver recenters. */
void map_view_set_results(lv_obj_t *mv, const route_stop_t *places, int n);
void map_view_set_follow(lv_obj_t *mv, bool follow);
bool map_view_following(lv_obj_t *mv);
/* Navigating: while following, the map turns heading-up with a tilted look-ahead (the car low in the view). */
void map_view_set_nav(lv_obj_t *mv, bool navigating);
void map_view_recenter(lv_obj_t *mv);   /* follow again at street level (heading-up when navigating) */
void map_view_north_up(lv_obj_t *mv);   /* the compass: north up and flat until the next recenter */
void map_view_overview(lv_obj_t *mv);   /* the whole route in view until the next recenter */
double map_view_bearing(lv_obj_t *mv);  /* degrees clockwise from north, as drawn */
void map_view_zoom(lv_obj_t *mv, double factor);   /* about the centre, eased */
bool map_view_failed(lv_obj_t *mv);                /* the engine could not start or died: show something else */
bool map_view_live(lv_obj_t *mv);                  /* MapLibre is rendering (not the placeholder) */
/* Night: navigation-night (live traffic) with place names added; day: streets (shops, restaurants, transit). */
void map_view_set_night(lv_obj_t *mv, bool night);
bool map_view_night(lv_obj_t *mv);
void map_view_wheel(lv_obj_t *mv, int32_t x, int32_t y, float notches);   /* screen point; zooms about it */
