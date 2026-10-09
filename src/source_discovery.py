"""Registry of official IMD gridded datasets and source-availability checks.

Every value here is taken from the official IMD Pune dataset pages or from the
two open-source readers that implement IMD's own download form (imdlib,
imdR). Where a fact could not be confirmed from an official page it is marked
in ``endpoint_provenance`` / ``notes`` rather than presented as verified.
See docs/SOURCE_REPORT.md for the full evidence trail.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import requests

log = logging.getLogger(__name__)

IMD_BASE_URL = "https://www.imdpune.gov.in/cmpg/Griddata"

CITATION_RAIN = (
    "Pai D.S., Sridhar L., Rajeevan M., Sreejith O.P., Satbhai N.S., Mukhopadhyay B. (2014). "
    "Development of a new high spatial resolution (0.25 x 0.25 degree) long period (1901-2010) "
    "daily gridded rainfall data set over India and its comparison with existing data sets over "
    "the region. MAUSAM 65(1):1-18. doi:10.54302/mausam.v65i1.851"
)
CITATION_TEMP = (
    "Srivastava A.K., Rajeevan M., Kshirsagar S.R. (2009). Development of a high resolution daily "
    "gridded temperature data set (1969-2005) for the Indian region. Atmospheric Science Letters "
    "10(4):249-254. doi:10.1002/asl.232"
)
ACCESS_NOTE = (
    "Free public download form on the IMD Pune 'Gridded Data Archive' pages; no login observed in "
    "the documented workflow. Station data and other products are supplied via the IMD Data Supply "
    "Portal (dsp.imdpune.gov.in) which requires registration and payment. Cite IMD and the dataset "
    "paper when using the data."
)


@dataclass(frozen=True)
class GridSpec:
    """Regular lat/lon grid; (lon0, lat0) is the centre of the south-west cell."""

    lon0: float
    lat0: float
    res: float
    nlon: int
    nlat: int

    @property
    def lons(self) -> np.ndarray:
        return np.round(self.lon0 + self.res * np.arange(self.nlon), 6)

    @property
    def lats(self) -> np.ndarray:
        return np.round(self.lat0 + self.res * np.arange(self.nlat), 6)

    def values_per_year(self, ndays: int) -> int:
        return ndays * self.nlat * self.nlon


@dataclass(frozen=True)
class DatasetInfo:
    id: str
    variable: str  # canonical pipeline variable: rainfall | tmax | tmin
    long_name: str
    units: str
    file_format: str  # imd_grd | netcdf
    grid: GridSpec
    missing_value: float
    first_year: int
    last_year_documented: int | None
    documented_page: str
    download_endpoint: str
    post_field: str
    filename_template: str
    citation: str
    endpoint_provenance: str
    temporal_frequency: str = "daily"
    crs: str = "EPSG:4326"
    time_convention: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def resolution_label(self) -> str:
        return f"{self.grid.res:g}deg".replace(".", "p")

    def filename(self, year: int) -> str:
        return self.filename_template.format(year=year)


RAIN_GRID = GridSpec(lon0=66.5, lat0=6.5, res=0.25, nlon=135, nlat=129)
TEMP_GRID = GridSpec(lon0=67.5, lat0=7.5, res=1.0, nlon=31, nlat=31)

_RAIN_TIME = (
    "Daily 24-h accumulation (IMD observational day ends 0830 IST / 0300 UTC, so each value spans two "
    "calendar days). Date labels are preserved exactly as in the source; whether they mark the start or "
    "end of the period is not confirmed here - check IMD documentation before lagging against other series."
)
_TEMP_TIME = "Daily maximum/minimum air temperature as labelled by IMD (dates in IST, preserved)."

DATASETS: dict[str, DatasetInfo] = {
    d.id: d
    for d in [
        DatasetInfo(
            id="IMD_RF025_GRD",
            variable="rainfall",
            long_name="IMD daily gridded rainfall (0.25 x 0.25 deg)",
            units="mm",
            file_format="imd_grd",
            grid=RAIN_GRID,
            missing_value=-999.0,
            first_year=1901,
            last_year_documented=2024,
            documented_page=f"{IMD_BASE_URL}/Rainfall_25_Bin.html",
            download_endpoint=f"{IMD_BASE_URL}/rainfall.php",
            post_field="rain",
            filename_template="Rainfall_ind{year}_rfp25.grd",
            citation=CITATION_RAIN,
            endpoint_provenance=(
                "Form endpoint used by imdlib (get_data, 'new url dated Oct 10 2022'); "
                "not re-verified from this workstation because imdpune.gov.in was blocked by a local web filter."
            ),
            time_convention=_RAIN_TIME,
            notes=(
                "Binary: little-endian float32, per day 129 rows (lat, south->north) x 135 cols (lon, west->east).",
                "Coverage end year 2024 is what the indexed official page stated; later years are attempted and validated.",
                "Gauge-based; station density varies over time, so early decades are less reliable.",
            ),
        ),
        DatasetInfo(
            id="IMD_RF025_NC",
            variable="rainfall",
            long_name="IMD daily gridded rainfall (0.25 x 0.25 deg), NetCDF distribution",
            units="mm",
            file_format="netcdf",
            grid=RAIN_GRID,
            missing_value=-999.0,
            first_year=1901,
            last_year_documented=2024,
            documented_page=f"{IMD_BASE_URL}/Rainfall_25_NetCDF.html",
            download_endpoint=f"{IMD_BASE_URL}/RF25.php",
            post_field="RF25",
            filename_template="RF25_ind{year}_rfp25.nc",
            citation=CITATION_RAIN,
            endpoint_provenance=(
                "Endpoint and field name come from a single third-party script (Data4WA); UNVERIFIED. "
                "Prefer IMD_RF025_GRD for automated download or import NetCDF files manually."
            ),
            time_convention=_RAIN_TIME,
            notes=(
                "Variable/coordinate names differ between years (RAINFALL/LATITUDE/LONGITUDE/TIME vs rf/lat/lon/time); "
                "the reader normalises both.",
            ),
        ),
        DatasetInfo(
            id="IMD_TMAX_1DEG_GRD",
            variable="tmax",
            long_name="IMD daily gridded maximum temperature (1 x 1 deg)",
            units="degC",
            file_format="imd_grd",
            grid=TEMP_GRID,
            missing_value=99.9,
            first_year=1951,
            last_year_documented=None,
            documented_page=f"{IMD_BASE_URL}/Max_1_Bin.html",
            download_endpoint=f"{IMD_BASE_URL}/maxtemp.php",
            post_field="maxtemp",
            filename_template="Maxtemp_MaxT_{year}.GRD",
            citation=CITATION_TEMP,
            endpoint_provenance="Form endpoint used by imdlib; not re-verified from this workstation (web filter).",
            time_convention=_TEMP_TIME,
            notes=(
                "Binary: little-endian float32, per day 31 x 31 (lat south->north, lon west->east).",
                "Current end year could not be confirmed; requested years are attempted and validated.",
                "From 2008 the analysis uses ~180 real-time stations (fewer than earlier years).",
            ),
        ),
        DatasetInfo(
            id="IMD_TMIN_1DEG_GRD",
            variable="tmin",
            long_name="IMD daily gridded minimum temperature (1 x 1 deg)",
            units="degC",
            file_format="imd_grd",
            grid=TEMP_GRID,
            missing_value=99.9,
            first_year=1951,
            last_year_documented=None,
            documented_page=f"{IMD_BASE_URL}/Min_1_Bin.html",
            download_endpoint=f"{IMD_BASE_URL}/mintemp.php",
            post_field="mintemp",
            filename_template="Mintemp_MinT_{year}.GRD",
            citation=CITATION_TEMP,
            endpoint_provenance="Form endpoint used by imdlib; not re-verified from this workstation (web filter).",
            time_convention=_TEMP_TIME,
            notes=(
                "Binary: little-endian float32, per day 31 x 31 (lat south->north, lon west->east).",
                "Current end year could not be confirmed; requested years are attempted and validated.",
                "From 2008 the analysis uses ~180 real-time stations (fewer than earlier years).",
            ),
        ),
    ]
}

DEFAULT_DATASET = {"rainfall": "IMD_RF025_GRD", "tmax": "IMD_TMAX_1DEG_GRD", "tmin": "IMD_TMIN_1DEG_GRD"}

VARIABLE_ALIASES = {
    "rainfall": "rainfall", "rain": "rainfall", "precipitation": "rainfall", "rf": "rainfall",
    "tmax": "tmax", "maximum temperature": "tmax", "max temperature": "tmax", "maxtemp": "tmax",
    "tmin": "tmin", "minimum temperature": "tmin", "min temperature": "tmin", "mintemp": "tmin",
}


def canonical_variable(name: str) -> str:
    key = re.sub(r"[_\-]+", " ", name.strip().lower())
    if key not in VARIABLE_ALIASES:
        raise ValueError(
            f"Unknown variable '{name}'. Supported IMD gridded variables: rainfall, tmax, tmin."
        )
    return VARIABLE_ALIASES[key]


def resolve_dataset(variable: str, source: str = "IMD", overrides: dict | None = None) -> DatasetInfo:
    var = canonical_variable(variable)
    if source.upper() != "IMD" and source not in DATASETS:
        raise ValueError(f"Only official IMD datasets are supported; got source='{source}'.")
    ds_id = (overrides or {}).get(var) or (source if source in DATASETS else DEFAULT_DATASET[var])
    if ds_id not in DATASETS:
        raise ValueError(f"Unknown dataset id '{ds_id}'. Known: {', '.join(DATASETS)}")
    ds = DATASETS[ds_id]
    if ds.variable != var:
        raise ValueError(f"Dataset {ds_id} provides '{ds.variable}', not '{var}'.")
    return ds


def check_availability(ds: DatasetInfo, start: date, end: date) -> dict:
    """Compare requested years against what the source documents (no network)."""
    years = list(range(start.year, end.year + 1))
    before = [y for y in years if y < ds.first_year]
    after_doc = [y for y in years if ds.last_year_documented and y > ds.last_year_documented]
    in_future = [y for y in years if y > date.today().year]
    return {
        "dataset_id": ds.id,
        "requested_years": years,
        "documented_first_year": ds.first_year,
        "documented_last_year": ds.last_year_documented,
        "years_before_dataset_start": before,
        "years_after_documented_end": after_doc,
        "years_in_future": in_future,
        "attemptable_years": [y for y in years if y >= ds.first_year and y not in in_future],
    }


def diagnose_connection_error(exc: Exception, url: str, timeout: float = 20) -> str:
    """Turn a network exception into a factual, actionable reason.

    WRONG_VERSION_NUMBER on an https URL means the peer answered the TLS
    handshake with non-TLS bytes, which is what transparent web filters do. We
    then look at the plain-HTTP answer only to *name* the interceptor; nothing
    from that response is ever treated as data.
    """
    text = repr(exc)
    if "WRONG_VERSION_NUMBER" in text or "BadStatusLine" in text:
        title = None
        m = re.search(r"<title>(.*?)</title>", text, re.I | re.S)
        if m:
            title = m.group(1).strip()
        elif url.startswith("https://"):
            try:
                requests.get("http://" + url[len("https://"):], timeout=timeout)
            except Exception as inner:  # noqa: BLE001 - only used for diagnosis
                m = re.search(r"<title>(.*?)</title>", repr(inner), re.I | re.S)
                title = m.group(1).strip() if m else None
        who = f" by '{title}'" if title else ""
        return (
            f"Connection to {url} is being intercepted{who} (non-TLS reply to HTTPS handshake). "
            "This is a local network/web-filter block, not an IMD outage. Ask your IT administrator to "
            "allow imdpune.gov.in, or download the yearly files from the official page on an unrestricted "
            "network and place them in `manual_import_dir`."
        )
    if isinstance(exc, requests.Timeout):
        return f"Timed out contacting {url}. The IMD server may be slow or unreachable; retry later or import files manually."
    if isinstance(exc, requests.ConnectionError):
        return f"Could not connect to {url}: {exc}. Check connectivity/DNS, or import files manually."
    return f"{type(exc).__name__}: {exc}"


def probe_source(ds: DatasetInfo, session: requests.Session | None = None, timeout: float = 30) -> dict:
    """GET the dataset's documented page and report whether it is reachable."""
    session = session or requests.Session()
    url = ds.documented_page
    try:
        r = session.get(url, timeout=timeout)
        ok = r.status_code == 200 and "seqrite" not in r.text.lower()
        return {"url": url, "reachable": ok, "status_code": r.status_code,
                "reason": "ok" if ok else f"HTTP {r.status_code}"}
    except requests.RequestException as exc:
        return {"url": url, "reachable": False, "status_code": None,
                "reason": diagnose_connection_error(exc, url)}


def describe(ds: DatasetInfo) -> dict:
    g = ds.grid
    return {
        "dataset_id": ds.id,
        "provider": "India Meteorological Department (IMD), Climate Research & Services, Pune",
        "variable": ds.variable,
        "long_name": ds.long_name,
        "units": ds.units,
        "temporal_frequency": ds.temporal_frequency,
        "time_convention": ds.time_convention,
        "first_year": ds.first_year,
        "last_year_documented": ds.last_year_documented,
        "resolution_deg": g.res,
        "grid": {"nlon": g.nlon, "nlat": g.nlat, "lon_first": g.lon0, "lat_first": g.lat0,
                 "lon_last": float(g.lons[-1]), "lat_last": float(g.lats[-1])},
        "crs": f"{ds.crs} (regular lat/lon; IMD does not state a datum, WGS84 assumed)",
        "file_format": ds.file_format,
        "missing_value": ds.missing_value,
        "documented_page": ds.documented_page,
        "download_endpoint": ds.download_endpoint,
        "endpoint_provenance": ds.endpoint_provenance,
        "bulk_download": "one HTTP POST per year (no bulk/multi-year archive endpoint known)",
        "access": ACCESS_NOTE,
        "citation": ds.citation,
        "notes": list(ds.notes),
    }
