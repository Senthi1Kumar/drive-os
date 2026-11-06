import hashlib
import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install_appimage.sh"


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _release(tmp_path: Path, home: Path, version: str) -> Path:
    downloads = home / "Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    app = downloads / f"DriveOS-{version}-aarch64.AppImage"
    _executable(app, "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$HOME/launch.log\"\n")
    release = tmp_path / "release" / f"v{version}"
    release.mkdir(parents=True)
    digest = hashlib.sha256(app.read_bytes()).hexdigest()
    (release / "SHA256SUMS").write_text(f"{digest}  {app.name}\n")
    (release / "SHA256SUMS.minisig").write_text("checksum signature\n")
    (release / f"{app.name}.minisig").write_text("application signature\n")
    (release / "driveos-minisign.pub").write_text("public key\n")
    return app


def _run(tmp_path: Path, home: Path, *args: str, minisign_fail: bool = False) -> subprocess.CompletedProcess[str]:
    release = tmp_path / "release"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    _executable(fake_bin / "curl", """#!/bin/bash
set -euo pipefail
out=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
[[ "$url" =~ /download/([^/]+)/([^/]+)$ ]]
cp "$FAKE_RELEASE_ROOT/${BASH_REMATCH[1]}/${BASH_REMATCH[2]}" "$out"
""")
    _executable(fake_bin / "minisign", """#!/bin/bash
set -euo pipefail
printf '%s\n' "$*" >> "$FAKE_LOG"
[ "${FAKE_MINISIGN_FAIL:-0}" != 1 ]
""")
    env = os.environ.copy()
    env.update(
        HOME=str(home),
        PATH=f"{fake_bin}:{env['PATH']}",
        FAKE_RELEASE_ROOT=str(release),
        FAKE_LOG=str(tmp_path / "tools.log"),
        FAKE_MINISIGN_FAIL="1" if minisign_fail else "0",
    )
    return subprocess.run(
        [str(INSTALLER), *args], text=True, capture_output=True, env=env, cwd=ROOT, check=False
    )


def _assert_launchers(home: Path, app: Path) -> None:
    for relative in (".local/share/applications/drive-os.desktop", "Desktop/DriveOS.desktop"):
        desktop = home / relative
        text = desktop.read_text()
        assert f'Exec="{app}"' in text
        assert "Name=DriveOS" in text
        assert "Icon=drive-os" in text
        assert os.access(desktop, os.X_OK)
    icon = home / ".local/share/icons/hicolor/256x256/apps/drive-os.png"
    assert icon.read_bytes() == (ROOT / "packaging/icon/drive-os-256.png").read_bytes()


def test_default_selects_newest_version_and_installs_launchers(tmp_path: Path):
    home = tmp_path / "home with spaces"
    _release(tmp_path, home, "1.0.9")
    newest = _release(tmp_path, home, "1.0.10")
    result = _run(tmp_path, home)
    assert result.returncode == 0, result.stderr
    installed = home / "Applications" / newest.name
    assert installed.is_file() and os.access(installed, os.X_OK)
    assert not newest.exists()
    assert (home / "Downloads/DriveOS-1.0.9-aarch64.AppImage").is_file()
    _assert_launchers(home, installed)
    tool_log = (tmp_path / "tools.log").read_text()
    assert "SHA256SUMS" in tool_log
    assert " -x " in tool_log and f"{newest.name}.minisig" in tool_log


def test_explicit_path_is_idempotent_and_launch_is_opt_in(tmp_path: Path):
    home = tmp_path / "home"
    app = _release(tmp_path, home, "1.0.1")
    result = _run(tmp_path, home, str(app))
    assert result.returncode == 0, result.stderr
    installed = home / "Applications" / app.name
    assert not (home / "launch.log").exists()
    result = _run(tmp_path, home, "--launch", str(installed))
    assert result.returncode == 0, result.stderr
    assert (home / "launch.log").is_file()
    _assert_launchers(home, installed)


def test_bad_checksum_preserves_existing_installation(tmp_path: Path):
    home = tmp_path / "home"
    app = _release(tmp_path, home, "1.0.1")
    installed = home / "Applications" / app.name
    installed.parent.mkdir(parents=True)
    installed.write_text("existing installation")
    app.write_text("tampered")
    result = _run(tmp_path, home, str(app))
    assert result.returncode != 0
    assert installed.read_text() == "existing installation"
    assert not (home / ".local/share/applications/drive-os.desktop").exists()


def test_signature_failure_preserves_existing_installation(tmp_path: Path):
    home = tmp_path / "home"
    app = _release(tmp_path, home, "1.0.1")
    installed = home / "Applications" / app.name
    installed.parent.mkdir(parents=True)
    installed.write_text("existing installation")
    result = _run(tmp_path, home, str(app), minisign_fail=True)
    assert result.returncode != 0
    assert installed.read_text() == "existing installation"
    assert not (home / "Desktop/DriveOS.desktop").exists()
