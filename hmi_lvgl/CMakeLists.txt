cmake_minimum_required(VERSION 3.21)
project(driveos_hmi C)
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

find_package(PkgConfig REQUIRED)
find_package(Threads REQUIRED)
# SDL2 is the window backend: on the Pi's labwc session it speaks native Wayland (SDL_VIDEODRIVER=wayland), on a laptop
# Wayland or X11. libdrm, when present, adds LVGL's own DRM/KMS + evdev backend for a bare console (--backend drm).
pkg_check_modules(SDL2 REQUIRED IMPORTED_TARGET sdl2)
pkg_check_modules(DRM IMPORTED_TARGET libdrm)
if(DRM_FOUND)
  set(DOS_WITH_DRM 1)
else()
  set(DOS_WITH_DRM 0)
endif()

# LVGL: pinned release, trimmed software-render build.
# FETCHCONTENT_SOURCE_DIR_LVGL (-D at configure time) reuses an unpacked tree when the machine is offline.
set(LV_BUILD_CONF_PATH ${CMAKE_CURRENT_SOURCE_DIR}/lv_conf.h CACHE PATH "" FORCE)
set(CONFIG_LV_BUILD_DEMOS OFF CACHE BOOL "" FORCE)
set(CONFIG_LV_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(CONFIG_LV_USE_THORVG OFF CACHE BOOL "" FORCE)
set(CONFIG_LV_USE_THORVG_INTERNAL OFF CACHE BOOL "" FORCE)
set(LV_FETCH_DEPENDENCIES OFF CACHE BOOL "" FORCE)
set(LV_BUILD_INSTALL OFF CACHE BOOL "" FORCE)
include(FetchContent)
FetchContent_Declare(lvgl
  URL https://github.com/lvgl/lvgl/archive/refs/tags/v9.6.0.tar.gz
  URL_HASH SHA256=b20ee3acc1bba13c62d854f9ebd62e4c51e0b443b1e0225892e86442defa84df
  DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
FetchContent_MakeAvailable(lvgl)
target_compile_definitions(lvgl PUBLIC DOS_WITH_DRM=${DOS_WITH_DRM})
target_link_libraries(lvgl PUBLIC PkgConfig::SDL2 m)
# JPEG thumbnails (libjpeg / libjpeg-turbo): decoded fully, so LVGL can scale them to the card size
pkg_check_modules(JPEG REQUIRED IMPORTED_TARGET libjpeg)
target_link_libraries(lvgl PUBLIC PkgConfig::JPEG)
if(DRM_FOUND)
  target_link_libraries(lvgl PUBLIC PkgConfig::DRM)
endif()

# Version: pyproject.toml of the DriveOS package; build id: the git short sha.
set(DRIVEOS_VERSION "0.0.0" CACHE STRING "DriveOS version shown on the About screen")
if(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/../pyproject.toml)
  file(STRINGS ${CMAKE_CURRENT_SOURCE_DIR}/../pyproject.toml _ver REGEX "^version *= *\"")
  if(_ver)
    string(REGEX REPLACE "^version *= *\"([^\"]+)\".*" "\\1" DRIVEOS_VERSION "${_ver}")
  endif()
endif()
execute_process(COMMAND git rev-parse --short HEAD WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
  OUTPUT_VARIABLE DRIVEOS_BUILD OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
if(NOT DRIVEOS_BUILD)
  set(DRIVEOS_BUILD "dev")
endif()

# Map: pure helpers (URL rewriting, gestures, follow math, route GeoJSON, polyline decoding) always build. The live map
# needs the prebuilt MapLibre Native C package under .deps/maplibre/<preset>/ (DOS_MAPLIBRE_PREFIX overrides the
# location); without it the Nav tab keeps the schematic radar view. Its headers are C23, so only dos_map compiles
# against them.
if(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64")
  set(DOS_MLN_PRESET linux-gnu-arm64-egl)
else()
  set(DOS_MLN_PRESET linux-gnu-x64-egl)
endif()
set(DOS_MAPLIBRE_PREFIX "${CMAKE_CURRENT_SOURCE_DIR}/.deps/maplibre/${DOS_MLN_PRESET}/maplibre-native-c-${DOS_MLN_PRESET}"
    CACHE PATH "MapLibre Native C package root (include/, lib/)")
add_library(dos_mapcore STATIC src/map/mapbox_url.c src/map/map_gesture.c src/map/map_puck.c src/map/map_follow.c
  src/map/route_geojson.c src/map/polyline.c src/map/route_identity.c src/map/route_progress.c
  src/map/lane_layout.c src/map/maneuver_arrow.c src/map/map_style.c)
target_include_directories(dos_mapcore PUBLIC src)
target_compile_options(dos_mapcore PRIVATE -Wall -Wextra)
target_link_libraries(dos_mapcore PUBLIC m)
set(DOS_HAVE_MAP OFF)
if(EXISTS "${DOS_MAPLIBRE_PREFIX}/include/maplibre_native_c.h")
  set(DOS_HAVE_MAP ON)
  if(NOT TARGET PkgConfig::EGL)
    pkg_check_modules(EGL REQUIRED IMPORTED_TARGET egl)
  endif()
  add_library(dos_map STATIC src/map/map_engine.c)
  set_target_properties(dos_map PROPERTIES C_STANDARD 23 C_EXTENSIONS ON)
  target_include_directories(dos_map PUBLIC src PRIVATE ${DOS_MAPLIBRE_PREFIX}/include)
  target_link_libraries(dos_map PUBLIC dos_mapcore
    ${DOS_MAPLIBRE_PREFIX}/lib/libmaplibre-native-c.so ${DOS_MAPLIBRE_PREFIX}/lib/libmln_ffi_platform.a
    dl PkgConfig::EGL Threads::Threads m)
  target_compile_options(dos_map PRIVATE -Wall -Wextra)
  message(STATUS "Map: MapLibre Native at ${DOS_MAPLIBRE_PREFIX}")
else()
  message(STATUS "Map: MapLibre Native package not found at ${DOS_MAPLIBRE_PREFIX}; Nav keeps the schematic view")
endif()

file(GLOB DOS_FONTS ${CMAKE_CURRENT_SOURCE_DIR}/fonts/dos_*.c)
add_executable(driveos_hmi
  src/main.c src/app.c src/display.c src/theme.c src/png_out.c src/ui_state.c src/ws_client.c src/ws.c src/json.c
  src/preview.c src/preview_proto.c
  src/icons_gen.c src/logo_gen.c src/orb.c src/thumbs.c src/thumb_path.c
  src/screens/home.c src/screens/agent.c src/screens/about.c src/screens/placeholder.c
  src/screens/shop.c src/screens/order.c src/screens/nav.c src/screens/sheets.c src/screens/enroll.c
  ${DOS_FONTS})
target_include_directories(driveos_hmi PRIVATE src fonts)
target_link_libraries(driveos_hmi PRIVATE lvgl Threads::Threads m)
target_compile_options(driveos_hmi PRIVATE -Wall -Wextra)
target_link_libraries(driveos_hmi PRIVATE dos_mapcore)
if(DOS_HAVE_MAP)
  target_sources(driveos_hmi PRIVATE src/map/map_view.c src/map/map_worker.c src/map/mapbox_logo.c)
  target_link_libraries(driveos_hmi PRIVATE dos_map)
  target_compile_definitions(driveos_hmi PRIVATE DOS_HAVE_MAP=1)
  # the shared object sits next to its package, so the binary finds it without LD_LIBRARY_PATH
  set_target_properties(driveos_hmi PROPERTIES BUILD_RPATH "${DOS_MAPLIBRE_PREFIX}/lib" INSTALL_RPATH "${DOS_MAPLIBRE_PREFIX}/lib")
else()
  target_compile_definitions(driveos_hmi PRIVATE DOS_HAVE_MAP=0)
endif()
target_compile_definitions(driveos_hmi PRIVATE DRIVEOS_VERSION="${DRIVEOS_VERSION}" DRIVEOS_BUILD="${DRIVEOS_BUILD}")

enable_testing()
foreach(t map_follow map_gesture map_puck mapbox_url route_geojson polyline route_identity route_progress lane_layout maneuver_arrow map_style)
  add_executable(test_${t} tests/test_${t}.c)
  target_include_directories(test_${t} PRIVATE tests)
  target_link_libraries(test_${t} PRIVATE dos_mapcore)
  target_compile_options(test_${t} PRIVATE -Wall -Wextra)
  add_test(NAME ${t} COMMAND test_${t})
endforeach()
target_sources(test_route_geojson PRIVATE src/json.c)
target_sources(test_map_style PRIVATE src/json.c)
target_include_directories(test_map_style PRIVATE src)
add_executable(test_preview_proto tests/test_preview_proto.c src/preview_proto.c)
target_include_directories(test_preview_proto PRIVATE src tests)
target_compile_options(test_preview_proto PRIVATE -Wall -Wextra)
add_test(NAME preview_proto COMMAND test_preview_proto)
add_executable(test_thumb_path tests/test_thumb_path.c src/thumb_path.c)
target_include_directories(test_thumb_path PRIVATE src tests)
target_compile_options(test_thumb_path PRIVATE -Wall -Wextra)
add_test(NAME thumb_path COMMAND test_thumb_path)
