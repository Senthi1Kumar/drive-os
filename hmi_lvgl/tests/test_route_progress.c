#include "dos_test.h"
#include "map/route_progress.h"
#include <math.h>

/* A route heading east along the equator, a point every 0.001 degrees (about 111 m). */
static void east_route(double (*ll)[2], int n) {
    for(int i = 0; i < n; i++) { ll[i][0] = 0.001 * i; ll[i][1] = 0; }
}

static void a_car_on_a_segment_drops_what_is_behind(void) {
    double ll[6][2]; east_route(ll, 6);
    dos_route_fix_t f;
    CHECK(dos_route_locate(ll, 6, 0.00001, 0.0025, 0, 50, &f));
    CHECK_EQ(f.seg, 2);
    CHECK(fabs(f.lon - 0.0025) < 1e-9);
    double out[6][2];
    int m = dos_route_remaining(out, ll, 6, &f);
    CHECK_EQ(m, 4);                       /* the car, then points 3, 4, 5 */
    CHECK(fabs(out[0][0] - 0.0025) < 1e-9);
    CHECK(fabs(out[1][0] - 0.003) < 1e-9);
    CHECK(fabs(out[3][0] - 0.005) < 1e-9);
}

static void the_end_of_the_route_leaves_one_segment(void) {
    double ll[4][2]; east_route(ll, 4);
    dos_route_fix_t f;
    CHECK(dos_route_locate(ll, 4, 0, 0.0031, 0, 50, &f));   /* a little past the last point */
    CHECK_EQ(f.seg, 2);
    double out[4][2];
    CHECK_EQ(dos_route_remaining(out, ll, 4, &f), 2);
    CHECK(fabs(out[0][0] - 0.003) < 1e-9);
}

static void off_route_is_not_located(void) {
    double ll[4][2]; east_route(ll, 4);
    dos_route_fix_t f;
    CHECK(!dos_route_locate(ll, 4, 0.002, 0.0015, 0, 50, &f));   /* 222 m north */
}

static void the_hint_keeps_a_self_crossing_route_in_order(void) {
    /* out east, up, back west across the first leg, and on: the crossing at lon 0.002 is on segments 1 and 5 */
    double ll[8][2] = { {0,0}, {0.001,0}, {0.002,0}, {0.003,0}, {0.003,0.002}, {0.002,0.002}, {0.002,-0.002}, {0.003,-0.002} };
    dos_route_fix_t f;
    CHECK(dos_route_locate(ll, 8, 0, 0.002, 1, 50, &f));
    CHECK(f.seg <= 2);                                          /* the first pass, not the second */
    CHECK(dos_route_locate(ll, 8, 0, 0.002, 5, 50, &f));
    CHECK(f.seg >= 5);                                          /* the second pass once the car is past the turn */
}

static void a_jump_far_ahead_is_found_by_the_full_search(void) {
    double ll[200][2]; east_route(ll, 200);
    dos_route_fix_t f;
    CHECK(dos_route_locate(ll, 200, 0, 0.150, 2, 50, &f));      /* the car is 150 segments past the hint */
    CHECK(f.seg == 149 || f.seg == 150);                        /* on the vertex between them */
    double out[200][2];
    CHECK_EQ(dos_route_remaining(out, ll, 200, &f), 50);        /* the car on vertex 150, then 151 .. 199 */
}

int main(void) {
    RUN(a_car_on_a_segment_drops_what_is_behind); RUN(the_end_of_the_route_leaves_one_segment);
    RUN(off_route_is_not_located); RUN(the_hint_keeps_a_self_crossing_route_in_order);
    RUN(a_jump_far_ahead_is_found_by_the_full_search);
    DONE();
}
