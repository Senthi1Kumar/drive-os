# Checkout sheet: Face ID camera preview and code entry by voice (contract v1, additive)

The checkout sheet (`screens/sheets.c`) shows a live camera image while Face ID runs, and tells the driver they can
say the one-time code. Both ride on `/v1/ui` with no new commands. Older clients ignore the new fields.

## State: `state.shop.checkout.auth` (added fields)
```json
{"stage": "face", "challenge": null, "detail": "", "sim": false,
 "preview": true,
 "face_state": "scanning",
 "face_rect": {"x": 0.31, "y": 0.22, "w": 0.38, "h": 0.46},
 "face_name": null,
 "voice_entry": false}
```
| field | type | meaning |
|---|---|---|
| `preview` | bool | the camera is delivering frames right now (binary messages, below). False when there is no camera, no preview support in the SDK, in simulated mode, or after the scan ended |
| `face_state` | `none` / `scanning` / `verified` / `failed` | `none` unless the Face ID tile was chosen. `scanning` from tile tap until the camera answers; `verified` and `failed` stay until the driver retries, picks another method or cancels. `verified` is held on screen about 1.2 s before the order goes through |
| `face_rect` | object or null | the detected face, normalised 0..1 inside the square preview (`x`, `y` = top-left). Coarse (steps of 0.02) and only as fresh as the last state push; the per-frame rectangle in the frame header is authoritative while scanning. Null when no face is detected |
| `face_name` | string or null | the enrolled driver's display name, only with `verified`. It is the name given at face enrollment (`FaceUserMap` name), else the Voice ID enrollment name, else the stem of a DriveOS-generated camera id (`alex_1af812` becomes `alex`). Never the raw camera id |
| `voice_entry` | bool | a one-time code is pending and can be spoken: show "Say the code or type it" (the typed keypad keeps working) |

The face fields are always present (`preview` false, `face_state` `none`, `face_rect` and `face_name` null when no
face scan applies, including when `checkout.pending` is false).

### What the sheet draws
* Below the three method tiles, a square panel with the image while `stage` is `face` and (`preview` or a frame
  has arrived or `face_state` is `verified`/`failed`); also while `stage` is `rejected` with `face_state` `failed`.
  Otherwise the existing face icon.
* Thin, semi-transparent corner brackets over the face: white `scanning`, green `verified`, red `failed`. They follow
  the frame-header rectangle (smoothed), else `face_rect`, else a centred frame.
* A line under the image: "Look at the camera" (scanning), "Face verified · <face_name>" (green), "Face didn't match" (red).

## Binary frames
Frames are WebSocket **binary** messages on the same connection (JSON stays text). RAM only, never written to disk,
never left the device. The camera is the Intel RealSense ID F455 over USB video, so the preview runs during the
serial `authenticate`. The back-end sends a centred square crop, area-averaged to `biometrics.preview_size` (240),
at `biometrics.preview_fps` (12) at most, mirrored like a selfie view (`biometrics.preview_mirror`). A client that
cannot keep up drops frames (the server never queues them). A client that connects mid-scan or after a result gets
the latest frame right after its first state.

One message = a 24-byte little-endian header, then `width * height` RGB565 pixels (little-endian `u16`,
`R5 G6 B5`, rows top to bottom):

| offset | size | field |
|---|---|---|
| 0 | 4 | magic `DVPV` |
| 4 | 1 | version (1; ignore frames with another version) |
| 5 | 1 | flags: bit0 = face rectangle valid |
| 6 | 2 | reserved (0) |
| 8 | 4 | `seq` (frame counter) |
| 12 | 2 | `width` |
| 14 | 2 | `height` |
| 16 | 8 | face rectangle `x`, `y`, `w`, `h` in preview pixels (4 x `u16`; zero when bit0 is clear) |

Parsing lives in `src/preview_proto.c` (unit tested by `tests/test_preview_proto.c`); `src/preview.c` keeps the
latest frame as an `lv_image_dsc_t`. Frames larger than 320 x 320, truncated frames and unknown versions are ignored.

## Name
The F455 camera user id (`alex_1af812`) is not shown. `driveos enroll --face --name Asha` and the `enroll_face_start`
command store the name beside the id (`f455_users.json`, key `_names`).

## Code by voice
While `stage` is `otp` the driver can say the digits ("four seven two nine one zero", "472910", "it's 4 7 2 9 1 0",
"oh" for 0, "double five"). The rules NLU parses them before any model sees the utterance and submits them to
`OtpService` (3 attempts). A wrong code is answered with the attempts left; a count of digits other than the code
length is asked again without using an attempt. "approve" / "yes" only prompt for the code (they never restart the
face scan and never approve), "cancel" cancels. The spoken code is masked in `voice.heard` and the transcript and is
never logged. The transcription prompt is biased to digits while a code is pending.

## Unavailable cases
No camera, no `rsid_py` Preview (SDK built without `-DRSID_PREVIEW=ON`), simulated biometrics, a busy UVC node or a
failed preview start: `preview` stays false and the sheet keeps the existing face icon. The scan itself is unaffected.
