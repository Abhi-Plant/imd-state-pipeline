"""Config validation, boundary selection, readers, leap years, missing values, coordinate order."""

import numpy as np
import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from src.boundary_processing import BoundaryError, load_region
from src.config import BoundaryConfig, ConfigError
from src.importer import identify
from src.inspect_data import CorruptFile, read_grd, read_netcdf
from src.raw_store import InvalidRawFile, check_raw_content
from src.source_discovery import DATASETS, check_availability, resolve_dataset
from src.synthetic import write_grd, write_imd_style_netcdf

RAIN, TMAX, TMIN = DATASETS["IMD_RF025_GRD"], DATASETS["IMD_TMAX_1DEG_GRD"], DATASETS["IMD_TMIN_1DEG_GRD"]


# --- configuration -------------------------------------------------------------------------

def test_end_before_start_rejected(make_cfg):
    with pytest.raises(ConfigError, match="before start_date"):
        make_cfg(start_date="2020-01-01", end_date="2019-12-31")


def test_bad_date_format_rejected(make_cfg):
    with pytest.raises(ConfigError, match="ISO date"):
        make_cfg(start_date="01/01/2020")


def test_unknown_variable_and_format_rejected(make_cfg):
    with pytest.raises(ConfigError, match="Unknown variable"):
        make_cfg(variables=["humidity"])
    with pytest.raises(ConfigError, match="Unsupported output_format"):
        make_cfg(output_format=["xlsx"])


def test_non_imd_source_rejected():
    with pytest.raises(ValueError, match="Only official IMD"):
        resolve_dataset("rainfall", source="CHIRPS")


def test_availability_flags_years_outside_documented_range():
    from datetime import date
    a = check_availability(TMAX, date(1949, 1, 1), date(1952, 12, 31))
    assert a["years_before_dataset_start"] == [1949, 1950]
    assert a["attemptable_years"] == [1951, 1952]


# --- boundaries ----------------------------------------------------------------------------

def test_invalid_state_name_suggests_match(sample):
    with pytest.raises(BoundaryError, match="Did you mean: Synthetica"):
        load_region(BoundaryConfig(path=str(sample["boundary"])), "Synthetika")


def test_state_name_matching_is_case_and_punctuation_insensitive(sample):
    r = load_region(BoundaryConfig(path=str(sample["boundary"])), "  synthetica ")
    assert r.state == "Synthetica" and len(r.features) == 4


def test_district_and_subdistrict_selection(sample):
    b = BoundaryConfig(path=str(sample["boundary"]))
    d = load_region(b, "Synthetica", districts=["south district"])
    assert d.level == "district" and set(d.features["DISTRICT"]) == {"South District"}
    s = load_region(b, "Synthetica", districts=["South District"], subdistricts=["Tiny Tehsil"])
    assert s.level == "subdistrict" and s.area_km2 < 200
    with pytest.raises(BoundaryError, match="not found"):
        load_region(b, "Synthetica", districts=["Far District"])  # belongs to another state


def test_boundary_without_crs_rejected(tmp_path):
    p = tmp_path / "nocrs.geojson"
    gpd.GeoDataFrame({"ST_NM": ["X"]}, geometry=[Polygon([(77, 21), (78, 21), (78, 22)])]).to_file(p)
    # GeoJSON implies WGS84, so write a shapefile without .prj instead.
    shp = tmp_path / "nocrs.shp"
    gpd.read_file(p).set_crs(None, allow_override=True).to_file(shp)
    (tmp_path / "nocrs.prj").unlink(missing_ok=True)
    with pytest.raises(BoundaryError, match="no CRS"):
        load_region(BoundaryConfig(path=str(shp)), "X")


def test_invalid_geometry_is_repaired_and_reported(tmp_path):
    bowtie = Polygon([(77, 21), (78, 22), (78, 21), (77, 22), (77, 21)])
    p = tmp_path / "bowtie.geojson"
    gpd.GeoDataFrame({"ST_NM": ["Bow"]}, geometry=[bowtie], crs="EPSG:4326").to_file(p)
    r = load_region(BoundaryConfig(path=str(p)), "Bow")
    assert r.n_repaired == 1 and r.geometry.is_valid and r.area_km2 > 0


# --- raw files, leap years, missing values -------------------------------------------------

def test_leap_and_non_leap_years(sample):
    d = sample["manual_downloads"]
    da19, i19 = read_grd(d / RAIN.filename(2019), RAIN, 2019)
    da20, i20 = read_grd(d / RAIN.filename(2020), RAIN, 2020)
    assert da19.sizes["time"] == 365 and da20.sizes["time"] == 366
    assert str(da20["time"].values[59])[:10] == "2020-02-29"
    assert str(da19["time"].values[59])[:10] == "2019-03-01"


def test_missing_value_codes_become_nan_not_zero(sample):
    da, info = read_grd(sample["manual_downloads"] / RAIN.filename(2019), RAIN, 2019)
    assert info["n_source_missing_values"] > 0
    assert not np.isclose(da.values, -999).any()
    assert np.isnan(da.sel(time="2019-07-09", lat=22.0, lon=77.75).item())  # injected missing day
    t, ti = read_grd(sample["manual_downloads"] / TMAX.filename(2019), TMAX, 2019)
    assert ti["n_source_missing_values"] > 0 and not np.isclose(t.values, 99.9).any()


def test_truncated_file_detected(tmp_path, sample):
    src = (sample["manual_downloads"] / TMAX.filename(2019)).read_bytes()
    p = tmp_path / TMAX.filename(2019)
    p.write_bytes(src[:-4000])
    with pytest.raises(InvalidRawFile, match="expected"):
        check_raw_content(TMAX, p, 2019)
    with pytest.raises(CorruptFile):
        read_grd(p, TMAX, 2019)


def test_html_error_page_detected(tmp_path):
    p = tmp_path / RAIN.filename(2030)
    p.write_bytes(b"<!DOCTYPE html><html><title>Data not available</title></html>")
    with pytest.raises(InvalidRawFile, match="HTML"):
        check_raw_content(RAIN, p, 2030)


def test_missing_file_reported_by_manifest(tmp_path):
    from src.raw_store import Manifest
    ok, why = Manifest(tmp_path).verify(tmp_path / "nope.grd")
    assert not ok and why == "file missing"


def test_netcdf_coordinate_order_and_descending_lat_normalised(tmp_path, sample):
    nc = write_imd_style_netcdf(DATASETS["IMD_RF025_NC"], 2020, tmp_path / "RF25_ind2020_rfp25.nc")
    a, info = read_netcdf(nc, DATASETS["IMD_RF025_NC"], 2020)
    b, _ = read_grd(sample["manual_downloads"] / RAIN.filename(2020), RAIN, 2020)
    assert info["source_dimension_order"] == ["LONGITUDE", "LATITUDE", "TIME"]
    assert info["source_lat_descending"] is True
    assert a.dims == ("time", "lat", "lon")
    np.testing.assert_allclose(a.values, b.values, equal_nan=True)


def test_netcdf_with_missing_day_rejected(tmp_path):
    import xarray as xr
    nc = write_imd_style_netcdf(DATASETS["IMD_RF025_NC"], 2019, tmp_path / "full.nc")
    with xr.open_dataset(nc) as d:
        d.isel(TIME=slice(0, 300)).load().to_netcdf(tmp_path / "short_2019.nc")
    with pytest.raises(CorruptFile, match="does not cover 2019"):
        read_netcdf(tmp_path / "short_2019.nc", DATASETS["IMD_RF025_NC"], 2019)


def test_importer_identifies_tmax_vs_tmin_by_name(tmp_path):
    a = write_grd(TMAX, 2019, tmp_path / "my_max_2019.grd")
    b = write_grd(TMIN, 2019, tmp_path / "MinT-2019.GRD")
    c = write_grd(TMIN, 2019, tmp_path / "temperature_2019.grd")
    dss = [RAIN, TMAX, TMIN]
    assert identify(a, dss) == (TMAX, 2019)
    assert identify(b, dss) == (TMIN, 2019)
    assert identify(c, dss) is None  # ambiguous: refuse to guess
