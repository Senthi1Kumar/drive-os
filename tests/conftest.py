from __future__ import annotations

import os

# tests must not read a developer's .env with real keys (read when driveos.config is imported)
os.environ["DRIVEOS_ENV_FILE"] = os.devnull

import pytest  # noqa: E402

from driveos.config import load_settings
from driveos.core.keys import FileKeyStore
from driveos.merchants.local import LocalMerchantProvider
from driveos.merchants.local_catalog import LOS_GATOS_CENTER, LocalCatalog
from driveos.merchants.registry import MerchantRegistry
from driveos.payments.simulated import SimulatedPayments
from driveos.wallet.authority import TransactionAuthority
from driveos.wallet.ledger import Ledger
from driveos.wallet.saga import OrderOrchestrator


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    # never pick up a developer's ~/.config/driveos/config.toml or real keys
    monkeypatch.setenv("DRIVEOS_CONFIG", str(tmp_path / "none.toml"))
    for k in ("DRIVEOS_MAPBOX__TOKEN", "DRIVEOS_PAYMENTS__PROVIDER", "GOOGLE_PLACES_API_KEY",
              "DRIVEOS_NLU__ENGINE", "OPENROUTER_API_KEY", "EXA_API_KEY"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture(autouse=True)
def _fresh_preview_hub():
    from driveos.biometrics.preview import HUB

    HUB.reset()
    yield
    HUB.reset()


@pytest.fixture
def settings(tmp_path):
    s = load_settings(data_dir=tmp_path / "data")
    s.biometrics.home = tmp_path / "drive-os"
    s.biometrics.mode = "live"
    s.biometrics.face_backend = "aibox"
    s.merchants.sources = ["local"]
    s.location.auto = False       # existing tests start in Los Gatos; location switching has its own tests
    return s


@pytest.fixture(scope="session")
def catalog():
    c = LocalCatalog()
    c.ensure_venues(LOS_GATOS_CENTER)
    return c


@pytest.fixture
def ledger(settings):
    lg = Ledger(settings.data_dir / "ledger.sqlite3", FileKeyStore(settings.data_dir / "key.pem"))
    yield lg
    lg.close()


@pytest.fixture
def demo(catalog):
    return LocalMerchantProvider(catalog, time_scale=200, accept_delay_s=0.05)


@pytest.fixture
def stack(settings, ledger, demo):
    reg = MerchantRegistry([demo])
    pay = SimulatedPayments(latency_s=0)
    auth = TransactionAuthority(settings, ledger, reg, pay)
    orch = OrderOrchestrator(settings, ledger, auth, reg, pay)
    return {"registry": reg, "payments": pay, "authority": auth, "orch": orch, "demo": demo}


def _has_dataset() -> bool:
    from importlib import resources
    return resources.files("driveos.data.catalog").joinpath("dataset.json").is_file()


requires_dataset = pytest.mark.skipif(not _has_dataset(), reason="extended restaurant dataset not installed")
