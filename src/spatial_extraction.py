"""Subset a native IMD grid to a region without changing its resolution.

Two cell-selection rules (config ``cell_selection``):
  * ``centre``    - a cell is selected if its centre lies inside the region.
  * ``intersect`` - a cell is selected if any part of it overlaps the region.
For every cell the exact overlap area with the region is computed in an
equal-area CRS. Those overlaps are used only for the optional area-weighted
regional series, which is a derived average of grid-cell values, not an
observation at district/block/village scale.
"""

from __future__ import annotations

import numpy as np
import geopandas as gpd
import pandas as pd
import shapely
import xarray as xr
from shapely.geometry.base import BaseGeometry

from .boundary_processing import EQUAL_AREA_CRS
from .source_discovery import GridSpec


class EmptySelection(ValueError):
    pass


def grid_cells(grid: GridSpec, bounds=None) -> gpd.GeoDataFrame:
    """Cell polygons for the grid, optionally limited to cells touching ``bounds``."""
    lats, lons, h = grid.lats, grid.lons, grid.res / 2
    if bounds is not None:
        minx, miny, maxx, maxy = bounds
        lats = lats[(lats + h >= miny) & (lats - h <= maxy)]
        lons = lons[(lons + h >= minx) & (lons - h <= maxx)]
    LA, LO = np.meshgrid(lats, lons, indexing="ij")
    la, lo = LA.ravel(), LO.ravel()
    polys = shapely.box(lo - h, la - h, lo + h, la + h)
    return gpd.GeoDataFrame({"lat": la, "lon": lo}, geometry=polys, crs="EPSG:4326")


def cell_weights(region_geom: BaseGeometry, grid: GridSpec, method: str = "centre") -> gpd.GeoDataFrame:
    """One row per cell near the region with overlap area and the selection flag."""
    cells = grid_cells(grid, region_geom.bounds)
    centres = shapely.points(cells["lon"].to_numpy(), cells["lat"].to_numpy())
    cells["centre_in_region"] = shapely.contains_xy(region_geom, cells["lon"], cells["lat"]) | \
        shapely.touches(region_geom, centres)
    ea_cells = cells.to_crs(EQUAL_AREA_CRS)
    ea_region = gpd.GeoSeries([region_geom], crs="EPSG:4326").to_crs(EQUAL_AREA_CRS).iloc[0]
    overlap = shapely.area(shapely.intersection(ea_cells.geometry.values, ea_region))
    cells["cell_area_km2"] = ea_cells.area.to_numpy() / 1e6
    cells["overlap_km2"] = overlap / 1e6
    cells["area_fraction"] = (cells["overlap_km2"] / cells["cell_area_km2"]).clip(0, 1)
    if method == "centre":
        cells["selected"] = cells["centre_in_region"]
    elif method == "intersect":
        cells["selected"] = cells["overlap_km2"] > 0
    else:
        raise ValueError(f"unknown cell_selection '{method}'")
    cells["cell_id"] = [f"{la:.2f}N_{lo:.2f}E" for la, lo in zip(cells["lat"], cells["lon"])]
    return cells[(cells["overlap_km2"] > 0) | cells["selected"]].reset_index(drop=True)


def selection_mask(weights: gpd.GeoDataFrame, lats, lons) -> xr.DataArray:
    sel = weights[weights["selected"]]
    m = xr.DataArray(np.zeros((len(lats), len(lons)), bool), dims=("lat", "lon"),
                     coords={"lat": lats, "lon": lons})
    li = np.searchsorted(lats, sel["lat"].to_numpy())
    lj = np.searchsorted(lons, sel["lon"].to_numpy())
    m.values[li, lj] = True
    return m


def subset_to_region(da: xr.DataArray, weights: gpd.GeoDataFrame, grid: GridSpec) -> tuple[xr.DataArray, xr.DataArray]:
    """Crop to the bounding box of selected cells and mask unselected cells.

    Values of selected cells are untouched; the grid spacing and cell centres
    are identical to the source.
    """
    sel = weights[weights["selected"]]
    if sel.empty:
        raise EmptySelection(
            "No grid-cell centre falls inside the region (it is smaller than or offset from the "
            f"{grid.res} deg cells). Use cell_selection: intersect, which keeps every overlapping cell."
        )
    eps = 1e-6
    sub = da.sel(lat=slice(sel["lat"].min() - eps, sel["lat"].max() + eps),
                 lon=slice(sel["lon"].min() - eps, sel["lon"].max() + eps))
    mask = selection_mask(weights, sub["lat"].values, sub["lon"].values)
    return sub.where(mask), mask


def area_weighted_series(sub: xr.DataArray, weights: gpd.GeoDataFrame) -> tuple[pd.Series, dict]:
    """Daily overlap-area-weighted mean of the native cells covering the region.

    Cells that have no data on every day (outside IMD's land mask, e.g. sea
    cells on a coastline) are excluded and reported. On any day where an
    otherwise valid overlapping cell is missing, the regional value is NaN —
    it is not re-weighted or filled.
    """
    w = weights[weights["overlap_km2"] > 0]
    lat = xr.DataArray(w["lat"].to_numpy(), dims="cell")
    lon = xr.DataArray(w["lon"].to_numpy(), dims="cell")
    vals = sub.sel(lat=lat, lon=lon, method="nearest", tolerance=1e-4) if _covers(sub, w) else None
    if vals is None:
        raise ValueError("internal: subset does not contain all overlapping cells")
    v = vals.values  # (time, cell)
    never = np.all(np.isnan(v), axis=0)
    wt = w["overlap_km2"].to_numpy()
    use = ~never
    vv, ww = v[:, use], wt[use]
    any_missing = np.isnan(vv).any(axis=1)
    mean = np.where(any_missing, np.nan, np.nansum(vv * ww, axis=1) / ww.sum())
    s = pd.Series(mean, index=pd.DatetimeIndex(sub["time"].values, name="date"), name=sub.name)
    info = {
        "method": "overlap-area-weighted mean of native grid-cell values (equal-area CRS EPSG:6933)",
        "n_cells_used": int(use.sum()),
        "n_cells_excluded_no_data": int(never.sum()),
        "excluded_overlap_fraction_of_region": float(wt[never].sum() / wt.sum()) if wt.sum() else 0.0,
        "n_days_set_missing_due_to_missing_cells": int(any_missing.sum()),
        "interpretation": "Spatial average of IMD grid cells; not a measurement at district/block/village scale.",
    }
    return s, info


def _covers(sub: xr.DataArray, w) -> bool:
    lats, lons = sub["lat"].values, sub["lon"].values
    return (w["lat"].min() >= lats.min() - 1e-6 and w["lat"].max() <= lats.max() + 1e-6 and
            w["lon"].min() >= lons.min() - 1e-6 and w["lon"].max() <= lons.max() + 1e-6)


def region_bbox_subset(da: xr.DataArray, weights: gpd.GeoDataFrame) -> xr.DataArray:
    """Crop to the bbox of *all* overlapping cells (used for the area-weighted series)."""
    eps = 1e-6
    return da.sel(lat=slice(weights["lat"].min() - eps, weights["lat"].max() + eps),
                  lon=slice(weights["lon"].min() - eps, weights["lon"].max() + eps))
