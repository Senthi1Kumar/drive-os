"""Release-facing content and packaging invariants for the first public release."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / ".release" / "overlay"
if not OVERLAY.is_dir():
    OVERLAY = ROOT


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_release_is_1_1_0_everywhere():
    assert 'version = "1.1.0"' in _read(ROOT / "pyproject.toml")
    assert '__version__ = "1.1.0"' in _read(ROOT / "src/driveos/__init__.py")
    export = ROOT / ".release/export.sh"
    if export.is_file():
        assert "RELEASE_VERSION:-1.1.0" in _read(export)
    assert "version-1.1.0-" in _read(OVERLAY / "README.md")
    assert "## 1.1.0 — Face ID settings" in _read(OVERLAY / "CHANGELOG.md")
    assert "## 1.0.2 — audio and cart fixes" in _read(OVERLAY / "CHANGELOG.md")
    assert "## 1.0.1 — AppImage runtime and desktop fixes" in _read(OVERLAY / "CHANGELOG.md")
    assert "## 1.0.0 — initial release" in _read(OVERLAY / "CHANGELOG.md")
    container = _read(ROOT / "src/driveos/container.py")
    assert "from . import __version__" in container
    assert 'f"DriveOS/{__version__} (in-car demo)"' in container


def test_face_id_settings_release_content():
    changelog = _read(OVERLAY / "CHANGELOG.md")
    assert "# DriveOS v1.1.0 Release Notes" in changelog
    assert "Release Date: 2026.11.07" in changelog
    assert "### Enroll and manage Face ID in Settings" in changelog
    assert "### Manage up to five face profiles" in changelog
    assert "Settings → Face ID" in _read(OVERLAY / "README.md")
    install = _read(OVERLAY / "INSTALL.md")
    assert "Settings → Face ID" in install
    assert "up to five" in install and "selected driver" in install
    assert "Add user" in install and "Edit" in install
    assert "~/.local/share/drive-os/f455_users.json" in install
    assert "rsid_py" in install and "dialout" in install and "explicit consent" in install
    assert "face_idle:face" in _read(ROOT / "packaging/appimage/AppRun")


def test_env_example_has_exactly_three_blank_service_keys():
    text = _read(ROOT / ".env.example")
    assignments = re.findall(r"^([A-Z][A-Z0-9_]*)=(.*)$", text, re.M)
    assert assignments == [
        ("DRIVEOS_MAPBOX__TOKEN", ""),
        ("OPENROUTER_API_KEY", ""),
        ("EXA_API_KEY", ""),
    ]


def test_public_docs_cover_only_verified_targets_and_public_clone():
    public = "\n".join(_read(OVERLAY / name) for name in ("README.md", "INSTALL.md", "CHANGELOG.md"))
    assert not re.search(r"Jetson|JetPack|Orin Nano", public, re.I)
    install = _read(OVERLAY / "INSTALL.md")
    assert "DriveOS runs on two targets:" in install
    assert "The repository is private" not in install
    assert "https://github.com/Senthi1Kumar/drive-os.git" in install


def test_voice_security_text_does_not_claim_replays_are_rejected():
    readme = _read(OVERLAY / "README.md")
    privacy = _read(OVERLAY / "PRIVACY.md")
    assert "advisory" in readme.lower()
    assert "advisory" in privacy.lower()
    assert "so a replayed recording is rejected" not in privacy


def test_public_license_does_not_call_public_source_confidential():
    license_text = _read(OVERLAY / "LICENSE").lower()
    assert "confidential" not in license_text
    assert "info@elevatics.com" in license_text


def test_release_legal_documents_have_no_draft_markers():
    for name in ("README.md", "PRIVACY.md", "THIRD_PARTY_NOTICES.md", "LICENSE"):
        text = _read(OVERLAY / name).lower()
        assert "draft" not in text
        assert "pending legal review" not in text


def test_appimage_stages_all_native_and_runtime_resources():
    build = _read(ROOT / "packaging/appimage/build.sh")
    run = _read(ROOT / "packaging/appimage/AppRun")
    assert "usr/lib/driveos/parakeet" in build
    assert "usr/share/drive-os/models" in build
    assert 'cp -a "$ROOT/models/." "$APPDIR/usr/share/drive-os/models/"' in build
    assert (ROOT / "models/SILERO-LICENSE.txt").is_file()
    assert (ROOT / "models/SMART-TURN-LICENSE.txt").is_file()
    assert "usr/share/drive-os/food" in build
    assert "libmaplibre-native-c.so" in build
    assert "--exclude=src/driveos/data/catalog/dataset.json" in build
    assert "--exclude=tests" not in build
    assert "/sbin/ldconfig -p" in build
    assert 'REQUESTED_VERSION="${VERSION:-}"' in build
    assert 'if [ -n "${REQUESTED_VERSION:-}" ]; then' in build
    parakeet_build = _read(ROOT / "scripts/build_parakeet.sh")
    assert '"$OUT/licenses/parakeet.LICENSE"' in parakeet_build
    assert '"$OUT/licenses/ggml.LICENSE"' in parakeet_build
    assert 'find "$WORK/build-shared" -name \'libggml*.so*\' -type f' in parakeet_build
    assert "readelf -d" in parakeet_build and "ORIGIN" in parakeet_build
    assert 'DRIVEOS_PARAKEET_LIB="$HERE/usr/lib/driveos/parakeet/libparakeet.so"' in run
    assert 'DRIVEOS_VOICE__SILERO_MODEL="$HERE/usr/share/drive-os/models/silero_vad.onnx"' in run
    assert 'DRIVEOS_VOICE__SMART_TURN_MODEL="$HERE/usr/share/drive-os/models/smart-turn-v3.2-cpu.onnx"' in run
    assert 'DRIVEOS_IMAGES__GENERATED_DIR="$HERE/usr/share/drive-os/food"' in run


def test_appimage_includes_and_selftests_phonon_runtime():
    build = _read(ROOT / "packaging/appimage/build.sh")
    run = _read(ROOT / "packaging/appimage/AppRun")
    assert 'startswith("ptts")' not in build
    assert "import ptts" in run


def test_appimage_restores_xvf3800_levels():
    build = _read(ROOT / "packaging/appimage/build.sh")
    run = _read(ROOT / "packaging/appimage/AppRun")
    assert 'install -D -m755 "$ROOT/scripts/set_xvf3800_levels.sh" ' \
           '"$APPDIR/usr/share/drive-os/scripts/set_xvf3800_levels.sh"' in build
    assert 'bash "$HERE/usr/share/drive-os/scripts/set_xvf3800_levels.sh" || true' in run
    assert run.count("usr/share/drive-os/scripts/set_xvf3800_levels.sh") >= 2
    assert "install_desktop_entry\nsetup_display\nrestore_audio_levels" in run


def test_appimage_xvf3800_setup_only_runs_for_application_modes():
    run = _read(ROOT / "packaging/appimage/AppRun")
    assert re.search(r"server\)\s+restore_audio_levels;", run)
    assert re.search(r"hmi\)\s+restore_audio_levels;", run)
    selftest = next(line for line in run.splitlines() if line.strip().startswith("selftest)"))
    assert "restore_audio_levels" not in selftest


def test_appimage_xvf3800_setup_is_best_effort():
    run = _read(ROOT / "packaging/appimage/AppRun")
    helper = run.split("restore_audio_levels() {", 1)[-1].split("}", 1)[0]
    assert 'bash "$HERE/usr/share/drive-os/scripts/set_xvf3800_levels.sh" || true' in helper


def test_lvgl_cart_controls_open_editable_cart_before_checkout():
    shop = _read(ROOT / "hmi_lvgl/src/screens/shop.c")
    assert 'static void cmd_view_cart(lv_event_t *e)' in shop
    assert 'link_cmd("view_cart", NULL)' in shop
    assert "lv_obj_add_event_cb(cb, cmd_view_cart" in shop
    assert "lv_obj_add_event_cb(s_cartbar, cmd_view_cart" in shop
    assert 'lv_obj_add_event_cb(po, cmd_checkout' in shop


def test_lvgl_item_sheet_has_cart_entry_before_checkout():
    sheets = _read(ROOT / "hmi_lvgl/src/screens/sheets.c")
    assert "if(s->shop.cart_count > 0)" in sheets
    assert 'link_cmd("view_cart", NULL)' in sheets
    assert "lv_obj_add_event_cb(add, add_clicked" in sheets


def test_appimage_hmi_uses_c23_capable_clang():
    build = _read(ROOT / "packaging/appimage/build.sh")
    assert "dpkg -s cmake clang" in build
    assert "build-essential clang cmake" in build
    assert "CC=clang CFLAGS=" in build


def test_appimage_scrubs_build_host_paths():
    build = _read(ROOT / "packaging/appimage/build.sh")
    assert 'PYTHONDONTWRITEBYTECODE=1 "$PY" -c' in build
    assert 'b"#!/usr/bin/env python3\\n"' in build
    assert 'glob("*.dist-info/direct_url.json")' in build
    assert "-ffile-prefix-map=$W=." in build


def test_release_has_no_companion_parakeet_archive_and_requires_signing():
    workflow = _read(OVERLAY / ".github/workflows/release.yml")
    assert "driveos-parakeet" not in workflow
    assert "MINISIGN_KEY is not set; publishing without signatures" not in workflow
    assert "apt-get install -y --no-install-recommends minisign" not in workflow
    assert "minisign-0.12-linux.tar.gz" in workflow
    assert "9a599b48ba6eb7b1e80f12f36b94ceca7c00b7a5173c95c3efc88d9822957e73" in workflow
    assert re.search(r'if \[ -z "\$MINISIGN_KEY" \].*?exit 1', workflow, re.S)


def test_release_uses_version_only_title_and_structured_notes():
    import subprocess

    workflow = _read(OVERLAY / ".github/workflows/release.yml")
    assert '--title "${GITHUB_REF_NAME#v}"' in workflow
    assert '--title "DriveOS ' not in workflow
    assert "minisign using \\`driveos-minisign.pub\\`." in workflow

    extractor = re.search(r"awk -v v=\"\$BASE_VERSION\" '([^']+)' CHANGELOG\.md > notes\.md", workflow)
    assert extractor
    notes = subprocess.run(
        ["awk", "-v", "v=1.0.2", extractor.group(1), "CHANGELOG.md"],
        cwd=OVERLAY,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "# DriveOS v1.0.2 Release Notes" in notes
    assert "Release Date: 2026.11.06" in notes
    assert "## What's New" in notes
    assert "### Consistent XVF3800 speaker volume" in notes
    assert "### Edit your order before checkout" in notes
    assert "## Verification" in notes
    assert "AppImage runtime and desktop fixes" not in notes


def test_release_notes_extractor_matches_the_entire_version(tmp_path):
    import subprocess

    workflow = _read(OVERLAY / ".github/workflows/release.yml")
    extractor = re.search(r"awk -v v=\"\$BASE_VERSION\" '([^']+)' CHANGELOG\.md > notes\.md", workflow)
    assert extractor
    (tmp_path / "CHANGELOG.md").write_text(
        "## 1.0.20 — newer\n\nWRONG_NEWER\n\n"
        "## 1.0.2 — requested\n\n## What's New\n\nRIGHT\n\n"
        "## 1.0.1 — older\n\nWRONG_OLDER\n",
        encoding="utf-8",
    )
    notes = subprocess.run(
        ["awk", "-v", "v=1.0.2", extractor.group(1), "CHANGELOG.md"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert notes == "\n## What's New\n\nRIGHT\n\n"


def test_release_includes_standalone_pi_installer():
    workflow = _read(OVERLAY / ".github/workflows/release.yml")
    install = _read(OVERLAY / "INSTALL.md")
    assert "install -m755 scripts/install_appimage.sh dist/driveos-installer.sh" in workflow
    assert "install -m644 packaging/icon/drive-os-256.png dist/drive-os.png" in workflow
    assert "driveos-installer.sh" in install
    assert "~/.local/share/applications/drive-os.desktop" in install
    assert "~/Desktop/DriveOS.desktop" in install
    assert "--launch" in install


def test_mapbox_navigation_responses_are_not_cached():
    router = _read(ROOT / "src/driveos/nav/router.py")
    config = _read(ROOT / "src/driveos/config.py")
    assert "origin_cache_m" not in router
    assert "origin_cache_ttl_s" not in router
    assert "origin_cache_m" not in config
    assert "origin_cache_ttl_s" not in config


def test_mapbox_attribution_includes_improve_this_map():
    map_view = _read(ROOT / "hmi_lvgl/src/map/map_view.c")
    assert "Improve this map" in map_view


def test_appimage_uses_native_window_controls_and_clean_close():
    run = _read(ROOT / "packaging/appimage/AppRun")
    main = _read(ROOT / "hmi_lvgl/src/main.c")
    assert '"$HMI" --url "ws://127.0.0.1:$PORT/v1/ui" --fullscreen' not in run
    assert 'exec "$HMI" --url "ws://127.0.0.1:$PORT/v1/ui" ;;' in run
    assert "SDL_AddEventWatch" in main and "SDL_DelEventWatch" in main
    assert "SDL_WINDOWEVENT_CLOSE" in main and "SDL_QUIT" in main
    assert "app_request_quit()" in main
    assert '/proc/$SERVER_PID/task/$SERVER_PID/children' in run


def test_readme_screenshots_are_trackable_release_assets():
    names = ("home", "nav-route", "places", "menu", "item-options", "cart")
    for name in names:
        assert (OVERLAY / "assets/screenshots" / f"{name}.png").is_file()
    assert "\n/screenshots/\n" in _read(OVERLAY / ".gitignore")


def test_hmi_sdl_hint_supports_ubuntu_22_04():
    display = _read(ROOT / "hmi_lvgl/src/display.c")
    assert 'SDL_SetHint("SDL_VIDEODRIVER", "wayland,x11")' in display
    assert "SDL_HINT_VIDEODRIVER" not in display


def test_image_generation_tools_and_references_stay_out_of_release():
    export = ROOT / ".release/export.sh"
    names = ("gen_food_images.py", "food_jobs_v2.py", "cover_jobs_v3.py", "ingest_food_images.py")
    if export.is_file():
        for name in names:
            assert f"- /tools/{name}" in _read(export)
    else:
        for name in names:
            assert not (ROOT / "tools" / name).exists()
    assert "`tools/" not in _read(ROOT / "hmi_lvgl/assets/food/LICENSE.md")
