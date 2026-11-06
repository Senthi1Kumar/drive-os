#pragma once
#include "map/route_progress.h"
#include <stddef.h>
#include <stdint.h>

/* The next turn on the map: a highlighted piece of the route around the maneuver and an arrowhead lying on the road at
   the end of it, pointing the way the car leaves the turn. Pure geometry; points are (lon, lat) like the route. */

/* GeoJSON FeatureCollection: a LineString (properties.k = "seg") from lead_m before the maneuver to tail_m after
   it, and a Point (properties.k = "head", properties.b = bearing in degrees clockwise from north) at the end of
   that line. dist_m is the distance along the route from the fix to the maneuver. Returns the length written, or 0
   when there is nothing to draw (maneuver behind or past the end of the line, or the buffer is too small). */
size_t dos_maneuver_geojson(char *out, size_t cap, const double (*ll)[2], int n, const dos_route_fix_t *fix,
                            double dist_m, double lead_m, double tail_m);

/* The arrowhead as a style image: a white arrow with a dark outline pointing up, premultiplied RGBA8,
   size * size * 4 bytes. */
void dos_maneuver_arrow_render(uint8_t *rgba, int size);
