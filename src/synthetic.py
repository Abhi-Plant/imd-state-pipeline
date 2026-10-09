"""Synthetic IMD-format files and a fictional boundary, for tests and demos.

Nothing here is IMD data. Values are random but deterministic (seeded), laid
out byte-for-byte like IMD's yearly .grd files (and an IMD-style NetCDF), so
the full pipeline can be exercised without network access. The boundary
describes a fictional state, 'Synthetica', not a real administrative unit.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr
from shapely.geometry import Polygon, box

from .raw_store import days_in_year
from .source_discovery import DATASETS, DatasetInfo


def _land(grid) -> np.ndarray:
    """Crude fictional 'land' mask: everything except a south-east 'sea' corner."""
    LA, LO = np.meshgrid(grid.lats, grid.lons, indexing="ij")
    return ~((LA < 12) & (LO > 85))


def synthetic_values(ds: DatasetInfo, year: int, seed: int = 0) -> np.ndarray:
    g = ds.grid
    nd = days_in_year(year)
    rng = np.random.default_rng(seed + year * 10 + {"rainfall": 0, "tmax": 1, "tmin": 2}[ds.variable])
    doy = np.arange(nd)[:, None, None]
    land = _land(g)[None]
    if ds.variable == "rainfall":
        season = np.clip(np.sin((doy - 150) / 120 * np.pi), 0, None)  # wet Jun-Sep
        wet = rng.random((nd, g.nlat, g.nlon)) < 0.05 + 0.6 * season
        vals = np.where(wet, rng.gamma(0.8, 12, (nd, g.nlat, g.nlon)), 0.0)
    else:
        base = 32 + 7 * np.sin((doy - 60) / 365 * 2 * np.pi)
        vals = base + rng.normal(0, 1.5, (nd, g.nlat, g.nlon))
        if ds.variable == "tmin":
            vals = vals - 12
    vals = np.where(land, vals, ds.missing_value).astype("<f4")
    return vals


def write_grd(ds: DatasetInfo, year: int, path: Path, seed: int = 0, tweak=None) -> Path:
    vals = synthetic_values(ds, year, seed)
    if tweak:
        tweak(vals)
    path.parent.mkdir(parents=True, exist_ok=True)
    vals.astype("<f4").tofile(path)
    return path


def write_imd_style_netcdf(ds: DatasetInfo, year: int, path: Path, seed: int = 0) -> Path:
    """Mimic IMD's NetCDF: RAINFALL(LONGITUDE, LATITUDE, TIME) with descending latitude, days since 1900-12-31."""
    g = ds.grid
    vals = synthetic_values(DATASETS["IMD_RF025_GRD"], year, seed)  # (time, lat, lon)
    arr = np.transpose(vals, (2, 1, 0))[:, ::-1, :].astype("float64")
    t = pd.date_range(f"{year}-01-01", periods=vals.shape[0], freq="D")
    dsx = xr.Dataset(
        {"RAINFALL": (("LONGITUDE", "LATITUDE", "TIME"), arr,
                      {"long_name": "Rainfall", "units": "mm", "missing_value": -999.0})},
        coords={"LONGITUDE": g.lons, "LATITUDE": g.lats[::-1], "TIME": t},
    )
    enc = {"RAINFALL": {"_FillValue": -999.0},
           "TIME": {"units": "days since 1900-12-31", "calendar": "gregorian"}}
    path.parent.mkdir(parents=True, exist_ok=True)
    dsx.to_netcdf(path, encoding=enc, format="NETCDF3_CLASSIC")
    return path


def write_boundary(path: Path) -> Path:
    """Fictional state 'Synthetica' (2 districts, 3 sub-districts) and a neighbour 'Otherland'."""
    north = Polygon([(76.1, 22.4), (79.3, 22.4), (78.9, 23.7), (77.0, 23.6), (76.1, 22.4)])
    south = Polygon([(76.4, 21.0), (79.1, 21.2), (79.3, 22.4), (76.1, 22.4), (76.4, 21.0)])
    sub_a = Polygon([(76.4, 21.0), (77.8, 21.1), (77.8, 22.4), (76.1, 22.4), (76.4, 21.0)])
    sub_b = south.difference(sub_a).difference(box(78.50, 21.50, 78.60, 21.60))
    tiny = box(78.50, 21.50, 78.60, 21.60)  # smaller than one 0.25 deg cell
    rows = [
        ("Synthetica", "North District", "North Tehsil", north),
        ("Synthetica", "South District", "West Tehsil", sub_a),
        ("Synthetica", "South District", "East Tehsil", sub_b),
        ("Synthetica", "South District", "Tiny Tehsil", tiny),
        ("Otherland", "Far District", "Far Tehsil", box(84.0, 25.0, 86.0, 27.0)),
    ]
    gdf = gpd.GeoDataFrame(
        {"ST_NM": [r[0] for r in rows], "DISTRICT": [r[1] for r in rows], "TEHSIL": [r[2] for r in rows],
         "NOTE": "FICTIONAL boundary for testing - not a real administrative unit"},
        geometry=[r[3] for r in rows], crs="EPSG:4326")
    path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(path, driver="GeoJSON")
    return path


def make_sample(root: Path, years=(2019, 2020)) -> dict:
    """Write a manual-download folder + boundary under ``root``. Returns paths."""
    root = Path(root)
    dl = root / "manual_downloads"
    for y in years:
        for ds_id in ("IMD_RF025_GRD", "IMD_TMAX_1DEG_GRD", "IMD_TMIN_1DEG_GRD"):
            ds = DATASETS[ds_id]
            tweak = None
            if ds_id == "IMD_RF025_GRD" and y == years[0]:
                def tweak(v, ds=ds):
                    # One fully missing day over the fictional state, and one isolated missing cell-day.
                    v[189, 55:72, 38:55] = ds.missing_value
                    v[200, 62, 45] = ds.missing_value
            if ds_id == "IMD_TMAX_1DEG_GRD" and y == years[-1]:
                def tweak(v):
                    v[100, 14, 10] = 5.0  # injects a tmax < tmin cell-day for the consistency check
            write_grd(ds, y, dl / ds.filename(y), tweak=tweak)
    bnd = write_boundary(root / "boundaries" / "synthetica_FICTIONAL.geojson")
    return {"manual_downloads": dl, "boundary": bnd}
