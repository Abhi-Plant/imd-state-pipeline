"""Load a user-supplied administrative boundary and select the requested region.

The pipeline never invents boundaries: state, district and sub-district
polygons must come from the boundary file named in the config. Weather
grids carry no administrative information.
"""

from __future__ import annotations

import difflib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
from shapely import make_valid
from shapely.geometry.base import BaseGeometry

from .config import BoundaryConfig

log = logging.getLogger(__name__)

EQUAL_AREA_CRS = "EPSG:6933"  # WGS 84 / NSIDC EASE-Grid 2.0 Global (equal area)

STATE_FIELDS = ["ST_NM", "STATE", "STATE_NAME", "State", "state", "st_nm", "stname", "STNAME",
                "NAME_1", "shapeName", "state_name", "STATE_UT"]
DISTRICT_FIELDS = ["DISTRICT", "District", "district", "dtname", "DTNAME", "NAME_2", "dist_name", "DIST_NAME"]
SUBDISTRICT_FIELDS = ["sdtname", "SDTNAME", "TEHSIL", "Tehsil", "tehsil", "TALUK", "Taluk", "taluk",
                      "BLOCK", "Block", "block", "SUB_DIST", "subdistrict", "NAME_3", "VILLAGE", "village", "NAME_4"]


class BoundaryError(ValueError):
    pass


@dataclass
class Region:
    state: str
    level: str  # state | district | subdistrict
    names: list[str]
    features: gpd.GeoDataFrame  # selected features, EPSG:4326
    geometry: BaseGeometry  # dissolved, EPSG:4326
    area_km2: float
    fields_used: dict
    source_crs: str
    n_repaired: int
    boundary_file: str
    warnings: list[str] = field(default_factory=list)

    @property
    def slug(self) -> str:
        base = slugify(self.state)
        if self.level == "state":
            return base
        tail = "-".join(slugify(n) for n in self.names[:3]) + ("-etc" if len(self.names) > 3 else "")
        return f"{base}_{tail}"

    def summary(self) -> dict:
        minx, miny, maxx, maxy = self.geometry.bounds
        return {"state": self.state, "level": self.level, "names": self.names,
                "boundary_file": self.boundary_file, "fields_used": self.fields_used,
                "source_crs": self.source_crs, "n_features": len(self.features),
                "n_geometries_repaired": self.n_repaired, "area_km2": round(self.area_km2, 1),
                "bounds_lon_lat": [round(v, 4) for v in (minx, miny, maxx, maxy)],
                "warnings": self.warnings}


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def normalise_name(s) -> str:
    s = str(s).casefold().replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _find_field(gdf, explicit, candidates, what, required):
    if explicit:
        if explicit not in gdf.columns:
            raise BoundaryError(f"{what} field '{explicit}' not in boundary file. Columns: {list(gdf.columns)}")
        return explicit
    for c in candidates:
        if c in gdf.columns:
            return c
    if required:
        raise BoundaryError(f"Could not find a {what} name field. Set boundary_source.{what}_field. "
                            f"Columns: {[c for c in gdf.columns if c != 'geometry']}")
    return None


def _match(requested: list[str], available, what: str) -> list[str]:
    avail = {normalise_name(a): a for a in available if a is not None}
    out = []
    for r in requested:
        n = normalise_name(r)
        if n in avail:
            out.append(avail[n])
            continue
        close = difflib.get_close_matches(n, list(avail), n=5, cutoff=0.6)
        hint = f" Did you mean: {', '.join(avail[c] for c in close)}?" if close else ""
        raise BoundaryError(f"{what} '{r}' not found in boundary file.{hint}")
    return out


def load_boundary_file(cfg: BoundaryConfig) -> gpd.GeoDataFrame:
    path = Path(cfg.path)
    if not path.exists():
        raise BoundaryError(f"Boundary file not found: {path}")
    try:
        gdf = gpd.read_file(path, layer=cfg.layer) if cfg.layer else gpd.read_file(path)
    except Exception as exc:  # noqa: BLE001
        raise BoundaryError(f"Cannot read boundary file {path}: {exc}") from exc
    if gdf.empty:
        raise BoundaryError(f"Boundary file {path} has no features")
    if gdf.crs is None:
        raise BoundaryError(f"Boundary file {path} has no CRS. Define it (e.g. a .prj file) rather than "
                            "letting the pipeline guess.")
    return gdf


def load_region(cfg: BoundaryConfig, state: str, districts=(), subdistricts=()) -> Region:
    gdf = load_boundary_file(cfg)
    warnings = []
    src_crs = gdf.crs.to_string()

    invalid = ~gdf.geometry.is_valid
    n_rep = int(invalid.sum())
    if n_rep:
        gdf.loc[invalid, "geometry"] = gdf.loc[invalid, "geometry"].apply(make_valid)
        warnings.append(f"{n_rep} invalid geometries repaired with shapely.make_valid")
    gdf = gdf[~gdf.geometry.is_empty & gdf.geometry.notna()]
    gdf = gdf.to_crs("EPSG:4326")

    sf = _find_field(gdf, cfg.state_field, STATE_FIELDS, "state", required=True)
    (state_name,) = _match([state], gdf[sf].unique(), "State")
    sel = gdf[gdf[sf] == state_name]
    fields = {"state": sf}
    level, names = "state", [state_name]

    if districts:
        df = _find_field(sel, cfg.district_field, DISTRICT_FIELDS, "district", required=True)
        names = _match(districts, sel[df].unique(), f"District (within {state_name})")
        sel = sel[sel[df].isin(names)]
        fields["district"] = df
        level = "district"
    if subdistricts:
        sdf = _find_field(sel, cfg.subdistrict_field, SUBDISTRICT_FIELDS, "subdistrict", required=True)
        names = _match(subdistricts, sel[sdf].unique(), "Sub-district")
        sel = sel[sel[sdf].isin(names)]
        fields["subdistrict"] = sdf
        level = "subdistrict"

    geom = make_valid(sel.geometry.union_all())
    area = float(gpd.GeoSeries([geom], crs="EPSG:4326").to_crs(EQUAL_AREA_CRS).area.iloc[0] / 1e6)
    if not (60 <= geom.bounds[0] <= 100 and 5 <= geom.bounds[1] <= 40):
        warnings.append("Region bounds fall outside the Indian IMD grid domain; check the boundary CRS.")
    log.info("Region %s (%s): %.0f km2 from %s", names, level, area, cfg.path)
    return Region(state=state_name, level=level, names=names, features=sel.reset_index(drop=True),
                  geometry=geom, area_km2=area, fields_used=fields, source_crs=src_crs,
                  n_repaired=n_rep, boundary_file=str(cfg.path), warnings=warnings)
