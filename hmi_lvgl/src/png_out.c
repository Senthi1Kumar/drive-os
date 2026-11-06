#include "png_out.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint32_t crc_table[256];

static void crc_init(void)
{
    for(uint32_t n = 0; n < 256; n++) {
        uint32_t c = n;
        for(int k = 0; k < 8; k++) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
        crc_table[n] = c;
    }
}

static uint32_t crc_upd(uint32_t crc, const uint8_t *p, size_t n)
{
    for(size_t i = 0; i < n; i++) crc = crc_table[(crc ^ p[i]) & 0xff] ^ (crc >> 8);
    return crc;
}

static void be32(uint8_t *o, uint32_t v) { o[0] = (uint8_t)(v >> 24); o[1] = (uint8_t)(v >> 16); o[2] = (uint8_t)(v >> 8); o[3] = (uint8_t)v; }

static void chunk(FILE *f, const char *type, const uint8_t *data, size_t len)
{
    uint8_t h[8];
    be32(h, (uint32_t)len);
    memcpy(h + 4, type, 4);
    fwrite(h, 1, 8, f);
    if(len) fwrite(data, 1, len, f);
    uint32_t crc = crc_upd(0xffffffffu, (const uint8_t *)type, 4);
    if(len) crc = crc_upd(crc, data, len);
    uint8_t t[4];
    be32(t, ~crc);
    fwrite(t, 1, 4, f);
}

bool png_snapshot(lv_obj_t *obj, const char *path)
{
    lv_draw_buf_t *buf = lv_snapshot_take(obj, LV_COLOR_FORMAT_XRGB8888);
    if(!buf) return false;
    uint32_t w = buf->header.w, h = buf->header.h, stride = buf->header.stride;
    size_t raw_len = (size_t)(w * 3 + 1) * h;
    uint8_t *raw = malloc(raw_len);
    if(!raw) {
        lv_draw_buf_destroy(buf);
        return false;
    }
    for(uint32_t y = 0; y < h; y++) {
        uint8_t *o = raw + (size_t)y * (w * 3 + 1);
        const uint8_t *row = buf->data + (size_t)y * stride;
        *o++ = 0;
        for(uint32_t x = 0; x < w; x++) { *o++ = row[x * 4 + 2]; *o++ = row[x * 4 + 1]; *o++ = row[x * 4]; }
    }
    lv_draw_buf_destroy(buf);

    /* zlib stream of stored blocks */
    size_t nblk = (raw_len + 65534) / 65535;
    size_t z_len = 2 + raw_len + nblk * 5 + 4;
    uint8_t *z = malloc(z_len);
    if(!z) { free(raw); return false; }
    size_t zp = 0;
    z[zp++] = 0x78; z[zp++] = 0x01;
    uint32_t a = 1, b = 0;
    for(size_t off = 0; off < raw_len; off += 65535) {
        size_t n = raw_len - off < 65535 ? raw_len - off : 65535;
        z[zp++] = (off + n == raw_len) ? 1 : 0;
        z[zp++] = (uint8_t)(n & 0xff); z[zp++] = (uint8_t)(n >> 8);
        z[zp++] = (uint8_t)(~n & 0xff); z[zp++] = (uint8_t)((~n >> 8) & 0xff);
        memcpy(z + zp, raw + off, n);
        for(size_t i = 0; i < n; i++) { a = (a + raw[off + i]) % 65521u; b = (b + a) % 65521u; }
        zp += n;
    }
    be32(z + zp, (b << 16) | a);
    zp += 4;
    free(raw);

    FILE *f = fopen(path, "wb");
    if(!f) { free(z); return false; }
    crc_init();
    static const uint8_t sig[8] = { 0x89, 'P', 'N', 'G', 0x0d, 0x0a, 0x1a, 0x0a };
    fwrite(sig, 1, 8, f);
    uint8_t ihdr[13];
    be32(ihdr, w); be32(ihdr + 4, h);
    ihdr[8] = 8; ihdr[9] = 2; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0;
    chunk(f, "IHDR", ihdr, 13);
    chunk(f, "IDAT", z, zp);
    chunk(f, "IEND", NULL, 0);
    fclose(f);
    free(z);
    return true;
}
