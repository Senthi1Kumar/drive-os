#pragma once
#include <stdbool.h>

/* Where the car is along a route line, so the part it has driven can be dropped from the drawn line. Points are
   (lon, lat) pairs, the order GeoJSON and route_geojson use. */
typedef struct {
    int seg;              /* the segment (point seg to seg + 1) the car is nearest */
    double lon, lat;      /* the car's projection onto that segment */
    double off_m;         /* distance from the car to the line */
} dos_route_fix_t;

/* Nearest point on the line to the car. The search starts at segment `hint` (the last fix) and looks a bounded
   distance ahead, so a route that crosses itself keeps its order; when nothing there is within `max_off_m` it
   searches the whole line. False when the car is farther than `max_off_m` from every segment (off route). */
bool dos_route_locate(const double (*ll)[2], int n, double lat, double lon, int hint, double max_off_m,
                      dos_route_fix_t *out);

/* The line from the fix to the end: out[0] is the projected car, then points seg + 1 .. n - 1. Returns how many
   points were written (at most n - seg); `out` needs room for n. The congestion letter for the segment that ends at
   out[k + 1] is congestion[n - m + k], m being the returned count (the last m - 1 segments of the route). */
int dos_route_remaining(double (*out)[2], const double (*ll)[2], int n, const dos_route_fix_t *fix);
