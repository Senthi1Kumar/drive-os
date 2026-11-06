#include "map/maneuver_arrow.h"
#include <math.h>
#include <stdbool.h>
#include <stdio.h>

#define M_PER_DEG 111320.0

typedef struct {
    char *p;
    size_t cap, len;
    bool full;
} out_t;

static void put(out_t *o, const char *fmt, double a, double b)
{
    if(o->full) return;
    int n = snprintf(o->p + o->len, o->cap - o->len, fmt, a, b);
    if(n < 0 || (size_t)n >= o->cap - o->len) o->full = true;
    else o->len += (size_t)n;
}

static void lit(out_t *o, const char *s) { put(o, s, 0, 0); }

/* point k of the line that starts at the fix: k = 0 the fix, k >= 1 the route's points after its segment */
static void at(const double (*ll)[2], const dos_route_fix_t *fix, int k, double *p)
{
    if(k == 0) {
        p[0] = fix->lon;
        p[1] = fix->lat;
    }
    else {
        p[0] = ll[fix->seg + k][0];
        p[1] = ll[fix->seg + k][1];
    }
}

size_t dos_maneuver_geojson(char *out, size_t cap, const double (*ll)[2], int n, const dos_route_fix_t *fix,
                            double dist_m, double lead_m, double tail_m)
{
    if(!out || cap == 0 || !ll || !fix || n < 2 || fix->seg < 0 || fix->seg > n - 2 || dist_m < 0) return 0;
    double s0 = dist_m - lead_m > 0 ? dist_m - lead_m : 0, s1 = dist_m + tail_m;
    double kx = M_PER_DEG * cos(fix->lat * M_PI / 180.0);
    int cnt = n - fix->seg;
    out_t o = { out, cap, 0, false };
    bool started = false;
    double c = 0, end_lon = 0, end_lat = 0, bearing = 0;
    for(int k = 0; k + 1 < cnt; k++) {
        double a[2], b[2];
        at(ll, fix, k, a);
        at(ll, fix, k + 1, b);
        double dx = (b[0] - a[0]) * kx, dy = (b[1] - a[1]) * M_PER_DEG, len = hypot(dx, dy);
        double lo = s0 > c ? s0 : c, hi = s1 < c + len ? s1 : c + len;
        if(len > 0 && hi > lo) {
            double t0 = (lo - c) / len, t1 = (hi - c) / len;
            if(!started) {
                lit(&o, "{\"type\":\"FeatureCollection\",\"features\":[{\"type\":\"Feature\",\"properties\":{\"k\":\"seg\"},"
                        "\"geometry\":{\"type\":\"LineString\",\"coordinates\":[");
                put(&o, "[%.6f,%.6f]", a[0] + t0 * (b[0] - a[0]), a[1] + t0 * (b[1] - a[1]));
                started = true;
            }
            end_lon = a[0] + t1 * (b[0] - a[0]);
            end_lat = a[1] + t1 * (b[1] - a[1]);
            lit(&o, ",");
            put(&o, "[%.6f,%.6f]", end_lon, end_lat);
            bearing = atan2(dx, dy) * 180.0 / M_PI;
            if(bearing < 0) bearing += 360.0;
        }
        c += len;
        if(c >= s1) break;
    }
    if(!started) return 0;
    lit(&o, "]}},{\"type\":\"Feature\",\"properties\":{\"k\":\"head\",\"b\":");
    put(&o, "%.1f", bearing, 0);
    lit(&o, "},\"geometry\":{\"type\":\"Point\",\"coordinates\":");
    put(&o, "[%.6f,%.6f]", end_lon, end_lat);
    lit(&o, "}}]}");
    return o.full ? 0 : o.len;
}

/* ---- the arrowhead image ---- */

typedef struct { double x, y; } v2_t;

static bool inside(const v2_t *poly, int n, double x, double y)
{
    bool in = false;
    for(int i = 0, j = n - 1; i < n; j = i++)
        if((poly[i].y > y) != (poly[j].y > y) &&
           x < (poly[j].x - poly[i].x) * (y - poly[i].y) / (poly[j].y - poly[i].y) + poly[i].x)
            in = !in;
    return in;
}

void dos_maneuver_arrow_render(uint8_t *rgba, int size)
{
    static const v2_t head[4] = { { 0.50, 0.08 }, { 0.90, 0.88 }, { 0.50, 0.66 }, { 0.10, 0.88 } };
    v2_t rim[4];
    for(int i = 0; i < 4; i++) {   /* the outline: the same shape grown about its centre */
        rim[i].x = 0.5 + (head[i].x - 0.5) * 1.12;
        rim[i].y = 0.5 + (head[i].y - 0.5) * 1.12;
    }
    const int ss = 4;
    for(int y = 0; y < size; y++)
        for(int x = 0; x < size; x++) {
            double fill = 0, edge = 0;
            for(int sy = 0; sy < ss; sy++)
                for(int sx = 0; sx < ss; sx++) {
                    double px = (x + (sx + 0.5) / ss) / size, py = (y + (sy + 0.5) / ss) / size;
                    if(inside(head, 4, px, py)) fill += 1;
                    else if(inside(rim, 4, px, py)) edge += 1;
                }
            fill /= ss * ss;
            edge /= ss * ss;
            double a = fill + edge;
            /* white over a navy outline, premultiplied */
            double r = fill * 255 + edge * 10, g = fill * 255 + edge * 21, b = fill * 255 + edge * 48;
            uint8_t *p = rgba + ((size_t)y * size + x) * 4;
            p[0] = (uint8_t)(r + 0.5);
            p[1] = (uint8_t)(g + 0.5);
            p[2] = (uint8_t)(b + 0.5);
            p[3] = (uint8_t)(a * 255 + 0.5);
        }
}
