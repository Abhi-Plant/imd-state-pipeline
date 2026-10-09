"""Write processed subsets. All outputs keep the native IMD grid (no resampling)."""

from __future__ import annotations

import csv
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import xarray as xr
from rasterio.transform import from_origin

from .source_discovery import DatasetInfo

NODATA = -999.0


def base_name(ds: DatasetInfo, region_slug: str, start, end) -> str:
    return (f"IMD_{ds.variable}_{ds.resolution_label}_{region_slug}_"
            f"{pd.Timestamp(start):%Y%m%d}-{pd.Timestamp(end):%Y%m%d}")


def _attrs(ds: DatasetInfo, method: str, region: dict) -> dict:
    return {
        "title": f"{ds.long_name} - subset for {region['state']} ({region['level']}: {', '.join(region['names'])})",
        "source": f"India Meteorological Department (IMD), dataset {ds.id}",
        "source_url": ds.documented_page,
        "references": ds.citation,
        "cell_selection": method,
        "processing_note": ("Values are unmodified IMD grid-cell values at the native "
                            f"{ds.grid.res} deg resolution; cells outside the selection are NoData. "
                            "No resampling, interpolation or gap filling was applied."),
        "source_missing_value": ds.missing_value,
        "time_convention": ds.time_convention,
        "boundary_file": region["boundary_file"],
        "Conventions": "CF-1.8",
    }


def to_netcdf(da: xr.DataArray, mask: xr.DataArray, weights, ds: DatasetInfo, path: Path,
              method: str, region: dict) -> Path:
    out = xr.Dataset({ds.variable: da.astype("float32")})
    frac = xr.DataArray(np.zeros(mask.shape, "float32"), dims=mask.dims, coords=mask.coords)
    lats, lons = mask["lat"].values, mask["lon"].values
    li = np.clip(np.searchsorted(lats, weights["lat"].to_numpy() - 1e-6), 0, len(lats) - 1)
    lj = np.clip(np.searchsorted(lons, weights["lon"].to_numpy() - 1e-6), 0, len(lons) - 1)
    inside = np.isclose(lats[li], weights["lat"]) & np.isclose(lons[lj], weights["lon"])
    frac.values[li[inside], lj[inside]] = weights["area_fraction"].to_numpy()[inside]
    out["in_selection"] = mask.astype("int8")
    out["in_selection"].attrs = {"long_name": f"cell selected by '{method}' rule", "flag_values": "0, 1"}
    out["cell_area_fraction_in_region"] = frac
    out["cell_area_fraction_in_region"].attrs = {"long_name": "fraction of grid-cell area inside region", "units": "1"}
    out[ds.variable].attrs = {"units": ds.units, "long_name": ds.long_name,
                              "standard_name": {"rainfall": "lwe_thickness_of_precipitation_amount",
                                                "tmax": "air_temperature", "tmin": "air_temperature"}[ds.variable],
                              "cell_methods": {"rainfall": "time: sum (interval: 1 day)",
                                               "tmax": "time: maximum", "tmin": "time: minimum"}[ds.variable],
                              "grid_mapping": "crs"}
    out["lat"].attrs = {"units": "degrees_north", "standard_name": "latitude", "axis": "Y",
                        "resolution_deg": ds.grid.res}
    out["lon"].attrs = {"units": "degrees_east", "standard_name": "longitude", "axis": "X",
                        "resolution_deg": ds.grid.res}
    out["time"].attrs = {"standard_name": "time", "axis": "T"}
    out["crs"] = xr.DataArray(np.int32(0), attrs={"grid_mapping_name": "latitude_longitude",
                                                   "semi_major_axis": 6378137.0,
                                                   "inverse_flattening": 298.257223563,
                                                   "crs_wkt": 'GEOGCRS["WGS 84",ENSEMBLE["World Geodetic System 1984 ensemble"],ID["EPSG",4326]]',
                                                   "epsg_code": "EPSG:4326"})
    out.attrs = _attrs(ds, method, region)
    enc = {ds.variable: {"zlib": True, "complevel": 4, "_FillValue": NODATA, "dtype": "float32"},
           "time": {"units": "days since 1900-12-31", "calendar": "proleptic_gregorian", "dtype": "int32"}}
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_netcdf(path, encoding=enc, engine="netcdf4")
    return path


def to_geotiffs(da: xr.DataArray, ds: DatasetInfo, out_dir: Path, stem: str) -> list[Path]:
    """One multi-band GeoTIFF per year; band i = day i, description = ISO date."""
    out_dir.mkdir(parents=True, exist_ok=True)
    res = ds.grid.res
    lats, lons = da["lat"].values, da["lon"].values
    transform = from_origin(lons.min() - res / 2, lats.max() + res / 2, res, res)
    paths = []
    for year, yda in da.groupby("time.year"):
        arr = yda.values[:, ::-1, :]  # north-up rows
        arr = np.where(np.isnan(arr), NODATA, arr).astype("float32")
        p = out_dir / f"{stem}_{year}.tif"
        with rasterio.open(p, "w", driver="GTiff", height=arr.shape[1], width=arr.shape[2],
                           count=arr.shape[0], dtype="float32", crs="EPSG:4326", transform=transform,
                           nodata=NODATA, compress="deflate") as dst:
            dst.write(arr)
            dates = pd.DatetimeIndex(yda["time"].values)
            for i, d in enumerate(dates, start=1):
                dst.set_band_description(i, d.strftime("%Y-%m-%d"))
            dst.update_tags(variable=ds.variable, units=ds.units, source=f"IMD {ds.id}",
                            resolution_deg=str(res), first_date=str(dates[0].date()),
                            last_date=str(dates[-1].date()),
                            note="Native IMD grid values; NoData outside selection or where IMD reports missing.")
        paths.append(p)
    return paths


def to_csv(da: xr.DataArray, mask: xr.DataArray, ds: DatasetInfo, path: Path) -> tuple[Path, int]:
    """Long table: one row per selected cell per day. Missing values are blank with qc_flag=missing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    li, lj = np.nonzero(mask.values)
    lats, lons = da["lat"].values[li], da["lon"].values[lj]
    ids = [f"{a:.2f}N_{b:.2f}E" for a, b in zip(lats, lons)]
    times = pd.DatetimeIndex(da["time"].values).strftime("%Y-%m-%d")
    vals = da.values[:, li, lj]
    rows = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "cell_id", "lat", "lon", "variable", "value", "units", "qc_flag", "source"])
        for t, day in zip(times, vals):
            for cid, a, b, v in zip(ids, lats, lons, day):
                missing = np.isnan(v)
                w.writerow([t, cid, f"{a:.2f}", f"{b:.2f}", ds.variable, "" if missing else f"{v:.2f}",
                            ds.units, "missing" if missing else "ok", ds.id])
                rows += 1
    return path, rows


def to_geopackage(weights: gpd.GeoDataFrame, region_features: gpd.GeoDataFrame, cell_stats: pd.DataFrame,
                  path: Path) -> Path:
    """Grid-cell polygons with selection info and per-cell period statistics, plus the region boundary."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    cells = weights.merge(cell_stats, on="cell_id", how="left") if not cell_stats.empty else weights
    cells.to_file(path, layer="grid_cells", driver="GPKG")
    region_features.to_file(path, layer="region_boundary", driver="GPKG")
    return path


def cell_period_stats(da: xr.DataArray, mask: xr.DataArray, variable: str) -> pd.DataFrame:
    li, lj = np.nonzero(mask.values)
    vals = da.values[:, li, lj]
    n_days = vals.shape[0]
    n_missing = np.isnan(vals).sum(axis=0)
    with np.errstate(invalid="ignore"):
        mean = np.where(n_missing == 0, np.nanmean(vals, axis=0) if n_days else np.nan, np.nan)
    df = pd.DataFrame({"cell_id": [f"{a:.2f}N_{b:.2f}E" for a, b in
                                   zip(da["lat"].values[li], da["lon"].values[lj])],
                       f"{variable}_n_missing_days": n_missing})
    if variable == "rainfall":
        df["rainfall_mean_daily_mm"] = mean
    else:
        df[f"{variable}_mean_degC"] = mean
    return df
