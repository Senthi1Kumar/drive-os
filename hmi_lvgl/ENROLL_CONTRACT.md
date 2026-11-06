# Voice ID enrollment over /v1/ui (PROPOSED, contract v1 additive)

The HMI Enroll screen (Settings > Voice ID) is built against this; the back-end does not serve it yet
(`biometrics/enroll.py` has `EnrollmentSession` but `server/commands.py` has no enroll commands).

## Commands (client -> server)
| name | args | effect |
|---|---|---|
| `enroll_start` | `{name, consent: true}` | opens an `EnrollmentSession` (consent is required; the UI shows the consent card first) |
| `enroll_take` | `{}` | records one prompted utterance, runs the quality gate, appends to `state.enroll.quality` |
| `enroll_cancel` | `{}` | discards the session; `state.enroll` becomes `null` |

## State
`state.enroll` is `null` when no session is open, else:
```json
{"stage": "ready|recording|analyzing|done|failed", "take": 2, "total": 4, "kept": 0,
 "prompt": "Hey Nova, this is Sam and I am enrolling my voice for payments.",
 "quality": [{"duration_s": 3.4, "snr_db": 24.1, "clipping_pct": 0.0, "outlier": false, "ok": true}],
 "error": ""}
```
- `ready`: waiting for `enroll_take`; `recording`: mic open (UI shows `voice.level`); `analyzing`: quality gate / embedding.
- `take` is the 1-based take about to be (or being) recorded; `quality` has one entry per finished take (`ok=false` -> re-record).
- `done`: `kept` of `total` takes survived outlier drop; `failed`: `error` explains (not enough speech, no consent, ...).
- Acks: `enroll_take` with the mic busy -> `ok:false, error:"voice unavailable"`.
