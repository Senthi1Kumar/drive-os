# Deploying to a head unit

Target: a Linux head unit (Yocto, Debian or Ubuntu Core) with a centre display, a microphone array, a
steering-wheel voice button mapped to a key, GNSS through gpsd and — for Face ID — a cabin camera with a
face-verification service. Qt 6 renders through EGL on Wayland; run DriveOS under a kiosk compositor such
as `cage`. No containers are required.

```bash
sudo useradd --system --create-home driveos
sudo python3.12 -m venv /opt/driveos/.venv
sudo /opt/driveos/.venv/bin/pip install "/opt/driveos[voice]"            # or the wheel built in CI
sudo ln -s /opt/driveos/.venv/bin/driveos /usr/local/bin/driveos
sudo install -d -m 0750 -o driveos /etc/driveos /var/lib/driveos
sudo install -m 0640 -o driveos config.example.toml /etc/driveos/config.toml   # then edit
sudo install -m 0600 -o driveos .env.example /etc/driveos/secrets.env         # then fill in
sudo cp deploy/driveos.service /etc/systemd/system/
sudo systemctl enable --now driveos
driveos doctor
```

Notes

* **Models**: the first start downloads Whisper `base.en` (recognition) and `tiny.en` (wake phrase).
  Pre-seed `~/.cache/huggingface` for the `driveos` user in the image for cars without connectivity at boot.
* **Wake phrase**: `voice.wake_phrase` (default "hey nova"). The wheel button (mapped to F12 or Space in the
  compositor) always opens a turn and barges in on speech.
* **Face ID**: set `auth.face = "command"` and `auth.face_command` to the cabin camera's verifier. DriveOS
  trusts only its exit status (0 = enrolled owner); no images or templates pass through DriveOS.
* **One-time codes**: `auth.otp_channel = "sms"` with Twilio credentials; codes go to the occupant's `phone`.
* **Device key**: `/var/lib/driveos/device_key.pem` signs every ledger entry. On production hardware
  implement `KeyStore` against the TPM / secure element so the private key never leaves it.
* **Ledger check**: the service runs `driveos ledger verify` before start; a broken chain stops it.
* **Position**: set `DRIVEOS_VEHICLE__SOURCE=gps` and `DRIVEOS_VEHICLE__GPS_SOURCE=gpsd` (or
  `serial:/dev/ttyACM0` to read the receiver directly). `driveos gps --source gpsd` prints fixes to check it.
* **Gear signal**: a GPS gives position and speed only. Feed real gear state from the vehicle bus with
  `services.vehicle.gps.set_bus_gear("P"|"D"|"R"|"N")` (a CAN bridge process); until then gear is inferred
  from speed with hysteresis (Drive after ~2 s above walking pace, Park after ~6 s stationary).
* **Shared merchant gateway**: `deploy/driveos-mcp.service` runs the merchant MCP server over HTTP for a
  fleet; point `[[merchants.mcp_servers]] url` at it behind TLS and auth.
