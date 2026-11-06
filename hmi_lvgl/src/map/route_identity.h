#pragma once
#include <stdbool.h>
#include <stdint.h>

/* Whether a route update starts a NEW route (frame it whole) or only replaces the line of the current one (ETA
   refresh, reroute). `id` is the back-end's nav.route.id, stable for a trip; 0 = not sent, then the geometry key
   (first/last point hash) decides. `key == 0 && id == 0` means no route. *last holds the previous identity. */
bool dos_route_is_new(uint32_t *last, uint32_t id, uint32_t geometry_key);

/* What the map was last told about the route, so a snapshot only touches the map when something it shows changed.
   Zero-initialise. The id is part of it: the same destination planned again from the same spot gives the same line
   (so `ver`, the line's hash counter, stays put) under a new id, and the map has to hear that at once. If it waits
   for the next line change, the new id is first seen at an ETA refresh and frames the whole route mid-drive. */
typedef struct {
    uint32_t ver, id, congestion;
    bool geometry;
} dos_route_seen_t;

/* True when the route changed since the last call (line, trip id, traffic colours, or whether there is a route). */
bool dos_route_needs_update(dos_route_seen_t *seen, uint32_t ver, uint32_t id, const char *congestion, bool geometry);
