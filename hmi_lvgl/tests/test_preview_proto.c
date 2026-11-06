#include "dos_test.h"
#include "preview_proto.h"
#include <string.h>

static size_t frame(uint8_t *b, uint16_t w, uint16_t h, uint8_t flags, uint32_t seq, const uint16_t rect[4])
{
    memset(b, 0, PV_HEADER_SIZE + (size_t)w * h * 2);
    memcpy(b, "DVPV", 4);
    b[4] = 1;
    b[5] = flags;
    b[8] = (uint8_t)seq; b[9] = (uint8_t)(seq >> 8); b[10] = (uint8_t)(seq >> 16); b[11] = (uint8_t)(seq >> 24);
    b[12] = (uint8_t)w; b[13] = (uint8_t)(w >> 8); b[14] = (uint8_t)h; b[15] = (uint8_t)(h >> 8);
    if(rect)
        for(int i = 0; i < 4; i++) { b[16 + 2 * i] = (uint8_t)rect[i]; b[17 + 2 * i] = (uint8_t)(rect[i] >> 8); }
    return PV_HEADER_SIZE + (size_t)w * h * 2;
}

static uint8_t buf[PV_HEADER_SIZE + 320 * 320 * 2 + 16];

static void t_parses_a_frame_with_a_rect(void)
{
    const uint16_t r[4] = { 30, 40, 100, 120 };
    size_t n = frame(buf, 240, 240, 1, 7, r);
    pv_hdr_t h;
    CHECK(pv_parse(buf, n, &h));
    CHECK_EQ(h.seq, 7);
    CHECK_EQ(h.w, 240);
    CHECK_EQ(h.h, 240);
    CHECK(h.has_rect);
    CHECK_EQ(h.rx, 30); CHECK_EQ(h.ry, 40); CHECK_EQ(h.rw, 100); CHECK_EQ(h.rh, 120);
    CHECK_EQ(h.payload, 240 * 240 * 2);
}

static void t_parses_the_golden_header_python_writes(void)
{
    /* the bytes of tests/test_face_preview.py::GOLDEN_HEADER: seq 0x01020304, 240 x 240, rect 1 2 3 4 */
    static const uint8_t g[PV_HEADER_SIZE] = { 'D', 'V', 'P', 'V', 1, 1, 0, 0, 0x04, 0x03, 0x02, 0x01, 0xf0, 0, 0xf0, 0,
                                               1, 0, 2, 0, 3, 0, 4, 0 };
    memset(buf, 0, sizeof buf);
    memcpy(buf, g, sizeof g);
    pv_hdr_t h;
    CHECK(pv_parse(buf, PV_HEADER_SIZE + 240 * 240 * 2, &h));
    CHECK_EQ(h.seq, 0x01020304);
    CHECK_EQ(h.w, 240); CHECK_EQ(h.h, 240);
    CHECK(h.has_rect);
    CHECK_EQ(h.rx, 1); CHECK_EQ(h.ry, 2); CHECK_EQ(h.rw, 3); CHECK_EQ(h.rh, 4);
}

static void t_rect_flag_clear_means_no_rect(void)
{
    const uint16_t r[4] = { 1, 2, 3, 4 };
    size_t n = frame(buf, 8, 8, 0, 1, r);
    pv_hdr_t h;
    CHECK(pv_parse(buf, n, &h));
    CHECK(!h.has_rect);
}

static void t_empty_rect_is_no_rect(void)
{
    const uint16_t r[4] = { 5, 5, 0, 9 };
    size_t n = frame(buf, 8, 8, 1, 1, r);
    pv_hdr_t h;
    CHECK(pv_parse(buf, n, &h));
    CHECK(!h.has_rect);
}

static void t_rejects_garbage_and_never_overreads(void)
{
    size_t n = frame(buf, 16, 16, 0, 1, NULL);
    pv_hdr_t h;
    CHECK(!pv_parse(NULL, n, &h));
    CHECK(!pv_parse(buf, 0, &h));
    CHECK(!pv_parse(buf, PV_HEADER_SIZE - 1, &h));
    CHECK(!pv_parse(buf, n - 1, &h));          /* one pixel byte short */
    uint8_t bad[PV_HEADER_SIZE + 8];
    memcpy(bad, buf, sizeof bad);
    bad[0] = 'X';
    CHECK(!pv_parse(bad, sizeof bad, &h));
    memcpy(bad, buf, sizeof bad);
    bad[4] = 2;                                  /* unknown version */
    CHECK(!pv_parse(bad, sizeof bad, &h));
    n = frame(buf, 0, 16, 0, 1, NULL);
    CHECK(!pv_parse(buf, n, &h));                /* zero width */
    n = frame(buf, 16, 0, 0, 1, NULL);
    CHECK(!pv_parse(buf, n, &h));
    n = frame(buf, 400, 16, 0, 1, NULL);
    CHECK(!pv_parse(buf, PV_HEADER_SIZE + 64, &h));   /* header claims 400 wide, but only 64 bytes follow */
}

static void t_oversize_is_refused(void)
{
    memset(buf, 0, sizeof buf);
    memcpy(buf, "DVPV", 4);
    buf[4] = 1;
    buf[12] = 0x2C; buf[13] = 0x01;              /* 300 wide: fine */
    buf[14] = 0x2C; buf[15] = 0x01;
    pv_hdr_t h;
    CHECK(pv_parse(buf, PV_HEADER_SIZE + 300 * 300 * 2, &h));
    buf[12] = 0x60; buf[13] = 0x01;              /* 352 > PV_MAX_SIDE */
    CHECK(!pv_parse(buf, sizeof buf, &h));
}

static void t_rect_follow_glides(void)
{
    pv_hdr_t h = { .has_rect = true, .rx = 100, .ry = 100, .rw = 50, .rh = 50 };
    pv_rect_t r = { 0 };
    pv_rect_follow(&r, &h, 0.5f);
    CHECK(r.valid);
    CHECK_EQ((int)r.x, 100);                      /* the first rect is taken as is */
    h.rx = 120;
    pv_rect_follow(&r, &h, 0.5f);
    CHECK_EQ((int)r.x, 110);
    h.has_rect = false;
    pv_rect_follow(&r, &h, 0.5f);
    CHECK(!r.valid);
}

int main(void)
{
    RUN(t_parses_a_frame_with_a_rect);
    RUN(t_parses_the_golden_header_python_writes);
    RUN(t_rect_flag_clear_means_no_rect);
    RUN(t_empty_rect_is_no_rect);
    RUN(t_rejects_garbage_and_never_overreads);
    RUN(t_oversize_is_refused);
    RUN(t_rect_follow_glides);
    DONE();
}
