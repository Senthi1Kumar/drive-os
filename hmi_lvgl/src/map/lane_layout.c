#include "map/lane_layout.h"
#include <string.h>

int dos_lane_dir(const char *name)
{
    static const struct { const char *n; int d; } t[] = {
        { "sharp left", DOS_LANE_SHARP_LEFT }, { "left", DOS_LANE_LEFT }, { "slight left", DOS_LANE_SLIGHT_LEFT },
        { "straight", DOS_LANE_STRAIGHT }, { "slight right", DOS_LANE_SLIGHT_RIGHT }, { "right", DOS_LANE_RIGHT },
        { "sharp right", DOS_LANE_SHARP_RIGHT }, { "uturn", DOS_LANE_UTURN },
    };
    if(!name) return -1;
    for(size_t i = 0; i < sizeof t / sizeof *t; i++)
        if(!strcmp(name, t[i].n)) return t[i].d;
    return -1;
}

int dos_lane_dir_deg(int dir)
{
    static const int deg[DOS_LANE_DIR_COUNT] = { -135, -90, -45, 0, 45, 90, 135, 180 };
    return dir >= 0 && dir < DOS_LANE_DIR_COUNT ? deg[dir] : 0;
}

int dos_lane_draw_order(uint16_t mask, int active_dir, int *out)
{
    int n = 0;
    for(int d = 0; d < DOS_LANE_DIR_COUNT; d++)
        if((mask & (1u << d)) && d != active_dir) out[n++] = d;
    if(active_dir >= 0 && active_dir < DOS_LANE_DIR_COUNT && (mask & (1u << active_dir))) out[n++] = active_dir;
    return n;
}

void dos_lane_row(dos_lane_row_t *out, int n, int max_lanes, int max_w, int cell_w, int min_cell_w, int gap)
{
    if(n > max_lanes) n = max_lanes;
    if(n < 0) n = 0;
    out->n = n;
    out->gap = gap;
    int w = cell_w;
    if(n > 0 && n * w + (n - 1) * gap > max_w) {
        w = (max_w - (n - 1) * gap) / n;
        if(w < min_cell_w) w = min_cell_w;
    }
    out->cell_w = w;
    out->total_w = n ? n * w + (n - 1) * gap : 0;
}

int dos_lane_cell_x(const dos_lane_row_t *row, int i) { return i * (row->cell_w + row->gap); }
