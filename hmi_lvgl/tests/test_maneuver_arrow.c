#include "dos_test.h"
#include "map/maneuver_arrow.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

/* East along the equator for 0.01 degrees (1113 m), then north for 0.01: a left turn at point 1. */
static const double L[3][2] = { { 0.0, 0.0 }, { 0.01, 0.0 }, { 0.01, 0.01 } };

static double bearing_of(const char *json)
{
    const char *p = strstr(json, "\"b\":");
    return p ? atof(p + 4) : -1;
}

static void a_turn_ahead_is_highlighted_and_pointed_along_the_new_road(void)
{
    char b[1024];
    dos_route_fix_t f = { .seg = 0, .lon = 0.0, .lat = 0.0 };
    double turn_m = 0.01 * 111320.0;
    CHECK(dos_maneuver_geojson(b, sizeof b, L, 3, &f, turn_m, 40, 30) > 0);
    CHECK(strstr(b, "\"k\":\"seg\"") != NULL);
    CHECK(strstr(b, "\"k\":\"head\"") != NULL);
    CHECK(fabs(bearing_of(b) - 0.0) < 1.0);   /* the road after the turn runs north */
    CHECK(strstr(b, "[0.010000,0.000000]") != NULL);   /* the turn point itself is on the line */
}

static void a_straight_run_points_the_way_it_goes(void)
{
    char b[1024];
    dos_route_fix_t f = { .seg = 0, .lon = 0.0, .lat = 0.0 };
    CHECK(dos_maneuver_geojson(b, sizeof b, L, 3, &f, 300, 40, 30) > 0);
    CHECK(fabs(bearing_of(b) - 90.0) < 1.0);   /* still on the eastbound leg */
}

static void the_line_starts_at_the_car_when_the_turn_is_close(void)
{
    char b[1024];
    dos_route_fix_t f = { .seg = 0, .lon = 0.0098, .lat = 0.0 };
    CHECK(dos_maneuver_geojson(b, sizeof b, L, 3, &f, 20, 40, 30) > 0);
    CHECK(strstr(b, "[0.009800,0.000000]") != NULL);
}

static void a_fix_on_a_later_segment_counts_from_there(void)
{
    char b[1024];
    dos_route_fix_t f = { .seg = 1, .lon = 0.01, .lat = 0.002 };
    CHECK(dos_maneuver_geojson(b, sizeof b, L, 3, &f, 100, 40, 30) > 0);
    CHECK(fabs(bearing_of(b) - 0.0) < 1.0);
}

static void nothing_is_drawn_past_the_end_or_in_a_small_buffer(void)
{
    char b[1024], tiny[40];
    dos_route_fix_t f = { .seg = 1, .lon = 0.01, .lat = 0.009999 };
    CHECK_EQ(dos_maneuver_geojson(b, sizeof b, L, 3, &f, 500, 40, 30), 0);   /* beyond the line */
    f = (dos_route_fix_t){ .seg = 0 };
    CHECK_EQ(dos_maneuver_geojson(tiny, sizeof tiny, L, 3, &f, 300, 40, 30), 0);
    CHECK_EQ(dos_maneuver_geojson(b, sizeof b, L, 1, &f, 300, 40, 30), 0);
    CHECK_EQ(dos_maneuver_geojson(b, sizeof b, L, 3, &f, -1, 40, 30), 0);
}

static void the_arrow_image_is_solid_in_the_middle_and_clear_at_the_corners(void)
{
    enum { S = 48 };
    static uint8_t px[S * S * 4];
    dos_maneuver_arrow_render(px, S);
    CHECK_EQ(px[(((S * 6) / 10) * S + S / 2) * 4 + 3], 255);   /* inside the head */
    CHECK_EQ(px[3], 0);                                         /* top-left corner */
    CHECK_EQ(px[((S - 1) * S + S - 1) * 4 + 3], 0);
    int lit = 0;
    for(int i = 0; i < S * S; i++) lit += px[i * 4 + 3] > 0;
    CHECK(lit > S * S / 5 && lit < S * S * 3 / 4);
}

int main(void)
{
    RUN(a_turn_ahead_is_highlighted_and_pointed_along_the_new_road);
    RUN(a_straight_run_points_the_way_it_goes);
    RUN(the_line_starts_at_the_car_when_the_turn_is_close);
    RUN(a_fix_on_a_later_segment_counts_from_there);
    RUN(nothing_is_drawn_past_the_end_or_in_a_small_buffer);
    RUN(the_arrow_image_is_solid_in_the_middle_and_clear_at_the_corners);
    DONE();
}
