#!/usr/bin/env bash
# Headless review screenshots: scripts/shots.sh OUT_DIR [build/driveos_hmi]
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
OUT=${1:?out dir}; BIN=${2:-$HERE/build/driveos_hmi}
cd "$HERE"          # fixtures with images use a relative image_root (fixtures/assets)
mkdir -p "$OUT"
shot() { "$BIN" --fixture "$HERE/fixtures/$1.json" --screen "$2" --screenshot "$OUT/$2_$1.png" >/dev/null && echo "$OUT/$2_$1.png"; }
shot home home
shot home_busy home
shot agent_idle agent
shot agent_listening agent
shot agent_thinking agent
shot agent_speaking agent
shot home about
shot about_update about
for f in shop_places shop_empty shop_menu shop_menu_cart shop_menu_pizza shop_menu_burger shop_item shop_item_img shop_cart shop_cart_empty checkout_choose checkout_voice checkout_digits \
         checkout_verifying checkout_face checkout_otp checkout_accepted checkout_rejected order_preparing; do shot $f shop; done
for f in nav_idle nav_navigating nav_arrived; do shot $f nav; done
shot checkout_otp_phone shop
shot checkout_digits shop
shot checkout_offline shop
for f in checkout_biometric checkout_consent checkout_face_unavailable checkout_face_preview checkout_face_verified checkout_face_failed; do shot $f shop; done
for f in enroll_consent enroll_ready enroll_recording enroll_quality enroll_done enroll_failed; do shot $f enroll; done
