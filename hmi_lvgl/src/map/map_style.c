#include "map/map_style.h"
#include <stdio.h>

size_t dos_style_buildings_json(char *out, size_t cap, bool night, double minzoom)
{
    const char *ramp = night ? "[\"interpolate\",[\"linear\"],[\"get\",\"height\"],0,\"#262d38\",40,\"#2d3644\",120,\"#384457\","
                               "300,\"#47566c\"]"
                             : "[\"interpolate\",[\"linear\"],[\"get\",\"height\"],0,\"#d6dbe2\",40,\"#c8cfd9\",120,\"#b7c0cd\","
                               "300,\"#a3aebd\"]";
    int n = snprintf(out, cap,
                     "{\"id\":\"dos-3d-buildings\",\"type\":\"fill-extrusion\",\"source\":\"composite\","
                     "\"source-layer\":\"building\",\"minzoom\":%.2f,"
                     "\"filter\":[\"all\",[\"!=\",[\"get\",\"type\"],\"building:part\"],"
                     "[\"==\",[\"get\",\"underground\"],\"false\"]],"
                     "\"paint\":{\"fill-extrusion-color\":%s,"
                     "\"fill-extrusion-height\":[\"interpolate\",[\"linear\"],[\"zoom\"],%.2f,0,%.2f,[\"coalesce\",[\"get\",\"height\"],6]],"
                     "\"fill-extrusion-base\":[\"interpolate\",[\"linear\"],[\"zoom\"],%.2f,0,%.2f,[\"coalesce\",[\"get\",\"min_height\"],0]],"
                     "\"fill-extrusion-opacity\":0.85,\"fill-extrusion-vertical-gradient\":true}}",
                     minzoom, ramp, minzoom, minzoom + 0.5, minzoom, minzoom + 0.5);
    return n > 0 && (size_t)n < cap ? (size_t)n : 0;
}

size_t dos_style_poi_filter_json(char *out, size_t cap)
{
    int n = snprintf(out, cap,
                     "[\"let\",\"densityByClass\",[\"match\",[\"get\",\"class\"],\"education\",1,\"landmark\",2,\"medical\",1,"
                     "\"motorist\",3,\"park_like\",1,0],"
                     "[\"<=\",[\"get\",\"filterrank\"],[\"+\",[\"var\",\"densityByClass\"],[\"step\",[\"zoom\"],0,16,1,17,2]]]]");
    return n > 0 && (size_t)n < cap ? (size_t)n : 0;
}
