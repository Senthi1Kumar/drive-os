#pragma once
#include "lvgl.h"

/* Nova's presence (ports VoiceOrb.qml): a soft glowing orb that breathes when idle, swells with the mic level while
   listening, ripples while speaking and spins a highlight while thinking. */
lv_obj_t *orb_create(lv_obj_t *parent, int32_t size);
void orb_set(lv_obj_t *orb, const char *state, float level);   /* idle | listening | thinking | speaking */
