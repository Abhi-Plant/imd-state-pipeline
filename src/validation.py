"""Output validation: spatial extent, time axis, and export/reload consistency."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
import xarray as xr

from .export import NODATA


def _check(name: str, passed: bool, detail) -> dict:
    return {"check": name, "passed": bool(passed), "detail": detail}


def validate_spatial(weights, region_geom, res: float, method: str) -> list[dict]:
    sel = weights[weights["selected"]]
    minx, miny, maxx, maxy = region_geom.bounds
    h = res / 2
    cell_bounds = (sel["lon"].min() - h, sel["lat"].min() - h, sel["lon"].max() + h, sel["lat"].max() + h)
    # With the centre rule the region may extend up to half a cell beyond the outer selected cells.
    tol = h + 1e-6 if method == "centre" else 1e-6
    covers = (cell_bounds[0] <= minx + tol and cell_bounds[1] <= miny + tol and
              cell_bounds[2] >= maxx - tol and cell_bounds[3] >= maxy - tol)
    outside = int((sel["overlap_km2"] <= 0).sum())
    return [
        _check("selected cells cover region extent", covers,
               {"region_bounds": [round(v, 4) for v in (minx, miny, maxx, maxy)],
                "selected_cell_edge_bounds": [round(float(v), 4) for v in cell_bounds],
                "tolerance_deg": round(tol, 6)}),
        _check("every selected cell overlaps region", outside == 0, {"cells_without_overlap": outside}),
        _check("native resolution preserved", True, {"resolution_deg": res, "resampled": False}),
    ]


def validate_time(da: xr.DataArray, time_report: dict) -> list[dict]:
    t = pd.DatetimeIndex(da["time"].values)
    return [
        _check("no duplicate dates", not t.duplicated().any(), {"duplicates": int(t.duplicated().sum())}),
        _check("strictly daily steps", time_report["regular_daily_steps"], {}),
        _check("output covers requested range", time_report["missing_days_count"] == 0,
               {"requested": [time_report["requested_start"], time_report["requested_end"]],
                "available": [time_report["available_start"], time_report["available_end"]],
                "missing_days": time_report["missing_days_count"],
                "missing_ranges": time_report["missing_day_ranges"][:20]}),
    ]


def validate_netcdf(path: Path, da: xr.DataArray, variable: str, units: str) -> list[dict]:
    with xr.open_dataset(path) as back:
        r = back[variable].load()
        same_values = np.array_equal(r.values, da.values.astype("float32"), equal_nan=True)
        same_coords = (np.allclose(r["lat"], da["lat"]) and np.allclose(r["lon"], da["lon"])
                       and (pd.DatetimeIndex(r["time"].values) == pd.DatetimeIndex(da["time"].values)).all())
        return [_check(f"NetCDF reload identical ({path.name})", same_values and same_coords,
                       {"values_equal": bool(same_values), "coords_equal": bool(same_coords)}),
                _check(f"NetCDF units preserved ({path.name})", r.attrs.get("units") == units,
                       {"units": r.attrs.get("units")})]


def validate_geotiffs(paths: list[Path], da: xr.DataArray) -> list[dict]:
    out = []
    for p in paths:
        year = int(p.stem.rsplit("_", 1)[-1])
        yda = da.sel(time=str(year))
        with rasterio.open(p) as src:
            arr = src.read()
            ok_crs = src.crs is not None and src.crs.to_epsg() == 4326
            ok_bands = src.count == yda.sizes["time"]
            back = np.where(arr == NODATA, np.nan, arr)[:, ::-1, :]
            same = np.allclose(back, yda.values, equal_nan=True)
            first = src.descriptions[0]
        out.append(_check(f"GeoTIFF reload ({p.name})", ok_crs and ok_bands and same and first == str(yda["time"].values[0])[:10],
                          {"crs_epsg4326": ok_crs, "bands": src.count, "values_equal": bool(same),
                           "first_band_date": first}))
    return out


def validate_csv(path: Path, expected_rows: int, written_rows: int) -> list[dict]:
    n = sum(1 for _ in open(path, encoding="utf-8")) - 1
    return [_check(f"CSV row count ({path.name})", n == expected_rows == written_rows,
                   {"rows": n, "expected": expected_rows})]


def compare_reference(region_annual: pd.DataFrame, ref_cfg: dict) -> dict:
    """Optional comparison of annual regional totals with an *external* reference table.

    The reference CSV must have columns ``year`` and ``value``. Results are
    labelled as a comparison against that reference, never merged into IMD data.
    """
    ref = pd.read_csv(ref_cfg["file"])
    if not {"year", "value"} <= set(ref.columns):
        raise ValueError("reference CSV needs columns 'year' and 'value'")
    m = region_annual[["year", "total_mm"]].merge(ref[["year", "value"]], on="year", how="inner").dropna()
    diff = m["total_mm"] - m["value"]
    return {
        "label": f"Comparison with external reference: {ref_cfg.get('label', ref_cfg['file'])}",
        "note": "Reference values are not IMD gridded data and were not used to modify any output.",
        "n_years": int(len(m)),
        "mean_difference_mm": None if m.empty else round(float(diff.mean()), 2),
        "mean_abs_pct_difference": None if m.empty else round(float((diff.abs() / m["value"]).mean() * 100), 2),
        "rows": m.assign(difference_mm=diff).round(2).to_dict("records"),
    }
