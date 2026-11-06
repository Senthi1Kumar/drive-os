"""Configuration: defaults < TOML file < environment (DRIVEOS_ prefix, __ for nesting).

    DRIVEOS_CONFIG=/etc/driveos/config.toml
    DRIVEOS_MAPBOX__TOKEN=pk.xxx
    DRIVEOS_PAYMENTS__PROVIDER=stripe
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from .domain.models import GeoPoint, Occupant, Payer, SavedPlace


def _default_data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "driveos"


def _transcripts_from_env() -> bool:
    return os.environ.get("DRIVEOS_LOG_TRANSCRIPTS", "").strip().lower() in ("1", "true", "yes", "on")


class LoggingConfig(BaseModel):
    transcripts: bool = Field(default_factory=_transcripts_from_env)   # env DRIVEOS_LOG_TRANSCRIPTS=1: utterances and replies in logs


class DisplayConfig(BaseModel):
    width: int = 1280
    height: int = 800
    fullscreen: bool = False
    full_menu_while_moving: bool = False  # NHTSA-aligned default: glance view while moving
    units: Literal["imperial", "metric"] = "imperial"


class MapboxConfig(BaseModel):
    token: SecretStr | None = None
    style: str = "mapbox://styles/mapbox/standard"
    light_preset: Literal["dawn", "day", "dusk", "night"] = "night"
    theme: Literal["default", "faded", "monochrome"] = "monochrome"
    profile: str = "driving-traffic"
    language: str = "en"

    @field_validator("token", mode="before")
    @classmethod
    def _blank_token_is_none(cls, v):
        # an empty `DRIVEOS_MAPBOX__TOKEN=` line in .env means "no token", not a token that fails every request
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return v


class NavConfig(BaseModel):
    off_route_m: float = 40.0         # distance from the route polyline that counts as off-route
    off_route_fixes: int = 3          # consecutive fixes beyond it before rerouting
    reroute_cooldown_s: float = 20.0  # minimum gap between reroutes
    eta_refresh_s: float = 90.0       # re-request the route while navigating (0 = off)
    sim_speed_kmh: float = 35.0       # cruising speed of the simulated vehicle
    sim_detour_at_m: float | None = None  # demo/test: leave the route this far along it (once), to show a reroute
    search_deviation_min: int = 6     # search along the route: maximum detour in minutes


class GooglePlacesConfig(BaseModel):
    api_key: SecretStr | None = None


class SquareConfig(BaseModel):
    access_token: SecretStr | None = None
    environment: Literal["sandbox", "production"] = "sandbox"
    location_ids: list[str] = Field(default_factory=list)
    api_version: str = "2026-09-16"
    # Square merchants are paid through Square Payments (an order is only shown to staff once paid)
    card_source_id: str = "cnon:card-nonce-ok"  # sandbox test nonce; production: ccof:... card-on-file id
    customer_id: str | None = None


class StripeConfig(BaseModel):
    secret_key: SecretStr | None = None
    customer_id: str | None = None
    payment_method_id: str | None = None
    api_version: str = "2026-09-30.endive"


class McpServerConfig(BaseModel):
    name: str
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None  # streamable HTTP endpoint instead of stdio
    headers: dict[str, str] = Field(default_factory=dict)


class MerchantsConfig(BaseModel):
    # where merchants come from, in priority order (results are de-duplicated)
    sources: list[Literal["local", "mcp", "square", "mapbox"]] = Field(default_factory=lambda: ["local", "mapbox"])
    mcp_servers: list[McpServerConfig] = Field(
        default_factory=lambda: [McpServerConfig(name="driveos-merchants", command=sys.executable,
                                                 args=["-m", "driveos.mcp_server"])]
    )
    # ordering from the bundled catalogue: "simulated" kitchen / pump / store, or "off" (browse + navigate only).
    # "auto" = simulated in demo mode, off in live mode.
    local_ordering: Literal["auto", "simulated", "off"] = "auto"
    include_dataset: bool = True  # the 20-city restaurant crawl (319 places) in addition to the Los Gatos set
    search_radius_m: int = 12_000
    max_results: int = 6
    pickup_overhead_s: int = 120
    # real map places (Mapbox) get a clearly labelled sample menu so they can be ordered from in the demo
    sample_menus: bool = True
    mapbox_cache_ttl_s: float = 600.0


class LocationConfig(BaseModel):
    """Which position the app uses: a live GPS fix when there is one, else a simulated place."""
    auto: bool = True                 # switch between live GPS and the simulated place by itself
    fallback: GeoPoint = GeoPoint(lat=37.4419, lon=-122.1430)   # Palo Alto, CA
    fallback_label: str = "Palo Alto, California"
    acquire_s: float = 5.0            # a fix must hold this long before we go live
    lose_s: float = 20.0              # no fix for this long before we fall back to the simulated place
    min_dwell_s: float = 30.0         # never switch twice within this window (no flapping)
    refresh_m: float = 1500.0         # re-query nearby places after moving this far
    max_accuracy_m: float = 150.0     # a coarse fix (IP location, a far-away satellite-less estimate) is not "live"


class ImagesConfig(BaseModel):
    enabled: bool = True
    max_cache_mb: int = 64            # cap on downloaded photos under <data_dir>/cache/images
    generated_dir: Path | None = None  # default: hmi_lvgl/assets/food next to the package


class PaymentsConfig(BaseModel):
    provider: Literal["simulated", "stripe", "square"] = "simulated"
    capture_on: Literal["accepted", "fulfilled"] = "accepted"
    cancel_window_s: int = 120


class PolicyConfig(BaseModel):
    voice_only_max_minor: int = 2500  # T1 (voice approval) cap per purchase ($25.00)
    voice_only_daily_minor: int = 5000
    voice_requires_known_merchant: bool = False
    per_transaction_max_minor: int = 15000
    daily_max_minor: int = 30000
    quote_ttl_s: int = 300


class AuthConfig(BaseModel):
    methods: list[Literal["voice", "face", "otp"]] = Field(default_factory=lambda: ["voice", "face", "otp"])
    # Face ID: "simulated" scan, "command" = run the cabin camera's verifier, "none"
    # "driveauth" = voice-first biometric verification (see BiometricsConfig); "auto" = driveauth once a voice
    # template is enrolled and the models are present, otherwise the simulated scan
    face: Literal["auto", "simulated", "command", "none", "driveauth"] = "auto"
    face_command: list[str] = Field(default_factory=lambda: ["driveos-face-verify"])
    face_scan_s: float = 1.6
    # One-time code: "phone" = paired-phone notification (shown on screen in demo), "sms" = Twilio
    otp_channel: Literal["phone", "sms"] = "phone"
    twilio_account_sid: str | None = None
    twilio_auth_token: SecretStr | None = None
    twilio_from: str | None = None
    timeout_s: float = 45.0


def _drive_os_home() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "drive-os"


class BiometricsConfig(BaseModel):
    """Voice-first payment verification. Every threshold here is demo-calibrated, not certified."""
    home: Path = Field(default_factory=_drive_os_home)
    driver_id: str = "driver1"
    # redimnet2: more accurate but a fixed 6 s window (about 2 s on a Pi 5); wespeaker: variable length, ~0.2 s
    sv_backend: Literal["redimnet2", "wespeaker"] = "wespeaker"
    challenge_whisper_model: str = "tiny.en"   # digit check only; the shared STT (base.en) is ~2x slower on a Pi
    cm_required: bool = False          # True: an unavailable anti-spoof model makes the voice result not-confident
    # gate: a spoof flag makes voice not-confident; advisory: scored and logged only; off: not run.
    # Advisory by the user's decision: on the XVF3800, AASIST-L (ASVspoof2019 LA only) scored the live driver 0.00 on
    # ch0 and ranked a phone replay above live speech on ch1. Replay defence is the random digit challenge.
    cm_mode: Literal["gate", "advisory", "off"] = "advisory"
    cm_spoof_threshold: float = 0.5    # bona fide probability below this is treated as a spoof (gate mode)
    # overridden per device by `driveos voice-check --apply` (stored 0600 next to the voice template)
    cm_channel: Literal["ch0", "ch1", "mix"] = "ch0"   # which mic channel the anti-spoof model reads; mix = (ch0 + ch1) / 2
    cm_ch1_gain: float = 3.0           # ch1 is mic 3 after echo cancellation only, ~26 dB quieter than ch0; applied to ch1 and mix
    cm_hard_floor: float = 0.0         # > 0: below this the attempt is rejected outright; between it and the threshold voice stays not-confident (step-up)
    challenge_beam_size: int = 5       # beam for the digit check (the shared STT uses 1)
    challenge_digits: int = 4
    capture_start_timeout_s: float = 6.0
    capture_max_s: float = 6.0
    # "auto": enforce only on a known mic array (name contains one of channel_check_devices); laptop and USB
    # headset mics duplicate mono across channels, which is not an attack
    channel_check: Literal["auto", "always", "off"] = "auto"
    channel_check_devices: list[str] = Field(default_factory=lambda: ["xvf3800", "respeaker", "seeed"])
    channel_corr_max: float = 0.9999   # two mic channels correlated above this with zero lag = injected mono
    # "live": real voice template and face rung (falls back to the plain simulated scan when not set up);
    # "simulated": scripted outcomes through the real DriveAuth ladder; "auto": live when a voice enrollment and
    # the models are present, else simulated
    mode: Literal["auto", "live", "simulated"] = "auto"
    # "auto": f455 when the device is present, else aibox; "f455" direct RealSense ID; "aibox" AI Box HTTP API;
    # "sim" scripted; "none" no face rung
    face_backend: Literal["auto", "f455", "aibox", "sim", "none"] = "auto"
    f455_lib: str | None = None        # directory holding rsid_py*.so (env DRIVEOS_F455_LIB, default ~/repos/RealSenseID/build-device/lib)
    face_url: str = "http://127.0.0.1:8442/v1/face/authenticate"
    face_user_id: str | None = None    # RealSense ID user that must authenticate (default: the one enrolled by DriveOS)
    face_timeout_s: float = 8.0
    # live camera image in the Face ID sheet (F455 over USB video; RAM only, never written to disk)
    preview: bool = True
    preview_fps: float = 15.0          # the F455 delivers 15 fps; lower it to save CPU
    preview_size: int = 240
    preview_mirror: bool = True
    sim_voice: Literal["accept", "impostor", "spoof", "unavailable"] = "accept"
    sim_face: Literal["accept", "impostor", "spoof", "unavailable"] = "accept"
    # risk context fed to DriveAuth: paying more than this far from the merchant is out of the trusted zone
    merchant_far_km: float = 5.0
    strict_driveauth_version: bool = False
    otp_timeout_s: float = 120.0

    @field_validator("home")
    @classmethod
    def _expand_home(cls, v: Path) -> Path:
        return Path(os.path.expanduser(str(v)))

    @property
    def models_dir(self) -> Path:
        return self.home / "models"

    @property
    def store_dir(self) -> Path:
        return self.home / "driveauth-store"


class VoiceConfig(BaseModel):
    enabled: bool = True
    # "parakeet": mudler/parakeet.cpp over ctypes (CPU, build with scripts/build_parakeet.sh); falls back to whisper
    # when the library or model is missing. "whisper": faster-whisper. "none": typed commands only.
    stt: Literal["parakeet", "whisper", "none"] = "parakeet"
    parakeet_lib: Path | None = None     # None: env DRIVEOS_PARAKEET_LIB, then <models>/parakeet/libparakeet.so
    parakeet_model: Path | None = None   # None: env DRIVEOS_PARAKEET_MODEL, then <models>/parakeet/tdt_ctc-110m-q8_0.gguf
    whisper_model: str = "base.en"
    whisper_compute_type: str = "int8"
    whisper_device: str = "auto"
    tts: Literal["phonon", "kokoro", "pyttsx3", "say", "none"] = "phonon"
    phonon_voice: str | None = None  # None = the model's default voice
    phonon_duration_delta: float = -0.25  # negative = faster speech (about 1.15x at -0.25); 0 = original rate
    kokoro_model: Path | None = None
    kokoro_voices: Path | None = None
    kokoro_voice: str = "af_heart"
    # "phrase": on-device keyword spotting with a small Whisper model (any phrase, default "hey nova")
    # "openwakeword": a trained wake-word model (wake_model); "none": push-to-talk only
    wake: Literal["phrase", "openwakeword", "none"] = "phrase"
    wake_phrase: str = "hey nova"
    wake_aliases: list[str] = Field(default_factory=lambda: ["hi nova", "hey no va", "a nova", "hey noah",
                                                              "hey nava", "okay nova", "ok nova", "nova"])
    wake_whisper_model: str = "tiny.en"
    wake_engine: Literal["parakeet", "whisper"] = "parakeet"   # parakeet shares the loaded STT model; else tiny.en
    wake_model: str = "hey_jarvis"
    wake_threshold: float = 0.5
    follow_up_s: float = 8.0  # keep listening this long after Nova asks a question (no wake word needed)
    echo_tail_ms: int = 400   # keep the mic closed this long after Nova stops speaking (speaker echo decay)
    # wake once, then converse: Silero segments speech, Smart Turn judges pauses (RAM only, models are bundled)
    vad: Literal["silero", "webrtc"] = "silero"       # "webrtc" = the legacy fixed-window endpointer
    turn_detector: Literal["smart", "none"] = "smart"   # "none" = a Silero pause always ends the turn
    conversation_idle_s: float = 60.0                 # close the conversation after this long without speech
    conversation_max_utterance_s: float = 60.0
    silero_model: Path | None = None                  # None = models/silero_vad.onnx
    smart_turn_model: Path | None = None              # None = models/smart-turn-v3.2-cpu.onnx
    ptt_key: str = "Space"
    input_device: str | int | None = None
    output_device: str | int | None = None
    sample_rate: int = 16000
    vad_aggressiveness: int = 2
    end_silence_ms: int = 700
    max_utterance_s: float = 9.0
    language: str = "en"


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8790


class OpenRouterConfig(BaseModel):
    """OpenRouter transport and model choice. Model ids live here, never in logic."""
    api_key: SecretStr | None = None            # also read from OPENROUTER_API_KEY (env or .env)
    base_url: str = "https://openrouter.ai/api/v1"
    primary_model: str = "deepseek/deepseek-v4.1-flash"
    fallback_model: str = "anthropic/claude-haiku-5.5"
    router_model: str = "openai/gpt-5.6-luna"   # one-shot structured work and summaries, never planning
    reasoning_effort: str = "none"              # "none", not "minimal": minimal still reasons on some models
    max_tokens: int = 200
    temperature: float | None = None            # unset: Haiku rejects it, and require_parameters then finds no host
    # provider routing; deepseek was observed on a single host, so these are exposed rather than assumed
    provider_order: list[str] = Field(default_factory=list)
    zdr: bool = True                            # only route to zero-data-retention endpoints
    provider_sort: str | None = "latency"
    allow_fallbacks: bool = True
    require_parameters: bool = True
    data_collection: Literal["allow", "deny"] = "deny"
    cache_breakpoint: bool = True               # mark the stable prefix for providers that cache by breakpoint
    app_title: str = "DriveOS"
    app_referer: str = "https://localhost/driveos"


class BudgetConfig(BaseModel):
    max_steps: int = 4                          # model calls per turn
    call_timeout_s: float = 3.5                 # wait for the first byte of one model call
    deadline_s: float = 6.0                     # whole turn
    max_in_tokens: int = 3500
    max_cost_usd: float = 0.01                  # per turn
    daily_cap_usd: float = 2.0
    breaker_failures: int = 2
    breaker_open_s: float = 30.0
    tool_timeout_s: float = 8.0


class SessionConfig(BaseModel):
    store_path: Path | None = None              # default <drive-os home>/sessions.db
    resume_window_s: float = 1800.0
    window_turns: int = 6
    idle_compact_s: float = 90.0
    turn_retention_days: int = 30
    summary_retention_days: int = 90


class ExaConfig(BaseModel):
    """Exa web search for the agent's `web_search` tool. Hidden from the model without a key."""
    enabled: bool = True
    api_key: SecretStr | None = None            # also read from EXA_API_KEY (env or .env)
    base_url: str = "https://api.exa.ai"
    type: Literal["lite", "auto", "deep"] = "lite"   # lite: lowest latency, cached content only
    num_results: int = Field(5, ge=1, le=10)
    timeout_s: float = Field(2.5, gt=0, le=10)
    max_chars: int = Field(1200, ge=200, le=4000)    # highlight text handed to the model, all results together

    @property
    def active(self) -> bool:
        return self.enabled and self.api_key is not None


class NluConfig(BaseModel):
    # "rules": on-device parser only. "harness": rules first, then the OpenRouter tool agent for the turns the
    # rules can't place ("llm" is the old name for it).
    engine: Literal["rules", "harness", "llm"] = "rules"
    openrouter: OpenRouterConfig = OpenRouterConfig()
    budget: BudgetConfig = BudgetConfig()
    session: SessionConfig = SessionConfig()
    router_enabled: bool = False                # optional one-shot router (T1); off until the eval says it pays
    exa: ExaConfig = ExaConfig()

    @property
    def agent_enabled(self) -> bool:
        return self.engine in ("harness", "llm")


class VehicleConfig(BaseModel):
    # "simulator" drives the route for you; "gps" follows the real position (switchable in the app)
    source: Literal["simulator", "gps", "gpsd"] = "simulator"
    # auto | qt | gpsd[:host:port] | serial:/dev/ttyACM0[@9600] | tcp:host:port | udp:port | replay[:file][@speed] | ip
    gps_source: str = "auto"
    gpsd_host: str = "127.0.0.1"
    gpsd_port: int = 2947
    simulator_speed_mps: float = 15.6  # ~35 mph cruising
    simulator_time_scale: float = 1.0
    outside_temp_f: float | None = 64.0
    start: GeoPoint = GeoPoint(lat=37.23580, lon=-121.97460)  # Los Gatos, CA
    start_label: str = "Los Gatos, California"
    start_heading: float = 0.0


def _default_places() -> list[SavedPlace]:
    return [
        SavedPlace(id="work", name="Work", location=GeoPoint(lat=37.25900, lon=-121.96270), aliases=["office"]),
        SavedPlace(id="home", name="Home", location=GeoPoint(lat=37.22050, lon=-121.98480), aliases=["house"]),
    ]


def _default_payers() -> list[Payer]:
    return [
        Payer(id="visa-4417", display="Visa ending 4417", brand="visa", last4="4417", owner_name="Singh",
              label="Personal", provider="simulated", token="pm_card_visa"),
        Payer(id="amex-1009", display="Amex ending 1009", brand="amex", last4="1009", owner_name="Singh",
              label="Work", provider="simulated", token="pm_card_amex"),
    ]


def _default_occupants() -> list[Occupant]:
    return [
        Occupant(id="singh", name="Singh", role="owner", payer_ids=["visa-4417", "amex-1009"],
                 default_payer_id="visa-4417", phone="+14085550117",
                 usual={"merchant_id": "r3-plur-coffee", "item_id": "CALI_CAESGFg1VlNCUkZMVlZHUEs3M0FUN0tDWlJEWg",
                        "selections": {}}),
    ]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DRIVEOS_", env_nested_delimiter="__", extra="ignore",
                                      env_file=os.environ.get("DRIVEOS_ENV_FILE", ".env"), env_file_encoding="utf-8")

    mode: Literal["demo", "live"] = "demo"
    data_dir: Path = Field(default_factory=_default_data_dir)
    log_level: str = "INFO"
    log_json: bool = False
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    display: DisplayConfig = DisplayConfig()
    mapbox: MapboxConfig = MapboxConfig()
    nav: NavConfig = NavConfig()
    google_places: GooglePlacesConfig = GooglePlacesConfig()
    square: SquareConfig = SquareConfig()
    stripe: StripeConfig = StripeConfig()
    merchants: MerchantsConfig = MerchantsConfig()
    payments: PaymentsConfig = PaymentsConfig()
    policy: PolicyConfig = PolicyConfig()
    auth: AuthConfig = AuthConfig()
    biometrics: BiometricsConfig = BiometricsConfig()
    voice: VoiceConfig = VoiceConfig()
    nlu: NluConfig = NluConfig()
    server: ServerConfig = ServerConfig()
    vehicle: VehicleConfig = VehicleConfig()
    location: LocationConfig = LocationConfig()
    images: ImagesConfig = ImagesConfig()
    places: list[SavedPlace] = Field(default_factory=_default_places)
    payers: list[Payer] = Field(default_factory=_default_payers)
    occupants: list[Occupant] = Field(default_factory=_default_occupants)
    active_occupant: str = "singh"
    default_destination: str = "work"

    @field_validator("data_dir")
    @classmethod
    def _expand(cls, v: Path) -> Path:
        return Path(os.path.expanduser(str(v)))

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings, dotenv_settings]
        path = os.environ.get("DRIVEOS_CONFIG")
        candidates = [Path(path)] if path else [
            Path.home() / ".config" / "driveos" / "config.toml",
            Path("/etc/driveos/config.toml"),
        ]
        for p in candidates:
            if p.is_file():
                sources.append(TomlConfigSettingsSource(settings_cls, toml_file=p))
                break
        sources.append(file_secret_settings)
        return tuple(sources)

    # convenience -------------------------------------------------------------
    def occupant(self, occupant_id: str | None = None) -> Occupant:
        oid = occupant_id or self.active_occupant
        for o in self.occupants:
            if o.id == oid:
                return o
        raise KeyError(f"unknown occupant {oid}")

    def payer(self, payer_id: str) -> Payer:
        for p in self.payers:
            if p.id == payer_id:
                return p
        raise KeyError(f"unknown payer {payer_id}")

    def place(self, place_id: str) -> SavedPlace | None:
        return next((p for p in self.places if p.id == place_id), None)

    @property
    def demo(self) -> bool:
        return self.mode == "demo"


def _read_env_key(name: str) -> str | None:
    """`name` from the process environment, else from the .env that Settings reads or the project's own."""
    v = os.environ.get(name)
    if v:
        return v.strip()
    explicit = os.environ.get("DRIVEOS_ENV_FILE")
    candidates = [Path(explicit)] if explicit else [Path(".env"), Path(__file__).resolve().parents[2] / ".env"]
    for p in candidates:
        try:
            if not p.is_file():
                continue
            for line in p.read_text(encoding="utf-8").splitlines():
                k, sep, val = line.strip().partition("=")
                if sep and k.strip() == name and val.strip():
                    return val.strip().strip("\"'")
        except OSError:
            continue
    return None


def load_settings(**overrides) -> Settings:
    s = Settings(**overrides)
    if s.nlu.openrouter.api_key is None and (key := _read_env_key("OPENROUTER_API_KEY")):
        s.nlu.openrouter.api_key = SecretStr(key)
    if s.nlu.exa.api_key is None and (key := _read_env_key("EXA_API_KEY")):
        s.nlu.exa.api_key = SecretStr(key)
    if s.demo and "local" not in s.merchants.sources:
        # demo mode must always work offline: keep the in-process catalogue as the last-resort source
        s.merchants.sources = [*s.merchants.sources, "local"]
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
