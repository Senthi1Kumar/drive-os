#include "map/polyline.h"

static int next_value(const char *s, size_t len, size_t *i, long *out)
{
    long result = 0;
    int shift = 0;
    for(;;) {
        if(*i >= len || shift > 30) return 0;
        int b = (unsigned char)s[(*i)++] - 63;
        if(b < 0 || b > 63) return 0;
        result |= (long)(b & 0x1f) << shift;
        shift += 5;
        if(b < 0x20) break;
    }
    *out = (result & 1) ? ~(result >> 1) : (result >> 1);
    return 1;
}

int dos_polyline6_decode(const char *text, size_t len, double (*lonlat)[2], int cap)
{
    long lat = 0, lon = 0;
    size_t i = 0;
    int n = 0;
    while(text && i < len && n < cap) {
        long dlat, dlon;
        if(!next_value(text, len, &i, &dlat) || !next_value(text, len, &i, &dlon)) return 0;
        lat += dlat;
        lon += dlon;
        lonlat[n][0] = (double)lon / 1e6;
        lonlat[n][1] = (double)lat / 1e6;
        n++;
    }
    return n;
}
