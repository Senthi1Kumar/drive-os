#include "map/route_progress.h"
#include <math.h>

#define M_PER_DEG 111320.0
#define AHEAD_SEGS 120   /* how far past the last fix the first search looks */

/* Equirectangular around the car: metres east / north of the car for a point. */
static void local(double lat0, double kx, const double *p, double *x, double *y)
{
    (void)lat0;
    *x = p[0] * kx;
    *y = p[1] * M_PER_DEG;
}

static bool search(const double (*ll)[2], int from, int to, double lat, double lon, dos_route_fix_t *best)
{
    double kx = M_PER_DEG * cos(lat * M_PI / 180.0), cx = lon * kx, cy = lat * M_PER_DEG;
    bool found = false;
    for(int i = from; i < to; i++) {
        double ax, ay, bx, by;
        local(lat, kx, ll[i], &ax, &ay);
        local(lat, kx, ll[i + 1], &bx, &by);
        double dx = bx - ax, dy = by - ay, len2 = dx * dx + dy * dy;
        double t = len2 > 0 ? ((cx - ax) * dx + (cy - ay) * dy) / len2 : 0;
        t = t < 0 ? 0 : t > 1 ? 1 : t;
        double px = ax + t * dx, py = ay + t * dy, d = hypot(cx - px, cy - py);
        if(!found || d < best->off_m) {
            best->seg = i;
            best->lon = ll[i][0] + t * (ll[i + 1][0] - ll[i][0]);
            best->lat = ll[i][1] + t * (ll[i + 1][1] - ll[i][1]);
            best->off_m = d;
            found = true;
        }
    }
    return found;
}

bool dos_route_locate(const double (*ll)[2], int n, double lat, double lon, int hint, double max_off_m,
                      dos_route_fix_t *out)
{
    if(!ll || n < 2 || !out) return false;
    int segs = n - 1;
    int from = hint < 1 ? 0 : hint - 1, to = from + AHEAD_SEGS + 1;
    if(from > segs - 1) from = segs - 1;
    if(to > segs) to = segs;
    dos_route_fix_t f = { 0 };
    if(!search(ll, from, to, lat, lon, &f) || f.off_m > max_off_m) {
        if(!search(ll, 0, segs, lat, lon, &f) || f.off_m > max_off_m) return false;
    }
    *out = f;
    return true;
}

int dos_route_remaining(double (*out)[2], const double (*ll)[2], int n, const dos_route_fix_t *fix)
{
    out[0][0] = fix->lon;
    out[0][1] = fix->lat;
    int m = 1, i = fix->seg + 1;
    if(i < n - 1 && fix->lon == ll[i][0] && fix->lat == ll[i][1]) i++;   /* on a vertex: no zero-length segment */
    for(; i < n; i++, m++) {
        out[m][0] = ll[i][0];
        out[m][1] = ll[i][1];
    }
    return m;
}
