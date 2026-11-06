# Map + guidance fields on /v1/ui (contract v1, additive)

All fields live under `state.nav` next to the existing `active`, `dest` (string), `maneuver`, `eta_min`, `mode`.
They are present while a route exists (`mode` = overview | navigating | arrived) and absent otherwise.
The Mapbox token never travels over WS; the HMI reads it from its own environment.

| field | type | meaning |
|---|---|---|
| `route.polyline6` | string | whole route, Mapbox polyline6, (lat, lon) order; sent on every snapshot, the HMI hashes it. The back-end re-plans from the car only on a new route, an ETA refresh or a reroute; between those the HMI draws only the part ahead of the car (it projects the puck onto the line and cuts the line there, at most every 300 ms and 4 m) |
| `route.id` | int | identity of the trip's route; stable across ETA refreshes, reroutes and added stops, new for each destination. The HMI frames the route whole (overview) only when it changes, and applies a changed id at once even when the line is identical to the previous one (the same destination planned again from the same spot); absent = the HMI falls back to the end points |
| `route.reason` | string | why this line was issued: `new` (new destination), `refresh` (ETA refresh) or `reroute` (off-route or stop added). `refresh`/`reroute` replace the line in place: follow mode and pitch stay as they are |
| `congestion` | string | one letter per route segment (`u l m h s`); length = decoded points - 1; offline routes are all `u` |
| `step.icon` | string | upcoming maneuver: `depart arrive straight turn_left turn_right slight_left slight_right sharp_left sharp_right uturn merge fork_left fork_right ramp_left ramp_right roundabout` |
| `step.text` | string | banner text for that maneuver (street / destination), else the instruction |
| `step.dist_m` | int | metres from the vehicle to that maneuver, along the route (navigating only) |
| `step.lanes` | `[{indications, valid, active, active_indication?}]` | optional lane guidance for that maneuver, left to right, from Directions `bannerInstructions[].sub.components` of type `lane` (the banner in force at the current distance; absent when the road has no lane data). `indications`: the lane's arrows, any of `left slight left sharp left straight slight right right sharp right uturn` (`none` is dropped); `valid`: Directions' `active`, the lane is recommended for the maneuver; `active`: the route takes one of its arrows; `active_indication`: that arrow (`active_direction`; only on valid lanes). The HMI draws the row under the maneuver banner: valid lanes lit with the taken arrow brightest, the others dim |
| `next_step` | `{icon, text}` | the maneuver after that one (optional) |
| `puck` | `{lat, lon, heading}` | vehicle position for the map puck (same values as `state.vehicle`) |
| `state.vehicle.t_ms` | number | monotonic stamp (ms) of the vehicle fix behind `state.vehicle` / `puck`; the HMI places fixes on its own clock by it and interpolates between them. A snapshot that repeats the last fix repeats the stamp. Not part of the change test: a parked car restamped every tick sends nothing |
| `dest_point` | `{lat, lon, name}` | destination marker (`dest` stays the name string for older clients) |
| `stops` | `[{lat, lon, name}]` | stops added along the route, in route order (optional) |
| `reroute` | bool | true for ~8 s after a reroute |
| `reroutes` | int | reroutes done in this trip |
| `eta_s`, `remaining_m` | int | time and distance left (navigating / arrived) |

Back-end knobs (`DRIVEOS_NAV__*`): `off_route_m` (40), `off_route_fixes` (3), `reroute_cooldown_s` (20),
`eta_refresh_s` (90, 0 = off), `sim_speed_kmh` (35), `sim_detour_at_m` (unset; the simulated car leaves the route once at that distance), `search_deviation_min` (6). Mapbox Directions responses are requested for each plan and are not cached.
Voice cues (`RouteStep.voice`, distance before the step's end) are spoken by the back-end (`CueTracker`) and are not sent over WS.

Map look (HMI env): `DOS_MAP_3D=0|1` extruded buildings (Mapbox Streets v8 `building`, from `DOS_MAP_3D_MINZOOM`, default 15, drawn below the labels and the route); `DOS_MAP_LABELS=0` leaves the style's own label density; `DOS_BOOST_MS` display refresh and animation period in ms while the map moves (default 33, the map's own frame period; 16 restores 60 Hz). The next turn is marked on the map from `step.dist_m` and the route line: the route around it lit and an arrowhead on the road pointing out of the turn.

HMI debugging env (stdout/stderr of `driveos_hmi`): `DOS_CAM_LOG=1` prints every camera decision (recenter, overview fit, route id received and whether it was new, follow lost); `DOS_MAP_STATS=1` adds snapshot rate, parse time and CPU to the `ui:` line; `DOS_SHOT_DIR=<dir>` with `DOS_SHOT_EVERY_MS=<n>` writes a PNG of the screen every n ms (headless runs).
