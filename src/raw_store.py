"""Immutable raw-file store: manifest, checksums and content sniffing.

Raw files are written once, made read-only and recorded with their SHA-256.
Processing re-verifies the checksum before reading, so an accidentally
modified raw file is detected instead of silently used.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

from .source_discovery import DatasetInfo

NETCDF_MAGIC = (b"CDF\x01", b"CDF\x02", b"CDF\x05", b"\x89HDF\r\n\x1a\n")


class InvalidRawFile(Exception):
    pass


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def days_in_year(year: int) -> int:
    return 366 if calendar.isleap(year) else 365


def expected_grd_size(ds: DatasetInfo, year: int) -> int:
    return ds.grid.values_per_year(days_in_year(year)) * 4


def check_raw_content(ds: DatasetInfo, path: Path, year: int) -> None:
    """Raise InvalidRawFile if the file cannot be the expected IMD file."""
    size = path.stat().st_size
    if size == 0:
        raise InvalidRawFile(f"{path.name}: empty file")
    with open(path, "rb") as f:
        head = f.read(512)
    if head.lstrip()[:1] == b"<" or b"<html" in head.lower():
        raise InvalidRawFile(
            f"{path.name}: server returned an HTML page instead of data "
            "(IMD error page, 'data not available' notice, or a web-filter block page)"
        )
    if ds.file_format == "imd_grd":
        exp = expected_grd_size(ds, year)
        if size != exp:
            raise InvalidRawFile(
                f"{path.name}: size {size} bytes, expected {exp} for {days_in_year(year)} days on a "
                f"{ds.grid.nlat}x{ds.grid.nlon} grid (truncated/corrupt or wrong variable/year)"
            )
    elif ds.file_format == "netcdf":
        if not head.startswith(NETCDF_MAGIC):
            raise InvalidRawFile(f"{path.name}: not a NetCDF file (bad magic bytes)")


def make_read_only(path: Path) -> None:
    os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Manifest:
    """JSON manifest of raw files keyed by path relative to the raw directory."""

    def __init__(self, raw_dir: Path):
        self.raw_dir = Path(raw_dir)
        self.path = self.raw_dir / "manifest.json"
        self.entries: dict[str, dict] = {}
        if self.path.exists():
            self.entries = json.loads(self.path.read_text(encoding="utf-8")).get("files", {})

    def key(self, file_path: Path) -> str:
        return Path(file_path).resolve().relative_to(self.raw_dir.resolve()).as_posix()

    def get(self, file_path: Path) -> dict | None:
        return self.entries.get(self.key(file_path))

    def record(self, file_path: Path, **info) -> dict:
        k = self.key(file_path)
        entry = {**self.entries.get(k, {}), **info, "updated_utc": utcnow()}
        self.entries[k] = entry
        self.save()
        return entry

    def save(self) -> None:
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"files": self.entries}, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def verify(self, file_path: Path) -> tuple[bool, str]:
        """True if the file exists, is recorded complete, and its checksum still matches."""
        entry = self.get(file_path)
        if not Path(file_path).exists():
            return False, "file missing"
        if not entry or entry.get("status") != "complete":
            return False, "not recorded as complete in manifest"
        if sha256(Path(file_path)) != entry.get("sha256"):
            return False, "checksum mismatch: raw file changed since it was recorded"
        return True, "ok"


def raw_path(raw_dir: Path, ds: DatasetInfo, year: int) -> Path:
    return Path(raw_dir) / ds.id / ds.filename(year)
