#include "dos_test.h"
#include "thumb_path.h"
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>

static char root[128];

static void put(const char *rel, const char *data)
{
    char p[256];
    snprintf(p, sizeof p, "%s/%s", root, rel);
    FILE *f = fopen(p, "wb");
    if(f) { fputs(data, f); fclose(f); }
}

static void t_existing_file_resolves_with_the_lvgl_drive_letter(void)
{
    char out[256];
    CHECK(dos_thumb_path(out, sizeof out, root, "food/latte_160.jpg"));
    char want[256];
    snprintf(want, sizeof want, "A:%s/food/latte_160.jpg", root);
    CHECK(!strcmp(out, want));
}

static void t_missing_or_empty_files_are_refused(void)
{
    char out[256];
    CHECK(!dos_thumb_path(out, sizeof out, root, "food/nope_160.jpg"));
    CHECK(out[0] == 0);
    CHECK(!dos_thumb_path(out, sizeof out, root, "food/empty.jpg"));
    CHECK(!dos_thumb_path(out, sizeof out, root, "food"));            /* a directory is not an image */
}

static void t_empty_arguments_and_overflow_are_refused(void)
{
    char out[256], tiny[8];
    CHECK(!dos_thumb_path(out, sizeof out, "", "food/latte_160.jpg"));
    CHECK(!dos_thumb_path(out, sizeof out, root, ""));
    CHECK(!dos_thumb_path(out, sizeof out, NULL, NULL));
    CHECK(!dos_thumb_path(tiny, sizeof tiny, root, "food/latte_160.jpg"));
    CHECK(tiny[0] == 0);
    CHECK(!dos_thumb_path(NULL, 0, root, "food/latte_160.jpg"));
}

int main(void)
{
    snprintf(root, sizeof root, "/tmp/dos_thumb_path_%d", (int)getpid());
    char p[160];
    mkdir(root, 0700);
    snprintf(p, sizeof p, "%s/food", root);
    mkdir(p, 0700);
    put("food/latte_160.jpg", "jpeg bytes");
    put("food/empty.jpg", "");
    RUN(t_existing_file_resolves_with_the_lvgl_drive_letter);
    RUN(t_missing_or_empty_files_are_refused);
    RUN(t_empty_arguments_and_overflow_are_refused);
    char cmd[200];
    snprintf(cmd, sizeof cmd, "rm -rf %s", root);
    if(system(cmd)) {}
    DONE();
}
