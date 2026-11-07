#include "dos_test.h"
#include "ui_state.h"
#include <stdio.h>
#include <string.h>

static const char *root;

static void load(const char *name)
{
    char path[512];
    snprintf(path, sizeof path, "%s/fixtures/%s.json", root, name);
    CHECK(ui_state_load_fixture(path));
}

static void t_idle_and_null_session(void)
{
    load("face_idle");
    const ui_state_t *s = ui_state();
    CHECK(s->face_id.enrolled && !s->face_id.linked);
    CHECK(!strcmp(s->face_id.name, "Sam"));
    CHECK(!s->enroll_face.active);
}

static void t_capturing(void)
{
    load("face_capturing");
    const ui_state_t *s = ui_state();
    CHECK(s->enroll_face.active);
    CHECK(!strcmp(s->enroll_face.operation, "enroll"));
    CHECK(!strcmp(s->enroll_face.name, "Sam"));
    CHECK(!strcmp(s->enroll_face.stage, "capturing"));
}

static void t_link_and_delete_operations(void)
{
    load("face_link_confirm");
    CHECK(!strcmp(ui_state()->enroll_face.stage, "link_confirm"));
    load("face_delete");
    CHECK(!strcmp(ui_state()->enroll_face.operation, "delete"));
    CHECK(!strcmp(ui_state()->enroll_face.stage, "deleting"));
}

static void t_profile_list_and_selection(void)
{
    load("face_profiles");
    const ui_state_t *s = ui_state();
    CHECK_EQ(s->face_profiles.n, 2);
    CHECK_EQ(s->face_profiles.max, 5);
    CHECK(!strcmp(s->face_profiles.selected_id, "profile2"));
    CHECK(!strcmp(s->face_profiles.items[0].name, "Sam"));
    CHECK(!strcmp(s->face_profiles.items[0].role, "driver"));
    CHECK(!s->face_profiles.items[0].linked);
    CHECK(!strcmp(s->face_profiles.items[1].name, "Bob"));
    CHECK(!strcmp(s->face_profiles.items[1].role, "passenger"));
    CHECK(s->face_profiles.items[1].linked);
    load("face_profiles_full");
    CHECK_EQ(ui_state()->face_profiles.n, 5);
    CHECK(!strcmp(ui_state()->face_profiles.selected_id, "p3"));
}

static void t_terminal_states_and_errors(void)
{
    load("face_done");
    CHECK(!strcmp(ui_state()->enroll_face.stage, "done"));
    load("face_failed");
    CHECK(!strcmp(ui_state()->enroll_face.stage, "failed"));
    CHECK(strstr(ui_state()->enroll_face.error, "camera") != NULL);
}

int main(int argc, char **argv)
{
    CHECK(argc == 2);
    root = argv[1];
    RUN(t_idle_and_null_session);
    RUN(t_capturing);
    RUN(t_link_and_delete_operations);
    RUN(t_profile_list_and_selection);
    RUN(t_terminal_states_and_errors);
    DONE();
}
