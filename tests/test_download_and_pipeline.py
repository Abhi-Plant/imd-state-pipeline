"""Download/restart behaviour (mocked HTTP), export/reload and end-to-end reproducibility."""

import os
import stat

import numpy as np
import pandas as pd
import pytest
import requests
import xarray as xr

from conftest import FakeResponse, FakeSession
from src.boundary_processing import BoundaryError
from src.downloader import download_year
from src.pipeline import run
from src.raw_store import Manifest, raw_path
from src.source_discovery import DATASETS

TMAX = DATASETS["IMD_TMAX_1DEG_GRD"]


@pytest.fixture
def body(sample):
    return (sample["manual_downloads"] / TMAX.filename(2019)).read_bytes()


def test_download_success_is_recorded_and_read_only(tmp_path, body):
    m = Manifest(tmp_path)
    s = FakeSession(FakeResponse(body))
    r = download_year(TMAX, 2019, tmp_path, m, session=s, backoff=0)
    p = raw_path(tmp_path, TMAX, 2019)
    assert r["status"] == "downloaded" and m.verify(p)[0]
    assert s.calls == [(TMAX.download_endpoint, {"maxtemp": "2019"})]
    assert not os.stat(p).st_mode & stat.S_IWRITE
    assert m.get(p)["origin"] == "downloaded"


def test_interrupted_download_leaves_no_file_and_restart_completes(tmp_path, body):
    m = Manifest(tmp_path)
    broken = FakeSession(*[FakeResponse(body, fail_after=100_000) for _ in range(2)])
    r = download_year(TMAX, 2019, tmp_path, m, session=broken, max_retries=2, backoff=0)
    p = raw_path(tmp_path, TMAX, 2019)
    assert r["status"] == "failed" and not p.exists()
    assert not list(p.parent.glob("*.part"))
    assert m.get(p)["status"] == "failed"
    # Restart: resumes by fetching the missing year
    r2 = download_year(TMAX, 2019, tmp_path, Manifest(tmp_path), session=FakeSession(FakeResponse(body)), backoff=0)
    assert r2["status"] == "downloaded"
    # Third run: nothing downloaded again
    s3 = FakeSession()
    r3 = download_year(TMAX, 2019, tmp_path, Manifest(tmp_path), session=s3)
    assert r3["status"] == "skipped_existing" and s3.calls == []


def test_retry_then_success(tmp_path, body):
    s = FakeSession(requests.Timeout("slow"), FakeResponse(b"", status=503), FakeResponse(body))
    r = download_year(TMAX, 2019, tmp_path, Manifest(tmp_path), session=s, max_retries=3, backoff=0)
    assert r["status"] == "downloaded" and len(s.calls) == 3


def test_short_content_length_detected(tmp_path, body):
    resp = FakeResponse(body[:1000], headers={"Content-Length": str(len(body))})
    r = download_year(TMAX, 2019, tmp_path, Manifest(tmp_path), session=FakeSession(resp), max_retries=1, backoff=0)
    assert r["status"] == "failed" and "incomplete" in r["reason"]


def test_html_instead_of_data_is_not_saved(tmp_path):
    html = b"<html><body>Data for this year is not available</body></html>"
    r = download_year(TMAX, 2019, tmp_path, Manifest(tmp_path), session=FakeSession(FakeResponse(html)), backoff=0)
    assert r["status"] == "unavailable_or_invalid" and not raw_path(tmp_path, TMAX, 2019).exists()


def test_web_filter_interception_is_reported_and_not_retried(tmp_path):
    exc = requests.ConnectionError("SSLError(1, '[SSL: WRONG_VERSION_NUMBER] wrong version number') "
                                   "<title>Seqrite Endpoint Protection</title>")
    s = FakeSession(exc, exc, exc)
    r = download_year(TMAX, 2019, tmp_path, Manifest(tmp_path), session=s, max_retries=3, backoff=0)
    assert r["status"] == "failed" and "intercepted by 'Seqrite Endpoint Protection'" in r["reason"]
    assert len(s.calls) == 1


def test_refetch_never_overwrites_raw_file(tmp_path, body):
    m = Manifest(tmp_path)
    download_year(TMAX, 2019, tmp_path, m, session=FakeSession(FakeResponse(body)), backoff=0)
    p = raw_path(tmp_path, TMAX, 2019)
    original = p.read_bytes()
    changed = bytearray(body)
    changed[5000:5004] = np.float32(12.5).tobytes()
    r = download_year(TMAX, 2019, tmp_path, m, session=FakeSession(FakeResponse(bytes(changed))),
                      resume=False, backoff=0)
    assert r["status"] == "conflict_upstream_changed"
    assert p.read_bytes() == original


def test_tampered_raw_file_is_detected(tmp_path, body):
    m = Manifest(tmp_path)
    download_year(TMAX, 2019, tmp_path, m, session=FakeSession(FakeResponse(body)), backoff=0)
    p = raw_path(tmp_path, TMAX, 2019)
    os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
    with open(p, "r+b") as f:
        f.write(b"\x00\x00\x00\x00")
    ok, why = m.verify(p)
    assert not ok and "checksum mismatch" in why


# --- end to end ------------------------------------------------------------------------------

def test_end_to_end_all_formats_and_validation(make_cfg):
    cfg = make_cfg(variables=["rainfall", "tmax", "tmin"],
                   output_format=["NetCDF", "GeoTIFF", "CSV", "GeoPackage"])
    rep = run(cfg)
    assert {v["status"] for v in rep["variables"].values()} == {"processed"}
    assert rep["validation_summary"]["all_passed"] is True
    assert rep["tmax_tmin_consistency"]["n_tmax_below_tmin"] == 1
    q = rep["variables"]["rainfall"]["qc"]
    assert q["days_missing_all_cells_list"] == ["2019-07-09"]
    nc = next(cfg.exports_dir.glob("IMD_rainfall_0p25deg_synthetica_20190101-20201231.nc"))
    with xr.open_dataset(nc) as d:
        assert d["rainfall"].attrs["units"] == "mm" and d.sizes["time"] == 731
        assert np.allclose(np.diff(d.lat), 0.25)
    origins = {f["origin"] for f in rep["variables"]["rainfall"]["files_used"]}
    assert origins == {"manual_import"}


def test_reproducible_outputs(make_cfg, tmp_path):
    a = run(make_cfg(output_dir=str(tmp_path / "a")))
    b = run(make_cfg(output_dir=str(tmp_path / "b")))
    assert a["config_fingerprint"] == b["config_fingerprint"]
    for name in ("cells_daily.csv", "region_daily_areaweighted.csv", "region_monthly.csv"):
        fa = next((tmp_path / "a").rglob(f"*{name}")).read_bytes()
        fb = next((tmp_path / "b").rglob(f"*{name}")).read_bytes()
        assert fa == fb, name
    with xr.open_dataset(next((tmp_path / "a").rglob("*.nc"))) as x, xr.open_dataset(next((tmp_path / "b").rglob("*.nc"))) as y:
        xr.testing.assert_identical(x["rainfall"], y["rainfall"])


def test_requested_range_beyond_data_is_reported_not_filled(make_cfg):
    rep = run(make_cfg(start_date="2020-11-01", end_date="2021-01-31"))
    t = rep["variables"]["rainfall"]["time"]
    assert t["missing_days_count"] == 31 and t["missing_day_ranges"] == [["2021-01-01", "2021-01-31"]]
    assert rep["validation_summary"]["all_passed"] is False  # coverage check fails honestly
    daily = pd.read_csv(next(f for f in rep["files_created"] if f.endswith("areaweighted.csv")))
    assert daily["date"].max() == "2020-12-31"


def test_invalid_state_stops_before_processing(make_cfg):
    with pytest.raises(BoundaryError, match="not found"):
        run(make_cfg(state="Atlantis"))


def test_subdistrict_smaller_than_cell(make_cfg):
    rep = run(make_cfg(districts=["South District"], subdistricts=["Tiny Tehsil"], cell_selection="intersect"))
    assert rep["variables"]["rainfall"]["spatial"]["n_selected_cells"] == 1
    assert any("smaller than one 0.25 deg cell" in w for w in rep["warnings"])
