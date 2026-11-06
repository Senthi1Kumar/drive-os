#include "dos_test.h"
#include "map/route_identity.h"

static void same_id_new_geometry_is_not_fresh(void) {
    uint32_t last = 0;
    CHECK(dos_route_is_new(&last, 7, 111));    /* first route of the trip */
    CHECK(!dos_route_is_new(&last, 7, 222));   /* ETA refresh / reroute: same id, the line starts elsewhere */
    CHECK(!dos_route_is_new(&last, 7, 333));
}

static void new_id_is_fresh(void) {
    uint32_t last = 0;
    CHECK(dos_route_is_new(&last, 7, 111));
    CHECK(dos_route_is_new(&last, 8, 111));    /* a new destination, even with identical geometry */
    CHECK(!dos_route_is_new(&last, 8, 111));
}

static void clearing_the_route_forgets_it(void) {
    uint32_t last = 0;
    CHECK(dos_route_is_new(&last, 7, 111));
    CHECK(!dos_route_is_new(&last, 0, 0));     /* no route */
    CHECK(dos_route_is_new(&last, 7, 111));    /* the same id coming back after a clear is a new trip */
}

static void without_an_id_the_geometry_decides(void) {
    uint32_t last = 0;
    CHECK(dos_route_is_new(&last, 0, 111));
    CHECK(!dos_route_is_new(&last, 0, 111));
    CHECK(dos_route_is_new(&last, 0, 222));
}

static void a_new_id_with_the_same_line_still_updates_the_map(void) {
    dos_route_seen_t seen = { 0 };
    CHECK(dos_route_needs_update(&seen, 1, 7, "uu", true));     /* first route */
    CHECK(!dos_route_needs_update(&seen, 1, 7, "uu", true));    /* nothing changed: leave the map alone */
    /* the same destination planned again from the same spot: the line is byte for byte the same (ver unchanged),
       but the trip is new, and the map must hear it now, not when the next ETA refresh changes the line */
    CHECK(dos_route_needs_update(&seen, 1, 8, "uu", true));
    CHECK(!dos_route_needs_update(&seen, 1, 8, "uu", true));
}

static void a_changed_line_or_traffic_or_clearing_updates_the_map(void) {
    dos_route_seen_t seen = { 0 };
    CHECK(dos_route_needs_update(&seen, 1, 7, "uu", true));
    CHECK(dos_route_needs_update(&seen, 2, 7, "uu", true));     /* ETA refresh: new line, same id */
    CHECK(dos_route_needs_update(&seen, 2, 7, "uh", true));     /* traffic colours */
    CHECK(dos_route_needs_update(&seen, 2, 7, "uh", false));    /* route gone */
    CHECK(!dos_route_needs_update(&seen, 2, 7, "uh", false));
}

int main(void) {
    RUN(same_id_new_geometry_is_not_fresh); RUN(new_id_is_fresh); RUN(clearing_the_route_forgets_it);
    RUN(without_an_id_the_geometry_decides); RUN(a_new_id_with_the_same_line_still_updates_the_map);
    RUN(a_changed_line_or_traffic_or_clearing_updates_the_map);
    DONE();
}
