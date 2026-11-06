#!/usr/bin/env bash
# Generate DriveOS's bitmap fonts from the bundled Manrope TTFs (SIL OFL 1.1). Needs Node (npx); the generated
# fonts/dos_*.c are committed so the Pi needs no Node.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
TTF=${DOS_FONT_DIR:-$HERE/../../src/driveos/ui/assets/fonts}
RANGE=0x20-0x7E,0xA0-0xFF,0x2013,0x2014,0x2018,0x2019,0x2022,0x201C,0x201D,0x2026,0x2190,0x2192

gen() {  # name ttf size
  npx -y lv_font_conv@1.5.3 --bpp 4 --format lvgl --no-compress --lv-include lvgl.h \
    --font "$TTF/$2" --size "$3" --range "${4:-$RANGE}" --lv-font-name "$1" -o "$HERE/$1.c"
  echo "$1 <- $2 @ $3"
}
gen dos_manrope_11 Manrope_800ExtraBold.ttf 11
gen dos_manrope_13 Manrope_600SemiBold.ttf 13
gen dos_manrope_15 Manrope_700Bold.ttf 15
gen dos_manrope_17 Manrope_700Bold.ttf 17
gen dos_manrope_30 Manrope_800ExtraBold.ttf 30
gen dos_manrope_46 Manrope_800ExtraBold.ttf 46
gen dos_manrope_64 Manrope_800ExtraBold.ttf 64 0x20-0x7E
