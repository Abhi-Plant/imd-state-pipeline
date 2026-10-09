"""Read IMD raw files into a common (time, lat, lon) form and describe them.

Readers never modify raw files. Source missing-value codes are converted to
NaN and counted; nothing is filled.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from .raw_store import days_in_year, expected_grd_size
from .source_discovery import DatasetInfo

log = logging.getLogger(__name__)

VAR_CANDIDATES = {"rainfall": ["RAINFALL", "rf", "rain", "rainfall", "RF"],
                  "tmax": ["tmax", "TMAX", "maxtemp", "MAXT", "max_temp"],
                  "tmin": ["tmin", "TMIN", "mintemp", "MINT", "min_temp"]}
LAT_CANDIDATES = ["LATITUDE", "lat", "latitude", "Lat", "y"]
LON_CANDIDATES = ["LONGITUDE", "lon", "longitude", "Lon", "x"]
TIME_CANDIDATES = ["TIME", "time", "Time", "t"]


class CorruptFile(Exception):
    pass


def _sentinel_mask(values: np.ndarray, sentinel: float) -> np.ndarray:
    return np.isclose(values, sentinel, atol=1e-3)


def _finish(values: np.ndarray, ds: DatasetInfo, year: int, lats, lons, source: Path,
            extra: dict | None = None) -> tuple[xr.DataArray, dict]:
    values = values.astype("float32", copy=True)
    sentinel = _sentinel_mask(values, ds.missing_value)
    nonfinite = ~np.isfinite(values) & ~sentinel
    n_sentinel, n_nonfinite = int(sentinel.sum()), int(nonfinite.sum())
    values[sentinel] = np.nan
    time = pd.date_range(f"{year}-01-01", periods=values.shape[0], freq="D")
    da = xr.DataArray(values, dims=("time", "lat", "lon"),
                      coords={"time": time, "lat": np.asarray(lats, float), "lon": np.asarray(lons, float)},
                      name=ds.variable, attrs={"units": ds.units, "long_name": ds.long_name})
    info = {
        "file": str(source),
        "dataset_id": ds.id,
        "year": year,
        "shape_time_lat_lon": list(values.shape),
        "time_first": str(time[0].date()),
        "time_last": str(time[-1].date()),
        "n_days": int(values.shape[0]),
        "expected_days": days_in_year(year),
        "lat_range": [float(np.min(lats)), float(np.max(lats))],
        "lon_range": [float(np.min(lons)), float(np.max(lons))],
        "units": ds.units,
        "source_missing_value": ds.missing_value,
        "n_source_missing_values": n_sentinel,
        "n_nan_in_source": n_nonfinite,  # NetCDF: values masked via _FillValue on decode
        "calendar": "proleptic_gregorian (daily, leap years have 366 days)",
        "crs": ds.crs,
        **(extra or {}),
    }
    return da, info


def read_grd(path: Path, ds: DatasetInfo, year: int) -> tuple[xr.DataArray, dict]:
    """IMD .grd: little-endian float32, day-major, then lat (S->N) rows of lon (W->E)."""
    path = Path(path)
    exp = expected_grd_size(ds, year)
    size = path.stat().st_size
    if size != exp:
        raise CorruptFile(f"{path.name}: {size} bytes, expected {exp}")
    g = ds.grid
    arr = np.fromfile(path, dtype="<f4").reshape(days_in_year(year), g.nlat, g.nlon)
    return _finish(arr, ds, year, g.lats, g.lons, path,
                   {"format": "IMD binary (.grd)", "source_layout": "time, lat(S->N), lon(W->E)",
                    "coordinate_names": "implicit (grid defined by IMD documentation)"})


def _pick(names, candidates, what, path):
    for c in candidates:
        if c in names:
            return c
    lower = {n.lower(): n for n in names}
    for c in candidates:
        if c.lower() in lower:
            return lower[c.lower()]
    raise CorruptFile(f"{Path(path).name}: no {what} found among {sorted(names)}")


def read_netcdf(path: Path, ds: DatasetInfo, year: int) -> tuple[xr.DataArray, dict]:
    path = Path(path)
    try:
        src = xr.open_dataset(path, decode_times=True, mask_and_scale=True)
    except Exception as exc:  # noqa: BLE001 - surface as corrupt input
        raise CorruptFile(f"{path.name}: cannot open as NetCDF ({exc})") from exc
    with src:
        names = set(src.variables)
        vname = _pick(set(src.data_vars), VAR_CANDIDATES[ds.variable], "data variable", path)
        latn = _pick(names, LAT_CANDIDATES, "latitude coordinate", path)
        lonn = _pick(names, LON_CANDIDATES, "longitude coordinate", path)
        timen = _pick(names, TIME_CANDIDATES, "time coordinate", path)
        var = src[vname]
        if set(var.dims) != {latn, lonn, timen}:
            raise CorruptFile(f"{path.name}: unexpected dimensions {var.dims}")
        original_order = list(var.dims)
        lat_desc = bool(src[latn].values[0] > src[latn].values[-1])
        var = var.transpose(timen, latn, lonn).sortby(latn).sortby(lonn)
        times = pd.DatetimeIndex(var[timen].values).normalize()
        attrs_units = var.attrs.get("units", "")
        fill = var.encoding.get("_FillValue", var.encoding.get("missing_value"))
        values = var.values
        lats, lons = var[latn].values, var[lonn].values
        cal = src[timen].encoding.get("calendar", "unknown")

    dup = times[times.duplicated()]
    expected = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")
    if len(dup):
        raise CorruptFile(f"{path.name}: duplicate dates {[str(d.date()) for d in dup[:5]]}")
    if not times.equals(expected):
        missing = expected.difference(times)
        extra = times.difference(expected)
        raise CorruptFile(f"{path.name}: time axis does not cover {year} exactly "
                          f"(missing {len(missing)} days, {len(extra)} unexpected days)")
    g = ds.grid
    if values.shape[1:] != (g.nlat, g.nlon) or not (np.allclose(lats, g.lats, atol=1e-3)
                                                    and np.allclose(lons, g.lons, atol=1e-3)):
        raise CorruptFile(f"{path.name}: grid {values.shape[1:]} does not match documented "
                          f"{g.nlat}x{g.nlon} {g.res} deg IMD grid")
    da, info = _finish(values, ds, year, g.lats, g.lons, path, {
        "format": "NetCDF",
        "coordinate_names": {"variable": vname, "lat": latn, "lon": lonn, "time": timen},
        "source_dimension_order": original_order,
        "source_lat_descending": lat_desc,
        "source_units_attr": attrs_units,
        "source_fill_value": None if fill is None else float(fill),
        "source_calendar": cal,
    })
    if attrs_units and attrs_units.lower() not in ("mm", "mm/day", "mm day-1", "degc", "c", "celsius", "deg c"):
        info["warning"] = f"unexpected units attribute '{attrs_units}'"
    return da, info


def read_raw(path: Path, ds: DatasetInfo, year: int) -> tuple[xr.DataArray, dict]:
    if ds.file_format == "imd_grd":
        return read_grd(path, ds, year)
    return read_netcdf(path, ds, year)
