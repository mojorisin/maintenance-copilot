"""Tests for the C-MAPSS download/extract/load code, fully offline.

A fake archive with the same nested layout as NASA's is served from a
file:// URL, so we can test checksum verification and safe extraction.
"""

import hashlib
import io
import zipfile

import pytest

from maintenance_copilot.data.cmapss import (
    COLUMNS,
    ChecksumError,
    fetch_fd001,
    load_fd001,
    load_test_rul,
)

ROW = " ".join(["1", "1"] + ["0.5"] * 24)


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def archive(tmp_path):
    inner = _zip(
        {
            "train_FD001.txt": f"{ROW}\n{ROW}\n".encode(),
            "test_FD001.txt": f"{ROW}\n".encode(),
            "RUL_FD001.txt": b"112\n",
            "train_FD002.txt": b"not needed",
            "../../escape.txt": b"must never be written",
        }
    )
    outer = _zip({"6. Turbofan Engine Degradation Simulation Data Set/CMAPSSData.zip": inner})
    path = tmp_path / "nasa.zip"
    path.write_bytes(outer)
    return path, hashlib.sha256(outer).hexdigest()


def test_fetch_extracts_only_fd001(tmp_path, archive):
    path, digest = archive
    dest = tmp_path / "out"
    fetch_fd001(dest, url=path.as_uri(), sha256=digest)
    assert sorted(p.name for p in dest.iterdir()) == [
        "RUL_FD001.txt",
        "test_FD001.txt",
        "train_FD001.txt",
    ]
    assert not (tmp_path / "escape.txt").exists()


def test_fetch_rejects_tampered_archive(tmp_path, archive):
    path, _ = archive
    with pytest.raises(ChecksumError):
        fetch_fd001(tmp_path / "out", url=path.as_uri(), sha256="0" * 64)
    assert not (tmp_path / "out").exists()


def test_fetch_skips_download_when_files_exist(tmp_path, archive):
    path, digest = archive
    dest = tmp_path / "out"
    fetch_fd001(dest, url=path.as_uri(), sha256=digest)
    # A bad URL would fail if a second download were attempted.
    fetch_fd001(dest, url="file:///does/not/exist.zip", sha256=digest)


def test_loaders_parse_columns(tmp_path, archive):
    path, digest = archive
    dest = fetch_fd001(tmp_path / "out", url=path.as_uri(), sha256=digest)
    train = load_fd001("train", dest)
    assert list(train.columns) == COLUMNS
    assert len(train) == 2
    assert load_test_rul(dest).loc[1] == 112
