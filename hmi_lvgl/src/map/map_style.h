#pragma once
#include <stdbool.h>
#include <stddef.h>

/* Style-spec JSON for the layers and edits the basemap gets on top of Mapbox's navigation styles. Pure strings: the
   engine applies them (map_view.c, on the map worker). Each returns the length written, or 0 when cap is too small. */

/* 3D buildings: a fill-extrusion layer over Mapbox Streets v8's `building` source layer, drawn from minzoom up, with
   a dark (night) or light (day) ramp by height, a slight vertical gradient and 0.85 opacity. */
size_t dos_style_buildings_json(char *out, size_t cap, bool night, double minzoom);

/* The poi-label filter with a modestly higher density: every class gets one more rank of filterrank from z16 and two
   from z17, on top of the style's own per-class allowance. */
size_t dos_style_poi_filter_json(char *out, size_t cap);

/* The route's casing and line, wide enough to read at 1280x800 and growing with zoom. */
#define DOS_STYLE_ROUTE_CASING_WIDTH "[\"interpolate\",[\"linear\"],[\"zoom\"],12,7,16,16,19,30]"
#define DOS_STYLE_ROUTE_LINE_WIDTH "[\"interpolate\",[\"linear\"],[\"zoom\"],12,5,16,11,19,20]"
