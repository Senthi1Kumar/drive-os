#include "map/route_identity.h"

bool dos_route_is_new(uint32_t *last, uint32_t id, uint32_t geometry_key)
{
    uint32_t key = id ? ((id * 2654435761u) ^ 0x9e3779b9u) | 1u : geometry_key;
    if(!id && !geometry_key) key = 0;
    bool fresh = key && key != *last;
    *last = key;
    return fresh;
}

bool dos_route_needs_update(dos_route_seen_t *seen, uint32_t ver, uint32_t id, const char *congestion, bool geometry)
{
    uint32_t h = 2166136261u;
    for(const char *c = congestion ? congestion : ""; *c; c++) h = (h ^ (unsigned char)*c) * 16777619u;
    if(!congestion || !*congestion) h = 0;
    bool changed = ver != seen->ver || id != seen->id || h != seen->congestion || geometry != seen->geometry;
    seen->ver = ver;
    seen->id = id;
    seen->congestion = h;
    seen->geometry = geometry;
    return changed;
}
