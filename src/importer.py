"""Import manually downloaded IMD files into the immutable raw store.

Use this when automated download is blocked or not permitted. Source files
are copied (never moved or modified) into ``raw/<dataset_id>/`` under the
canonical name, checked, checksummed and recorded with origin
``manual_import`` so reports keep them distinct from downloaded files.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from .raw_store import (InvalidRawFile, Manifest, check_raw_content, expected_grd_size,
                        make_read_only, raw_path, sha256, utcnow)
from .source_discovery import DatasetInfo

log = logging.getLogger(__name__)

_YEAR = re.compile(r"(?<!\d)(19\d\d|20\d\d)(?!\d)")


def identify(path: Path, datasets: list[DatasetInfo]) -> tuple[DatasetInfo, int] | None:
    """Work out which dataset and year a manually downloaded file belongs to."""
    years = _YEAR.findall(path.name)
    if not years:
        return None
    year = int(years[-1])
    name = path.name.lower()
    ext = path.suffix.lower()
    size = path.stat().st_size
    candidates = []
    for ds in datasets:
        if ds.file_format == "netcdf" and ext in (".nc", ".nc4"):
            candidates.append(ds)
        elif ds.file_format == "imd_grd" and ext == ".grd" and size == expected_grd_size(ds, year):
            candidates.append(ds)
    if len(candidates) > 1:
        # 1-degree tmax and tmin files have identical sizes: disambiguate by name.
        for key, var in (("max", "tmax"), ("min", "tmin")):
            if key in name:
                candidates = [d for d in candidates if d.variable == var]
                break
    return (candidates[0], year) if len(candidates) == 1 else None


def import_manual_files(import_dir: Path, datasets: list[DatasetInfo], years: dict[str, list[int]],
                        raw_dir: Path, manifest: Manifest) -> list[dict]:
    """Copy recognised files for the requested dataset-years into raw/. Returns per-file results."""
    import_dir = Path(import_dir)
    results = []
    if not import_dir.is_dir():
        log.warning("manual_import_dir %s does not exist", import_dir)
        return results
    for src in sorted(p for p in import_dir.rglob("*") if p.is_file()):
        if src.suffix.lower() not in (".grd", ".nc", ".nc4"):
            continue
        hit = identify(src, datasets)
        if hit is None:
            results.append({"source": str(src), "status": "unrecognised",
                            "reason": "could not determine dataset/year from name and size"})
            continue
        ds, year = hit
        if year not in years.get(ds.id, []):
            continue
        target = raw_path(raw_dir, ds, year)
        res = {"source": str(src), "dataset_id": ds.id, "year": year, "path": str(target)}
        try:
            check_raw_content(ds, src, year)
        except InvalidRawFile as exc:
            results.append({**res, "status": "invalid", "reason": str(exc)})
            continue
        digest = sha256(src)
        if target.exists():
            old = manifest.get(target) or {}
            same = old.get("sha256") == digest
            results.append({**res, "status": "already_present" if same else "conflict",
                            "reason": "" if same else "different content already in raw store; not overwritten"})
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        make_read_only(target)
        manifest.record(target, dataset_id=ds.id, variable=ds.variable, year=year, origin="manual_import",
                        status="complete", sha256=digest, size=target.stat().st_size,
                        imported_from=str(src), original_filename=src.name, imported_utc=utcnow())
        log.info("Imported %s -> %s", src, target)
        results.append({**res, "status": "imported", "sha256": digest})
    return results
