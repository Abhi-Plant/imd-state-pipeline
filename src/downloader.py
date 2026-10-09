"""Download IMD yearly files through the public form endpoint on the official pages.

One POST per year (the documented mechanism; there is no bulk archive).
Downloads stream to ``<name>.part`` and are only promoted to the final name
after the content check passes, so an interrupted transfer never leaves a
file that looks complete. The IMD form endpoint is not known to support HTTP
Range requests, so resume works at whole-file granularity: completed years are
skipped, partial years are re-fetched.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import requests

from .raw_store import (InvalidRawFile, Manifest, check_raw_content, make_read_only,
                        raw_path, sha256, utcnow)
from .source_discovery import DatasetInfo, diagnose_connection_error

log = logging.getLogger(__name__)

USER_AGENT = "imd-state-pipeline/1.0 (research; python-requests)"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class DownloadFailed(Exception):
    def __init__(self, reason: str, retryable: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def _fetch_once(session, ds: DatasetInfo, year: int, part: Path, timeout: float) -> dict:
    url = ds.download_endpoint
    try:
        with session.post(url, data={ds.post_field: str(year)}, stream=True,
                          timeout=(30, timeout)) as r:
            if r.status_code in RETRYABLE_STATUS:
                raise DownloadFailed(f"HTTP {r.status_code} from {url}", retryable=True)
            if r.status_code != 200:
                raise DownloadFailed(f"HTTP {r.status_code} from {url}")
            expected = r.headers.get("Content-Length")
            written = 0
            with open(part, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    if chunk:
                        f.write(chunk)
                        written += len(chunk)
            if expected is not None and int(expected) != written:
                raise DownloadFailed(f"incomplete transfer: {written} of {expected} bytes", retryable=True)
            return {"http_status": r.status_code, "content_length": expected, "bytes": written}
    except DownloadFailed:
        raise
    except (requests.Timeout, requests.ConnectionError) as exc:
        reason = diagnose_connection_error(exc, url)
        # A web-filter block will not go away by retrying.
        raise DownloadFailed(reason, retryable="intercepted" not in reason) from exc
    except requests.RequestException as exc:
        raise DownloadFailed(f"{type(exc).__name__}: {exc}") from exc


def download_year(ds: DatasetInfo, year: int, raw_dir: Path, manifest: Manifest,
                  session: requests.Session | None = None, resume: bool = True,
                  timeout: float = 120, max_retries: int = 3, backoff: float = 5.0) -> dict:
    """Ensure the raw file for (dataset, year) exists. Returns a status dict, never raises for network errors."""
    target = raw_path(raw_dir, ds, year)
    target.parent.mkdir(parents=True, exist_ok=True)
    result = {"dataset_id": ds.id, "year": year, "path": str(target)}

    if target.exists():
        ok, why = manifest.verify(target)
        if ok and resume:
            log.info("%s %s: already downloaded and verified, skipping", ds.id, year)
            return {**result, "status": "skipped_existing", "origin": manifest.get(target).get("origin")}
        if not ok and why.startswith("checksum mismatch"):
            return {**result, "status": "error", "reason": why}

    session = session or make_session()
    part = target.with_name(target.name + ".part")
    last_reason = ""
    for attempt in range(1, max_retries + 1):
        if part.exists():
            part.unlink()
        try:
            log.info("%s %s: POST %s (attempt %d/%d)", ds.id, year, ds.download_endpoint, attempt, max_retries)
            meta = _fetch_once(session, ds, year, part, timeout)
            check_raw_content(ds, part, year)
            break
        except InvalidRawFile as exc:
            last_reason = str(exc)
            part.unlink(missing_ok=True)
            log.warning("%s %s: %s", ds.id, year, last_reason)
            manifest_fail(manifest, target, ds, year, last_reason)
            return {**result, "status": "unavailable_or_invalid", "reason": last_reason}
        except DownloadFailed as exc:
            last_reason = exc.reason
            part.unlink(missing_ok=True)
            log.warning("%s %s: %s", ds.id, year, last_reason)
            if not exc.retryable or attempt == max_retries:
                manifest_fail(manifest, target, ds, year, last_reason)
                return {**result, "status": "failed", "reason": last_reason}
            time.sleep(backoff * 2 ** (attempt - 1))
    else:  # pragma: no cover - loop always breaks or returns
        return {**result, "status": "failed", "reason": last_reason}

    digest = sha256(part)
    if target.exists():
        # resume_download=false re-fetch: never overwrite raw data.
        old = manifest.get(target) or {}
        if old.get("sha256") == digest:
            part.unlink()
            return {**result, "status": "redownloaded_identical", "origin": old.get("origin")}
        conflict = target.with_name(f"{target.name}.upstream-{digest[:12]}")
        os.replace(part, conflict)
        make_read_only(conflict)
        manifest.record(conflict, dataset_id=ds.id, year=year, origin="downloaded", status="conflict",
                        sha256=digest, size=conflict.stat().st_size, source_url=ds.download_endpoint,
                        retrieved_utc=utcnow(),
                        note=f"Differs from existing {target.name}; IMD may have revised this year. Not used.")
        return {**result, "status": "conflict_upstream_changed", "conflict_path": str(conflict)}

    os.replace(part, target)
    make_read_only(target)
    manifest.record(target, dataset_id=ds.id, variable=ds.variable, year=year, origin="downloaded",
                    status="complete", sha256=digest, size=target.stat().st_size,
                    source_url=ds.download_endpoint, post_data={ds.post_field: str(year)},
                    retrieved_utc=utcnow(), http=meta)
    return {**result, "status": "downloaded", "origin": "downloaded", "sha256": digest}


def manifest_fail(manifest: Manifest, target: Path, ds: DatasetInfo, year: int, reason: str) -> None:
    if target.exists() and (manifest.get(target) or {}).get("status") == "complete":
        return  # keep the good record; a failed re-fetch does not invalidate it
    manifest.record(target, dataset_id=ds.id, variable=ds.variable, year=year, origin="downloaded",
                    status="failed", reason=reason, source_url=ds.download_endpoint, attempted_utc=utcnow())
