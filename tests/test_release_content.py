"""Release-facing content and packaging invariants for the first public release."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / ".release" / "overlay"
if not OVERLAY.is_dir():
    OVERLAY = ROOT


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_first_public_release_is_1_0_0_everywhere():
    assert 'version = "1.0.0"' in _read(ROOT / "pyproject.toml")
    assert '__version__ = "1.0.0"' in _read(ROOT / "src/driveos/__init__.py")
    export = ROOT / ".release/export.sh"
    if export.is_file():
        assert "RELEASE_VERSION:-1.0.0" in _read(export)
    assert "version-1.0.0-" in _read(OVERLAY / "README.md")
    assert "## 1.0.0 — initial release" in _read(OVERLAY / "CHANGELOG.md")
    container = _read(ROOT / "src/driveos/container.py")
    assert "from . import __version__" in container
    assert 'f"DriveOS/{__version__} (in-car demo)"' in container


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
