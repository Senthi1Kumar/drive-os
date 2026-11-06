#pragma once
#include <stdbool.h>
#include <stdint.h>

/* Lane guidance: the indication names Mapbox Directions sends in bannerInstructions lane components, the arrow each
   one draws as, and how a row of lanes is laid out. Pure: no LVGL. */
typedef enum {
    DOS_LANE_SHARP_LEFT, DOS_LANE_LEFT, DOS_LANE_SLIGHT_LEFT, DOS_LANE_STRAIGHT,
    DOS_LANE_SLIGHT_RIGHT, DOS_LANE_RIGHT, DOS_LANE_SHARP_RIGHT, DOS_LANE_UTURN,
    DOS_LANE_DIR_COUNT
} dos_lane_dir_t;

/* "left", "slight right", "uturn", ... -> direction; -1 for "none" and for names it does not know. */
int dos_lane_dir(const char *name);
/* Degrees clockwise from an arrow pointing up; a u-turn is drawn as a down arrow. */
int dos_lane_dir_deg(int dir);
/* The direction bits of a lane (bit d = 1 << dir) in the order they are drawn: every dim direction first, the
   active one last so it sits on top. out has room for DOS_LANE_DIR_COUNT; returns how many were written. */
int dos_lane_draw_order(uint16_t mask, int active_dir, int *out);

typedef struct {
    int n, cell_w, gap, total_w;
} dos_lane_row_t;

/* n lanes of up to cell_w px, gap px apart, shrunk (never below min_cell_w) to fit max_w. n is capped at max_lanes. */
void dos_lane_row(dos_lane_row_t *out, int n, int max_lanes, int max_w, int cell_w, int min_cell_w, int gap);
int dos_lane_cell_x(const dos_lane_row_t *row, int i);   /* left edge of lane i */
