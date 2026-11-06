#include "dos_test.h"
#include "json.h"
#include "map/map_style.h"
#include <string.h>

static void the_building_layer_is_a_valid_extrusion_over_the_composite_source(void)
{
    char b[2048];
    size_t n = dos_style_buildings_json(b, sizeof b, true, 15);
    CHECK(n > 0 && n == strlen(b));
    cbj_t *j = cbj_parse(b, n);
    CHECK(j != NULL);
    if(!j) return;
    CHECK(!strcmp(cbj_str(cbj_get(j, "type"), ""), "fill-extrusion"));
    CHECK(!strcmp(cbj_str(cbj_get(j, "source"), ""), "composite"));
    CHECK(!strcmp(cbj_str(cbj_get(j, "source-layer"), ""), "building"));
    CHECK(cbj_num(cbj_get(j, "minzoom"), 0) == 15);
    const cbj_t *p = cbj_get(j, "paint");
    CHECK(cbj_num(cbj_get(p, "fill-extrusion-opacity"), 0) == 0.85);
    CHECK(cbj_bool(cbj_get(p, "fill-extrusion-vertical-gradient"), false));
    CHECK(cbj_get(p, "fill-extrusion-height") && cbj_get(p, "fill-extrusion-base") && cbj_get(p, "fill-extrusion-color"));
    CHECK(strstr(b, "\"min_height\"") && strstr(b, "\"height\""));
    cbj_free(j);
}

static void the_day_palette_differs_and_the_min_zoom_follows_the_argument(void)
{
    char a[2048], d[2048];
    CHECK(dos_style_buildings_json(a, sizeof a, true, 16.5) > 0);
    CHECK(dos_style_buildings_json(d, sizeof d, false, 16.5) > 0);
    CHECK(strcmp(a, d) != 0);
    cbj_t *j = cbj_parse(d, strlen(d));
    CHECK(j && cbj_num(cbj_get(j, "minzoom"), 0) == 16.5);
    cbj_free(j);
}

static void a_buffer_that_is_too_small_yields_nothing(void)
{
    char tiny[64];
    CHECK_EQ(dos_style_buildings_json(tiny, sizeof tiny, true, 15), 0);
    CHECK_EQ(dos_style_poi_filter_json(tiny, sizeof tiny), 0);
}

static void the_poi_filter_is_a_let_expression_on_filterrank(void)
{
    char b[1024];
    size_t n = dos_style_poi_filter_json(b, sizeof b);
    CHECK(n > 0);
    cbj_t *j = cbj_parse(b, n);
    CHECK(j != NULL);
    if(!j) return;
    CHECK(j->type == CBJ_ARR && !strcmp(cbj_str(cbj_at(j, 0), ""), "let"));
    CHECK(strstr(b, "filterrank") != NULL);
    cbj_free(j);
}

int main(void)
{
    RUN(the_building_layer_is_a_valid_extrusion_over_the_composite_source);
    RUN(the_day_palette_differs_and_the_min_zoom_follows_the_argument);
    RUN(a_buffer_that_is_too_small_yields_nothing);
    RUN(the_poi_filter_is_a_let_expression_on_filterrank);
    DONE();
}
