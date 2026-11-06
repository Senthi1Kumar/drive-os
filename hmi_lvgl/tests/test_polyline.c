#include "dos_test.h"
#include "map/polyline.h"
#include <math.h>
#include <string.h>

/* Google's reference points (38.5,-120.2) (40.7,-120.95) (43.252,-126.453) encoded at precision 6, as Mapbox sends. */
static void t_decode(void)
{
    const char *enc = "_izlhA~rlgdF_{geC~ywl@_kwzCn`{nI";
    double p[8][2];
    int n = dos_polyline6_decode(enc, strlen(enc), p, 8);
    CHECK_EQ(n, 3);
    CHECK(fabs(p[0][1] - 38.5) < 1e-6 && fabs(p[0][0] + 120.2) < 1e-6);
    CHECK(fabs(p[1][1] - 40.7) < 1e-6 && fabs(p[1][0] + 120.95) < 1e-6);
    CHECK(fabs(p[2][1] - 43.252) < 1e-6 && fabs(p[2][0] + 126.453) < 1e-6);
}

static void t_limits(void)
{
    double p[2][2];
    CHECK_EQ(dos_polyline6_decode("", 0, p, 2), 0);
    CHECK_EQ(dos_polyline6_decode(NULL, 0, p, 2), 0);
    CHECK_EQ(dos_polyline6_decode("_izlhA~rlgdF_{geC~ywl@_kwzCn`{nI", 32, p, 2), 2);   /* capped */
    CHECK_EQ(dos_polyline6_decode("_izlhA~", 7, p, 2), 0);                              /* truncated pair */
    CHECK_EQ(dos_polyline6_decode("\x01\x02", 2, p, 2), 0);                              /* out of alphabet */
}

int main(void)
{
    RUN(t_decode);
    RUN(t_limits);
    DONE();
}
