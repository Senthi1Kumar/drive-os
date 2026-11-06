#include "dos_test.h"
#include "map/lane_layout.h"

static void names_map_to_directions(void) {
    CHECK_EQ(dos_lane_dir("left"), DOS_LANE_LEFT);
    CHECK_EQ(dos_lane_dir("slight right"), DOS_LANE_SLIGHT_RIGHT);
    CHECK_EQ(dos_lane_dir("straight"), DOS_LANE_STRAIGHT);
    CHECK_EQ(dos_lane_dir("uturn"), DOS_LANE_UTURN);
    CHECK_EQ(dos_lane_dir("none"), -1);
    CHECK_EQ(dos_lane_dir("sideways"), -1);
    CHECK_EQ(dos_lane_dir(NULL), -1);
}

static void arrows_turn_by_direction(void) {
    CHECK_EQ(dos_lane_dir_deg(DOS_LANE_STRAIGHT), 0);
    CHECK_EQ(dos_lane_dir_deg(DOS_LANE_RIGHT), 90);
    CHECK_EQ(dos_lane_dir_deg(DOS_LANE_LEFT), -90);
    CHECK_EQ(dos_lane_dir_deg(DOS_LANE_SLIGHT_LEFT), -45);
    CHECK_EQ(dos_lane_dir_deg(DOS_LANE_UTURN), 180);
    CHECK_EQ(dos_lane_dir_deg(-1), 0);
}

static void the_active_arrow_is_drawn_last(void) {
    int out[DOS_LANE_DIR_COUNT];
    uint16_t m = (1u << DOS_LANE_STRAIGHT) | (1u << DOS_LANE_RIGHT);
    CHECK_EQ(dos_lane_draw_order(m, DOS_LANE_STRAIGHT, out), 2);
    CHECK_EQ(out[0], DOS_LANE_RIGHT);
    CHECK_EQ(out[1], DOS_LANE_STRAIGHT);
    CHECK_EQ(dos_lane_draw_order(m, -1, out), 2);
    CHECK_EQ(out[0], DOS_LANE_STRAIGHT);
    CHECK_EQ(dos_lane_draw_order(m, DOS_LANE_LEFT, out), 2);   /* an active arrow the lane lacks is ignored */
    CHECK_EQ(dos_lane_draw_order(0, -1, out), 0);
}

static void a_short_row_keeps_its_cell_width(void) {
    dos_lane_row_t r;
    dos_lane_row(&r, 4, 8, 400, 44, 24, 4);
    CHECK_EQ(r.n, 4);
    CHECK_EQ(r.cell_w, 44);
    CHECK_EQ(r.total_w, 4 * 44 + 3 * 4);
    CHECK_EQ(dos_lane_cell_x(&r, 0), 0);
    CHECK_EQ(dos_lane_cell_x(&r, 3), 3 * 48);
}

static void a_long_row_shrinks_to_fit_but_not_below_the_minimum(void) {
    dos_lane_row_t r;
    dos_lane_row(&r, 8, 8, 300, 44, 24, 4);
    CHECK_EQ(r.cell_w, (300 - 7 * 4) / 8);
    CHECK(r.total_w <= 300);
    dos_lane_row(&r, 8, 8, 100, 44, 24, 4);
    CHECK_EQ(r.cell_w, 24);
    dos_lane_row(&r, 12, 8, 1000, 44, 24, 4);
    CHECK_EQ(r.n, 8);
    dos_lane_row(&r, 0, 8, 400, 44, 24, 4);
    CHECK_EQ(r.total_w, 0);
}

int main(void) {
    RUN(names_map_to_directions);
    RUN(arrows_turn_by_direction);
    RUN(the_active_arrow_is_drawn_last);
    RUN(a_short_row_keeps_its_cell_width);
    RUN(a_long_row_shrinks_to_fit_but_not_below_the_minimum);
    DONE();
}
