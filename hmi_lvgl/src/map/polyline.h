#pragma once
#include <stddef.h>

/* Decodes an encoded polyline with 6 decimal digits (Mapbox Directions geometries=polyline6) into (lon, lat) pairs,
   the order GeoJSON and route_geojson use. Returns the number of points written (at most cap), 0 when the text is
   empty or malformed. */
int dos_polyline6_decode(const char *text, size_t len, double (*lonlat)[2], int cap);
