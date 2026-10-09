"""Spatial extraction on a small grid, temporal slicing, QC and summaries."""

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from shapely.geometry import box

from src.quality_control import qc_variable, summarise_series
from src.source_discovery import GridSpec
from src.spatial_extraction import EmptySelection, area_weighted_series, cell_weights, subset_to_region
from src.temporal_extraction import DuplicateDates, combine_years, slice_time

SMALL = GridSpec(lon0=70.0, lat0=20.0, res=1.0, nlon=5, nlat=4)  # lon 70..74, lat 20..23


def small_da(days=3, start="2020-02-28"):
    vals = np.arange(days * SMALL.nlat * SMALL.nlon, dtype="float32").reshape(days, SMALL.nlat, SMALL.nlon)
    return xr.DataArray(vals, dims=("time", "lat", "lon"),
                        coords={"time": pd.date_range(start, periods=days), "lat": SMALL.lats, "lon": SMALL.lons},
                        name="rainfall")


def test_centre_vs_intersect_selection():
    region = box(70.6, 20.6, 72.6, 21.6)  # contains centres (21,71),(21,72); overlaps 2 rows x 3 cols
    wc = cell_weights(region, SMALL, "centre")
    wi = cell_weights(region, SMALL, "intersect")
    assert set(zip(wc[wc.selected].lat, wc[wc.selected].lon)) == {(21.0, 71.0), (21.0, 72.0)}
    assert wi.selected.sum() == 6
    np.testing.assert_allclose(wi.overlap_km2.sum(), wi.cell_area_km2.mean() * 2.0, rtol=0.03)


def test_subset_preserves_values_and_resolution():
    da = small_da()
    w = cell_weights(box(70.6, 20.6, 72.6, 21.6), SMALL, "intersect")
    sub, mask = subset_to_region(da, w, SMALL)
    assert np.allclose(np.diff(sub.lon), 1.0) and np.allclose(np.diff(sub.lat), 1.0)
    assert list(sub.lat.values) == [21.0, 22.0] and list(sub.lon.values) == [71.0, 72.0, 73.0]
    xr.testing.assert_equal(sub.where(mask), da.sel(lat=sub.lat, lon=sub.lon).where(mask))
    assert int(mask.sum()) == 6 and not sub.isnull().any()


def test_region_smaller_than_cell_needs_intersect():
    tiny = box(71.6, 21.6, 71.7, 21.7)  # no centre inside
    with pytest.raises(EmptySelection, match="intersect"):
        subset_to_region(small_da(), cell_weights(tiny, SMALL, "centre"), SMALL)
    w = cell_weights(tiny, SMALL, "intersect")
    sub, mask = subset_to_region(small_da(), w, SMALL)
    assert int(mask.sum()) == 1 and sub.lat.item() == 22.0 and sub.lon.item() == 72.0


def test_area_weighted_series_and_missing_cell_handling():
    da = small_da()
    region = box(70.5, 20.5, 72.5, 21.5)  # exactly two full cells (71,21),(72,21)
    w = cell_weights(region, SMALL, "intersect")
    s, info = area_weighted_series(da, w)
    expected = (da.sel(lat=21.0, lon=71.0) + da.sel(lat=21.0, lon=72.0)) / 2
    np.testing.assert_allclose(s.values, expected.values, rtol=1e-3)
    da2 = da.copy()
    da2.loc[{"time": "2020-02-29", "lat": 21.0, "lon": 72.0}] = np.nan
    s2, info2 = area_weighted_series(da2, w)
    assert np.isnan(s2.loc["2020-02-29"]) and info2["n_days_set_missing_due_to_missing_cells"] == 1
    da3 = da.copy()
    da3.loc[{"lat": 21.0, "lon": 72.0}] = np.nan  # permanently no-data cell: excluded, reported
    s3, info3 = area_weighted_series(da3, w)
    assert info3["n_cells_excluded_no_data"] == 1
    np.testing.assert_allclose(s3.values, da.sel(lat=21.0, lon=71.0).values)


def test_slice_across_leap_day_and_report_missing():
    da = small_da(days=3)  # 2020-02-28 .. 2020-03-01
    out, rep = slice_time(da, pd.Timestamp("2020-02-27").date(), pd.Timestamp("2020-03-01").date())
    assert rep["present_days"] == 3 and rep["missing_days_count"] == 1
    assert rep["missing_day_ranges"] == [["2020-02-27", "2020-02-27"]]
    assert rep["leap_days_present"] == rep["leap_days_expected"] == 1


def test_duplicate_dates_identical_dropped_conflicting_raise():
    a = small_da(days=3)
    b = a.isel(time=[2])
    assert combine_years([a, b]).sizes["time"] == 3
    with pytest.raises(DuplicateDates):
        combine_years([a, b + 1])


def test_qc_counts_negative_rain_without_changing_it():
    da = small_da()
    da[0, 0, 0] = -5.0
    mask = xr.ones_like(da.isel(time=0), dtype=bool)
    q = qc_variable(da, "rainfall", mask)
    assert q["negative_values"] == 1 and q["filled_or_modified_values"] == 0
    assert float(da[0, 0, 0]) == -5.0


def test_incomplete_periods_have_no_totals():
    idx = pd.date_range("2020-06-15", "2020-12-31")  # starts mid-June
    s = pd.Series(1.0, index=idx)
    s.loc["2020-08-10"] = np.nan
    t = summarise_series(s, "rainfall")
    m = t["monthly"].set_index("month")
    assert np.isnan(m.loc[6, "total_mm"])            # partial month (range starts mid-month)
    assert np.isnan(m.loc[8, "total_mm"])            # one missing day
    assert m.loc[7, "total_mm"] == 31
    seas = t["seasonal"].set_index("season")
    assert np.isnan(seas.loc["Monsoon (JJAS)", "total_mm"])
    assert seas.loc["Post-monsoon (OND)", "total_mm"] == 92
    assert np.isnan(t["annual"]["total_mm"].iloc[0])
