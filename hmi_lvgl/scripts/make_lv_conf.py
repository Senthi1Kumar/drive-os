#!/usr/bin/env python3
"""Regenerate lv_conf.h from LVGL's v9.6 template plus hmi_lvgl's overrides.

Usage: scripts/make_lv_conf.py <path/to/lvgl/lv_conf_template.h> [out=lv_conf.h]

Every override replaces the template's own `#define <SYMBOL>` line in place (the
template stays the single source of every other default). A symbol missing from
the template is an error, so a renamed LVGL option fails loudly instead of being
silently ignored.
"""
import re
import sys
from pathlib import Path

# The renderer is chosen at configure time: CMake defines DOS_WITH_DRM (0/1) on
# the lvgl target, so LVGL and the app always see the same value.
OVERRIDES = {
    "LV_COLOR_FORMAT_DEFAULT": "LV_COLOR_FORMAT_XRGB8888",
    "LV_USE_STDLIB_MALLOC": "LV_STDLIB_CLIB",
    "LV_USE_STDLIB_STRING": "LV_STDLIB_CLIB",
    "LV_USE_STDLIB_SPRINTF": "LV_STDLIB_CLIB",
    "LV_DEF_REFR_PERIOD": "33",
    "LV_DRAW_SW_COMPLEX": "1",
    "LV_GRADIENT_MAX_STOPS": "3",
    "LV_USE_DRAW_SW_COMPLEX_GRADIENTS": "1",
    "LV_DRAW_SW_SHADOW_CACHE_SIZE": "64",
    "LV_USE_FLOAT": "1",
    "LV_USE_SNAPSHOT": "1",
    "LV_USE_LOG": "1",
    "LV_LOG_LEVEL": "LV_LOG_LEVEL_WARN",
    "LV_LOG_PRINTF": "1",
    "LV_USE_SDL": "1",
    "LV_SDL_AUTO_BACKEND": "0",
    "LV_SDL_BACKEND": "LV_SDL_BACKEND_SW",
    "LV_SDL_BUF_COUNT": "2",
    "LV_USE_LINUX_DRM": "DOS_WITH_DRM",
    "LV_USE_EVDEV": "DOS_WITH_DRM",
    "LV_USE_THEME_DEFAULT": "0",
}

RENDER_DEFAULT = (
    "\n/* Set by CMake (-DDOS_WITH_DRM=1 when libdrm is present); 0 when a file is compiled outside the build. */\n"
    "#ifndef DOS_WITH_DRM\n#define DOS_WITH_DRM 0\n#endif\n"
)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    src = Path(sys.argv[1]).read_text()
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "lv_conf.h")

    text, n = re.subn(r'^#if 0 /\* Set this to "1" to enable content \*/$', "#if 1 /* hmi_lvgl: enabled */", src,
                      count=1, flags=re.M)
    if n != 1:
        print("template enable guard not found", file=sys.stderr)
        return 1
    text, n = re.subn(r"^#define LV_CONF_H$", "#define LV_CONF_H" + RENDER_DEFAULT, text, count=1, flags=re.M)
    if n != 1:
        print("LV_CONF_H guard not found", file=sys.stderr)
        return 1

    missing = []
    for sym, val in OVERRIDES.items():
        text, n = re.subn(rf"^(\s*)#define {sym}\b.*$", rf"\g<1>#define {sym} {val}", text, count=1, flags=re.M)
        if n != 1:
            missing.append(sym)
    if missing:
        print("not in template: " + ", ".join(missing), file=sys.stderr)
        return 1
    out.write_text(text)
    print(f"wrote {out} ({len(OVERRIDES)} overrides)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
