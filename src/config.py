"""Load and validate the pipeline configuration (YAML + CLI overrides)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import yaml

from .source_discovery import canonical_variable

OUTPUT_FORMATS = {"netcdf": "NetCDF", "geotiff": "GeoTIFF", "csv": "CSV", "geopackage": "GeoPackage", "gpkg": "GeoPackage"}
CELL_SELECTION = {"centre", "intersect"}
ACQUISITION_MODES = {"auto", "download", "manual"}


class ConfigError(ValueError):
    pass


@dataclass
class BoundaryConfig:
    path: str
    layer: str | None = None
    state_field: str | None = None
    district_field: str | None = None
    subdistrict_field: str | None = None
    source_note: str = ""


@dataclass
class PipelineConfig:
    state: str
    start_date: date
    end_date: date
    variables: list[str]
    boundary: BoundaryConfig
    districts: list[str] = field(default_factory=list)
    subdistricts: list[str] = field(default_factory=list)
    source: str = "IMD"
    datasets: dict[str, str] = field(default_factory=dict)
    output_dir: Path = Path("./data")
    output_format: list[str] = field(default_factory=lambda: ["NetCDF", "CSV"])
    resume_download: bool = True
    validate_data: bool = True
    acquisition_mode: str = "auto"
    manual_import_dir: Path | None = None
    cell_selection: str = "centre"
    mean_min_valid_fraction: float = 1.0
    timeout_seconds: float = 120.0
    max_retries: int = 3
    reference_comparison: dict | None = None

    @property
    def raw_dir(self) -> Path:
        return self.output_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.output_dir / "processed"

    @property
    def exports_dir(self) -> Path:
        return self.output_dir / "exports"

    @property
    def reports_dir(self) -> Path:
        return self.output_dir / "reports"

    @property
    def logs_dir(self) -> Path:
        return self.output_dir / "logs"

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, (date, Path)):
                d[k] = str(v)
        return d

    def fingerprint(self) -> str:
        """Stable hash of everything that affects outputs (not paths)."""
        d = self.to_dict()
        for k in ("output_dir", "manual_import_dir", "resume_download", "timeout_seconds", "max_retries"):
            d.pop(k, None)
        return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _parse_date(value, name: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ConfigError(f"{name} must be an ISO date YYYY-MM-DD, got {value!r}") from exc


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def build_config(raw: dict, base_dir: Path | None = None) -> PipelineConfig:
    base_dir = base_dir or Path.cwd()
    missing = [k for k in ("state", "start_date", "end_date", "variables", "boundary_source") if not raw.get(k)]
    if missing:
        raise ConfigError(f"Missing required config keys: {', '.join(missing)}")

    start = _parse_date(raw["start_date"], "start_date")
    end = _parse_date(raw["end_date"], "end_date")
    if end < start:
        raise ConfigError(f"end_date {end} is before start_date {start}")
    if start.year < 1901:
        raise ConfigError("IMD gridded data begin in 1901 (rainfall) / 1951 (temperature).")

    try:
        variables = list(dict.fromkeys(canonical_variable(v) for v in _as_list(raw["variables"])))
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc

    fmts = []
    for f in _as_list(raw.get("output_format", ["NetCDF", "CSV"])):
        key = str(f).strip().lower()
        if key not in OUTPUT_FORMATS:
            raise ConfigError(f"Unsupported output_format '{f}'. Choose from NetCDF, GeoTIFF, CSV, GeoPackage.")
        fmts.append(OUTPUT_FORMATS[key])

    b = raw["boundary_source"]
    if isinstance(b, str):
        b = {"path": b}
    if not b.get("path"):
        raise ConfigError("boundary_source.path is required (GeoJSON, Shapefile or GeoPackage).")
    bpath = Path(b["path"])
    if not bpath.is_absolute():
        bpath = base_dir / bpath
    boundary = BoundaryConfig(
        path=str(bpath), layer=b.get("layer"), state_field=b.get("state_field"),
        district_field=b.get("district_field"), subdistrict_field=b.get("subdistrict_field"),
        source_note=b.get("source_note", ""),
    )

    mode = str(raw.get("acquisition_mode", "auto")).lower()
    if mode not in ACQUISITION_MODES:
        raise ConfigError(f"acquisition_mode must be one of {sorted(ACQUISITION_MODES)}")
    sel = str(raw.get("cell_selection", "centre")).lower().replace("center", "centre")
    if sel not in CELL_SELECTION:
        raise ConfigError(f"cell_selection must be one of {sorted(CELL_SELECTION)}")
    frac = float(raw.get("mean_min_valid_fraction", 1.0))
    if not 0 < frac <= 1:
        raise ConfigError("mean_min_valid_fraction must be in (0, 1]")

    def _path(v):
        if v is None:
            return None
        p = Path(v)
        return p if p.is_absolute() else base_dir / p

    datasets = {canonical_variable(k): v for k, v in (raw.get("datasets") or {}).items()}

    return PipelineConfig(
        state=str(raw["state"]).strip(),
        start_date=start,
        end_date=end,
        variables=variables,
        boundary=boundary,
        districts=[str(x) for x in _as_list(raw.get("districts"))],
        subdistricts=[str(x) for x in _as_list(raw.get("subdistricts"))],
        source=str(raw.get("source", "IMD")),
        datasets=datasets,
        output_dir=_path(raw.get("output_dir", "./data")),
        output_format=list(dict.fromkeys(fmts)),
        resume_download=bool(raw.get("resume_download", True)),
        validate_data=bool(raw.get("validate_data", True)),
        acquisition_mode=mode,
        manual_import_dir=_path(raw.get("manual_import_dir")),
        cell_selection=sel,
        mean_min_valid_fraction=frac,
        timeout_seconds=float(raw.get("timeout_seconds", 120)),
        max_retries=int(raw.get("max_retries", 3)),
        reference_comparison=raw.get("reference_comparison"),
    )


def load_config(path: str | Path, overrides: dict | None = None) -> PipelineConfig:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for k, v in (overrides or {}).items():
        if v is not None:
            raw[k] = v
    # Relative paths in the YAML are resolved against the project root (config/..).
    return build_config(raw, base_dir=path.resolve().parent.parent)
