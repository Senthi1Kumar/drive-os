#!/usr/bin/env bash
# Wrapper: build the DriveOS AppImage (see packaging/README.md). Pass --in-container on an x86 host with arm64 docker.
exec "$(cd "$(dirname "$0")/.." && pwd)/packaging/appimage/build.sh" "$@"
