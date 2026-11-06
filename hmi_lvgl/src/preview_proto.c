#include "preview_proto.h"
#include <string.h>

static uint16_t le16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }
static uint32_t le32(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }

bool pv_parse(const uint8_t *buf, size_t len, pv_hdr_t *out)
{
    if(!buf || len < PV_HEADER_SIZE || memcmp(buf, "DVPV", 4) != 0 || buf[4] != 1) return false;
    pv_hdr_t h = { 0 };
    h.has_rect = (buf[5] & 1) != 0;
    h.seq = le32(buf + 8);
    h.w = le16(buf + 12);
    h.h = le16(buf + 14);
    h.rx = le16(buf + 16);
    h.ry = le16(buf + 18);
    h.rw = le16(buf + 20);
    h.rh = le16(buf + 22);
    if(h.w == 0 || h.h == 0 || h.w > PV_MAX_SIDE || h.h > PV_MAX_SIDE) return false;
    h.payload = (size_t)h.w * h.h * 2;
    if(len - PV_HEADER_SIZE < h.payload) return false;
    if(h.has_rect && (h.rw == 0 || h.rh == 0)) h.has_rect = false;
    if(out) *out = h;
    return true;
}

void pv_rect_follow(pv_rect_t *cur, const pv_hdr_t *hdr, float a)
{
    if(!hdr->has_rect) {
        cur->valid = false;
        return;
    }
    if(!cur->valid || a >= 1.0f) {
        cur->x = hdr->rx; cur->y = hdr->ry; cur->w = hdr->rw; cur->h = hdr->rh;
        cur->valid = true;
        return;
    }
    cur->x += a * ((float)hdr->rx - cur->x);
    cur->y += a * ((float)hdr->ry - cur->y);
    cur->w += a * ((float)hdr->rw - cur->w);
    cur->h += a * ((float)hdr->rh - cur->h);
}
