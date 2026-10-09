import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import build_config  # noqa: E402
from src.synthetic import make_sample  # noqa: E402


@pytest.fixture(scope="session")
def sample(tmp_path_factory):
    """Synthetic IMD-format files for 2019 (non-leap) and 2020 (leap) + fictional boundary."""
    return make_sample(tmp_path_factory.mktemp("sample"))


@pytest.fixture
def make_cfg(sample, tmp_path):
    def _make(**kw):
        raw = {
            "state": "Synthetica", "start_date": "2019-01-01", "end_date": "2020-12-31",
            "variables": ["rainfall"], "boundary_source": {"path": str(sample["boundary"])},
            "output_dir": str(tmp_path / "out"), "output_format": ["NetCDF", "CSV"],
            "acquisition_mode": "manual", "manual_import_dir": str(sample["manual_downloads"]),
        }
        raw.update(kw)
        return build_config(raw, base_dir=tmp_path)
    return _make


class FakeResponse:
    def __init__(self, body: bytes, status=200, fail_after: int | None = None, headers=None):
        self.body, self.status_code, self.fail_after = body, status, fail_after
        self.headers = headers if headers is not None else {"Content-Length": str(len(body))}

    def iter_content(self, chunk_size=65536):
        import requests
        sent = 0
        for i in range(0, len(self.body), chunk_size):
            if self.fail_after is not None and sent >= self.fail_after:
                raise requests.ConnectionError("connection reset by peer (simulated)")
            chunk = self.body[i:i + chunk_size]
            sent += len(chunk)
            yield chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeSession:
    """Returns queued responses (or raises queued exceptions) for successive POSTs."""

    def __init__(self, *responses):
        self.queue = list(responses)
        self.calls = []

    def post(self, url, data=None, stream=False, timeout=None):
        self.calls.append((url, data))
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
