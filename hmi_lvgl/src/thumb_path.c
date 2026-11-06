#include "thumb_path.h"
#include <stdio.h>
#include <sys/stat.h>

bool dos_thumb_path(char *out, size_t cap, const char *root, const char *rel)
{
    if(!out || cap == 0) return false;
    out[0] = 0;
    if(!root || !root[0] || !rel || !rel[0]) return false;
    char fs[512];
    if(snprintf(fs, sizeof fs, "%s/%s", root, rel) >= (int)sizeof fs) return false;
    struct stat st;
    if(stat(fs, &st) != 0 || !S_ISREG(st.st_mode) || st.st_size <= 0) return false;
    if(snprintf(out, cap, "A:%s", fs) >= (int)cap) { out[0] = 0; return false; }
    return true;
}
