#include "thumbs.h"
#include "thumb_path.h"
#include "theme.h"
#include "ui_state.h"
#include <stdio.h>
#include <string.h>

lv_obj_t *dos_thumb_photo(lv_obj_t *thumb, const char *small, const char *large, int32_t w, int32_t h)
{
    const char *root = ui_state()->hello.image_root;
    if(!root[0]) return NULL;
    const char *rel = (w > 120 || h > 120) && large && large[0] ? large : small;
    if(!rel || !rel[0]) rel = large && large[0] ? large : NULL;
    if(!rel) return NULL;
    char path[256];
    if(!dos_thumb_path(path, sizeof path, root, rel)) {
        /* the other size may still be there */
        const char *alt = rel == small ? large : small;
        if(!alt || !alt[0] || !dos_thumb_path(path, sizeof path, root, alt)) return NULL;
    }
    lv_image_header_t hdr;
    if(lv_image_decoder_get_info(path, &hdr) != LV_RESULT_OK || hdr.w == 0 || hdr.h == 0) return NULL;
    lv_obj_t *img = lv_image_create(thumb);
    lv_obj_set_ignore_layout(img, true);
    lv_obj_set_clickable(img, false);
    lv_image_set_src(img, path);
    /* a wide banner (the item sheet) shows the whole square product shot centred; cards crop to fill */
    lv_image_set_inner_align(img, w > 2 * h ? LV_IMAGE_ALIGN_CONTAIN : LV_IMAGE_ALIGN_COVER);
    lv_obj_set_size(img, w, h);
    lv_obj_set_pos(img, 0, 0);
    return img;
}

lv_obj_t *dos_thumb_img(lv_obj_t *parent, int32_t w, int32_t h, int icon, int icon_px, int32_t radius,
                        const char *small, const char *large)
{
    lv_obj_t *t = dos_thumb(parent, w, h, icon, icon_px, radius);
    dos_thumb_photo(t, small, large, w, h);
    return t;
}
