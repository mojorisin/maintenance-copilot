"""Download and load NASA's C-MAPSS turbofan degradation dataset (FD001 subset).

Source: NASA Prognostics Center of Excellence data repository,
"Turbofan Engine Degradation Simulation Data Set" (Saxena et al., 2008).
The raw files are downloaded at setup time and never committed.

FD001 = 100 training engines run to failure, 100 test engines cut off some
time before failure, one operating condition, one fault mode (HPC
degradation), plus the true remaining useful life (RUL) of each test engine.
"""

import hashlib
import io
import os
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

from maintenance_copilot.config import CMAPSS_DIR

CMAPSS_URL = os.getenv(
    "MC_CMAPSS_URL",
    "https://phm-datasets.s3.amazonaws.com/NASA/6.+Turbofan+Engine+Degradation+Simulation+Data+Set.zip",
)
CMAPSS_SHA256 = "c9c5dec12a945a82e8bb4446589d7fb3cc057b5e5d81fa1a12e25ee9912ad3b2"
FD001_FILES = ("train_FD001.txt", "test_FD001.txt", "RUL_FD001.txt")

# Sensor metadata from the C-MAPSS documentation. `informative` is False for
# sensors that are constant in FD001 and therefore carry no signal.
SENSORS = {
    "s1": ("T2", "Total temperature at fan inlet", "°R", False),
    "s2": ("T24", "Total temperature at LPC outlet", "°R", True),
    "s3": ("T30", "Total temperature at HPC outlet", "°R", True),
    "s4": ("T50", "Total temperature at LPT outlet", "°R", True),
    "s5": ("P2", "Pressure at fan inlet", "psia", False),
    "s6": ("P15", "Total pressure in bypass duct", "psia", False),
    "s7": ("P30", "Total pressure at HPC outlet", "psia", True),
    "s8": ("Nf", "Physical fan speed", "rpm", True),
    "s9": ("Nc", "Physical core speed", "rpm", True),
    "s10": ("epr", "Engine pressure ratio (P50/P2)", "-", False),
    "s11": ("Ps30", "Static pressure at HPC outlet", "psia", True),
    "s12": ("phi", "Ratio of fuel flow to Ps30", "pps/psi", True),
    "s13": ("NRf", "Corrected fan speed", "rpm", True),
    "s14": ("NRc", "Corrected core speed", "rpm", True),
    "s15": ("BPR", "Bypass ratio", "-", True),
    "s16": ("farB", "Burner fuel-air ratio", "-", False),
    "s17": ("htBleed", "Bleed enthalpy", "-", True),
    "s18": ("Nf_dmd", "Demanded fan speed", "rpm", False),
    "s19": ("PCNfR_dmd", "Demanded corrected fan speed", "rpm", False),
    "s20": ("W31", "HPT coolant bleed", "lbm/s", True),
    "s21": ("W32", "LPT coolant bleed", "lbm/s", True),
}
COLUMNS = ["unit", "cycle", "setting1", "setting2", "setting3", *SENSORS]
INFORMATIVE_SENSORS = [s for s, meta in SENSORS.items() if meta[3]]


class ChecksumError(RuntimeError):
    """The downloaded archive does not match the expected SHA-256."""


def _extract_fd001(archive: bytes, dest: Path) -> None:
    """Extract only the FD001 files from the (nested) NASA zip into `dest`.

    Files are written by their base name, so a crafted archive path such as
    "../../etc/x" can never escape `dest`.
    """
    outer = zipfile.ZipFile(io.BytesIO(archive))
    inner_name = next(n for n in outer.namelist() if n.endswith("CMAPSSData.zip"))
    inner = zipfile.ZipFile(io.BytesIO(outer.read(inner_name)))
    dest.mkdir(parents=True, exist_ok=True)
    for name in inner.namelist():
        base = Path(name).name
        if base in FD001_FILES:
            (dest / base).write_bytes(inner.read(name))


def fetch_fd001(
    dest: Path = CMAPSS_DIR, url: str = CMAPSS_URL, sha256: str = CMAPSS_SHA256
) -> Path:
    """Download, verify and extract FD001 into `dest`. Skips if already present."""
    if all((dest / f).exists() for f in FD001_FILES):
        return dest
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            archive = resp.read()
    except OSError as exc:
        raise RuntimeError(
            f"Could not download C-MAPSS from {url} ({exc}). Check your connection, or "
            "set MC_CMAPSS_URL to a mirror of NASA's 'Turbofan Engine Degradation "
            "Simulation Data Set' zip."
        ) from exc
    digest = hashlib.sha256(archive).hexdigest()
    if digest != sha256:
        raise ChecksumError(f"C-MAPSS archive SHA-256 is {digest}, expected {sha256}")
    _extract_fd001(archive, dest)
    return dest


def load_fd001(split: str, data_dir: Path = CMAPSS_DIR) -> pd.DataFrame:
    """Load the 'train' or 'test' split as a DataFrame with named columns."""
    if split not in ("train", "test"):
        raise ValueError("split must be 'train' or 'test'")
    path = data_dir / f"{split}_FD001.txt"
    return pd.read_csv(path, sep=r"\s+", header=None, names=COLUMNS)


def load_test_rul(data_dir: Path = CMAPSS_DIR) -> pd.Series:
    """True RUL at the last observed cycle of each test engine, indexed by unit."""
    rul = pd.read_csv(data_dir / "RUL_FD001.txt", header=None, names=["rul"])["rul"]
    rul.index = pd.RangeIndex(1, len(rul) + 1, name="unit")
    return rul
