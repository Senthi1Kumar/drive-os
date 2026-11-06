#include "dos_test.h"
#include "map/map_follow.h"
#include <math.h>

static bool near(double a, double b, double eps) { return fabs(a - b) <= eps; }

/* 5 Hz samples, 200 ms apart: the car is drawn 1.5 intervals (300 ms) behind and moves every frame between them. */
static void moves_between_samples(void)
{
    map_track_t t = { 0 };
    double lat, lon, h;
    CHECK(!map_track_at(&t, 0, &lat, &lon, &h));
    map_track_push(&t, 37.0000, -122.0000, 0, 1000);
    CHECK(map_track_at(&t, 1100, &lat, &lon, &h) && lat == 37.0000);    /* one sample: held there */
    map_track_push(&t, 37.0001, -122.0000, 10, 1200);
    CHECK(map_track_at(&t, 1300, &lat, &lon, &h) && near(lat, 37.0000, 1e-9));    /* now - 300 ms = first sample */
    CHECK(map_track_at(&t, 1400, &lat, &lon, &h) && near(lat, 37.00005, 1e-9) && near(h, 5, 1e-9));   /* halfway */
    CHECK(map_track_at(&t, 1500, &lat, &lon, &h) && near(lat, 37.0001, 1e-9));
    CHECK(map_track_at(&t, 1900, &lat, &lon, &h) && near(lat, 37.0001, 1e-9));   /* late sample: held, not run past */
    double l1, l2;
    map_track_at(&t, 1350, &l1, &lon, &h);
    map_track_at(&t, 1366, &l2, &lon, &h);
    CHECK(l2 > l1);   /* a 60 fps frame later, the car has moved */
}

/* Samples arriving unevenly (180, then 220 ms) must not make the car surge at each arrival: what is drawn just
   before a sample lands and just after it differ by a sliver, not by a step. */
static void uneven_arrivals_stay_smooth(void)
{
    map_track_t t = { 0 };
    double lat0, lat1, lon, h;
    const double times[] = { 0, 180, 400, 580, 800, 980 };
    for(int k = 0; k < 6; k++) map_track_push(&t, 37.0 + k * 0.0001, -122.0, 0, times[k]);
    map_track_at(&t, 1199, &lat0, &lon, &h);
    map_track_push(&t, 37.0006, -122.0, 0, 1200);
    map_track_at(&t, 1200, &lat1, &lon, &h);
    CHECK(fabs(lat1 - lat0) < 0.00002);   /* under a fifth of a sample's step */
    CHECK(lat1 > lat0 - 1e-7);            /* never visibly backwards: under 1 cm, far below a pixel */
}

/* 10 Hz samples with +-20 ms arrival jitter, drawn at 60 fps as they arrive: the drawn speed wobbles far less
   than the arrival times do. */
static double speed_wobble(bool jitter)
{
    double arrive[60];
    unsigned seed = 7;
    for(int k = 0; k < 60; k++) {
        arrive[k] = k * 100.0;
        if(jitter && k > 0) {
            seed = seed * 1103515245u + 12345u;
            arrive[k] += (double)((seed >> 8) % 41) - 20.0;
        }
    }
    map_track_t t = { 0 };
    double sum = 0, sq = 0, prev_lat = 0, lat, lon, h;
    int n = 0, next = 0;
    for(double now = 0; now < 5800; now += 16.7) {
        while(next < 60 && arrive[next] <= now) {
            map_track_push(&t, 37.0 + next * 0.00001, -122.0, 0, arrive[next]);
            next++;
        }
        if(!map_track_at(&t, now, &lat, &lon, &h)) continue;
        if(now > 1500 && prev_lat != 0) {   /* past the start-up */
            double v = (lat - prev_lat) / 16.7;
            sum += v;
            sq += v * v;
            n++;
        }
        prev_lat = lat;
    }
    double mean = sum / n;
    return sqrt(fmax(sq / n - mean * mean, 0)) / mean;
}

static void arrival_jitter_is_smoothed(void)
{
    CHECK(speed_wobble(false) < 0.01);   /* an even feed is drawn at an even speed */
    CHECK(speed_wobble(true) < 0.05);    /* +-20 ms in 100 ms is ~0.2 stamped raw */
}

/* A real change of rate (5 Hz to 10 Hz) is followed, not smoothed away. */
static void rate_changes_are_followed(void)
{
    map_track_t t = { 0 };
    for(int k = 0; k < 10; k++) map_track_push(&t, 37.0 + k * 1e-5, -122.0, 0, k * 200.0);
    for(int k = 1; k <= 30; k++) map_track_push(&t, 37.0001 + k * 1e-5, -122.0, 0, 1800 + k * 100.0);
    CHECK(t.interval > 95 && t.interval < 110);
    CHECK(fabs(t.s[t.n - 1].t_ms - 4800) < 40);   /* the stamps have caught up with the arrivals */
}

static void jumps_are_not_motion(void)
{
    map_track_t t = { 0 };
    double lat, lon, h;
    map_track_push(&t, 37.0, -122.0, 0, 1000);
    map_track_push(&t, 38.0, -122.0, 0, 1200);   /* 111 km: a new trip, not a drive */
    CHECK(t.n == 1);
    CHECK(map_track_at(&t, 1250, &lat, &lon, &h) && lat == 38.0);
    for(int k = 1; k <= 6; k++) map_track_push(&t, 38.0 + k * 1e-5, -122.0, 0, 1200 + k * 200);
    CHECK(t.n == MAP_TRACK_N);   /* a bounded history */
    map_track_push(&t, 38.0001, -122.0, 0, 1100);   /* an older timestamp starts over too */
    CHECK(t.n == 1);
}

static void headings_turn_the_short_way(void)
{
    CHECK(near(map_angle_lerp(350, 10, 0.5), 0, 1e-9));
    CHECK(near(map_angle_lerp(10, 350, 0.5), 0, 1e-9));
    CHECK(near(map_angle_lerp(90, 270, 0.5), 180, 1e-9));
    CHECK(near(map_angle_norm(-30), 330, 1e-9) && near(map_angle_norm(725), 5, 1e-9));
    map_track_t t = { 0 };
    double lat, lon, h;
    map_track_push(&t, 37.0, -122.0, 359, 0);
    map_track_push(&t, 37.0, -122.0, 1, 200);
    map_track_at(&t, 400, &lat, &lon, &h);
    CHECK(near(h, 0, 1e-9));   /* through north, not all the way round */
}

static void easing(void)
{
    CHECK(near(map_approach(0, 10, 0, 100), 0, 1e-12));
    CHECK(near(map_approach(0, 10, 100, 100), 10 * (1 - exp(-1.0)), 1e-9));
    CHECK(map_approach(0, 10, 1000, 100) > 9.99);
    CHECK(near(map_angle_approach(350, 10, 1e6, 100), 10, 1e-6));
    double a = map_angle_approach(350, 10, 100, 100);   /* partway, through north */
    CHECK(a > 350 || a < 10);
    CHECK(map_smoothstep(-1) == 0 && map_smoothstep(2) == 1 && near(map_smoothstep(0.5), 0.5, 1e-12));
    CHECK(near(map_distance_m(37.0, -122.0, 37.001, -122.0), 111.2, 0.5));
}

/* The sender's stamps, not the arrival times, set the spacing: packets landing up to 150 ms late (never early
   of the fastest) leave the car moving at one steady speed, and a second sender clock offset changes nothing. */
static void sender_stamps_beat_arrival_jitter(void)
{
    map_clock_t c = { 0 };
    CHECK(map_clock_local(&c, 0, 5000) == 5000);   /* unstamped: arrival time */
    map_track_t t = { 0 };
    const double late[] = { 0, 140, 20, 90, 150, 10, 60, 130, 0, 100, 30, 80 };
    double prev = 0, lo = 1e9, hi = 0, lat, lon, h;
    int k = 0;
    for(double now = 20000; now < 26000; now += 16.7) {   /* one clock; fixes land when their packet does */
        while(k < 12 && now >= 20000 + k * 500.0 + late[k]) {   /* 2 Hz, 25 m/s north: 0.00022 deg per fix */
            map_track_push_exact(&t, 37.0 + k * 0.00022, -122.0, 0, map_clock_local(&c, 777000 + k * 500.0, now));
            k++;
        }
        if(now < 23500 || !map_track_at(&t, now, &lat, &lon, &h)) continue;
        if(prev > 0 && now < 25500) { double d = lat - prev; lo = d < lo ? d : lo; hi = d > hi ? d : hi; }
        prev = lat;
    }
    double step = 0.00022 / 500.0 * 16.7;   /* the per-frame step at constant speed */
    CHECK(lo > step * 0.75 && hi < step * 1.25);   /* no stalls, no surges */
}

/* The UI push is gated at 0.5 s over a 10 Hz source, so stamped fixes are 500-600 ms apart: the car still moves at
   the true speed, not at the mean. */
static void uneven_sender_pacing_keeps_true_speed(void)
{
    map_clock_t c = { 0 };
    map_track_t t = { 0 };
    const double gap[] = { 0, 500, 600, 500, 600, 500, 600, 500, 600, 500, 600, 500 };
    double src = 5000, lat, lon, h, prev = 0, lo = 1e9, hi = 0;
    int k = 0;
    double due = 5000, pos = 0;
    for(double now = 90000; now < 97000; now += 16.7) {
        while(k < 12 && now >= 90000 + (due - 5000)) {
            src = due;
            pos = (src - 5000) * (25.0 / 1000.0) / 111320.0;   /* 25 m/s north */
            map_track_push_exact(&t, 37.0 + pos, -122.0, 0, map_clock_local(&c, src, now));
            k++;
            due += k < 12 ? gap[k] : 0;
        }
        if(now < 93500 || !map_track_at(&t, now, &lat, &lon, &h)) continue;
        if(prev > 0 && now < 96500) { double d = lat - prev; lo = d < lo ? d : lo; hi = d > hi ? d : hi; }
        prev = lat;
    }
    double step = 25.0 / 111320.0 * 0.0167;
    CHECK(lo > step * 0.8 && hi < step * 1.2);   /* one frame in a sample dips by a few percent at most */
}

static void a_long_gap_starts_over(void)
{
    map_track_t t = { 0 };
    double lat, lon, h;
    map_track_push_exact(&t, 37.0, -122.0, 0, 1000);
    map_track_push_exact(&t, 37.00001, -122.0, 0, 1500);
    map_track_push_exact(&t, 37.00002, -122.0, 0, 60000);   /* parked a minute, then moving */
    CHECK(t.n == 1);
    CHECK(map_track_at(&t, 60100, &lat, &lon, &h) && near(lat, 37.00002, 1e-9));
}

static void clock_follows_the_fastest_arrival(void)
{
    map_clock_t c = { 0 };
    double a = map_clock_local(&c, 1000, 9000);
    CHECK(near(a, 9000, 1e-9));
    CHECK(near(map_clock_local(&c, 1500, 9700), 9500, 15));   /* 200 ms late: placed where it was due, +2 ms */
    CHECK(near(map_clock_local(&c, 2000, 9900), 9900, 1e-9));   /* a faster path re-anchors */
    double r = map_clock_local(&c, 10, 500000);   /* sender restarted: its clock went back */
    CHECK(near(r, 500000, 1e-9));
}

int main(void)
{
    RUN(sender_stamps_beat_arrival_jitter);
    RUN(clock_follows_the_fastest_arrival);
    RUN(uneven_sender_pacing_keeps_true_speed);
    RUN(a_long_gap_starts_over);
    RUN(moves_between_samples);
    RUN(uneven_arrivals_stay_smooth);
    RUN(arrival_jitter_is_smoothed);
    RUN(rate_changes_are_followed);
    RUN(jumps_are_not_motion);
    RUN(headings_turn_the_short_way);
    RUN(easing);
    DONE();
}
