"""Command-line entry point for the IMD state extraction pipeline.

Examples:
    python main.py --config config/config.yaml
    python main.py --config config/config.yaml --state "Maharashtra" --districts Nagpur Amravati
    python main.py --list-sources
    python main.py --probe
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.config import ConfigError, load_config
from src.source_discovery import DATASETS, describe, probe_source


def _abs(p):
    return str(Path(p).resolve()) if p else None


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Download/import, clip and export official IMD gridded data for an Indian state.")
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--state")
    ap.add_argument("--districts", nargs="*")
    ap.add_argument("--subdistricts", nargs="*")
    ap.add_argument("--start-date")
    ap.add_argument("--end-date")
    ap.add_argument("--variables", nargs="+", help="rainfall tmax tmin")
    ap.add_argument("--output-dir")
    ap.add_argument("--output-format", nargs="+", help="NetCDF GeoTIFF CSV GeoPackage")
    ap.add_argument("--boundary", help="boundary file path (GeoJSON/Shapefile/GeoPackage)")
    ap.add_argument("--acquisition-mode", choices=["auto", "download", "manual"])
    ap.add_argument("--manual-import-dir")
    ap.add_argument("--cell-selection", choices=["centre", "intersect"])
    ap.add_argument("--no-resume", action="store_true", help="re-fetch years even if already downloaded")
    ap.add_argument("--no-validate", action="store_true")
    ap.add_argument("--list-sources", action="store_true", help="print the IMD dataset registry and exit")
    ap.add_argument("--probe", action="store_true", help="check whether the IMD pages are reachable and exit")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    if a.list_sources:
        print(json.dumps([describe(d) for d in DATASETS.values()], indent=2))
        return 0
    if a.probe:
        res = [probe_source(d) for d in DATASETS.values()]
        print(json.dumps(res, indent=2))
        return 0 if all(r["reachable"] for r in res) else 2

    overrides = {
        "state": a.state, "start_date": a.start_date, "end_date": a.end_date, "variables": a.variables,
        "output_dir": _abs(a.output_dir), "output_format": a.output_format,
        "acquisition_mode": a.acquisition_mode, "manual_import_dir": _abs(a.manual_import_dir),
        "cell_selection": a.cell_selection,
        "resume_download": False if a.no_resume else None,
        "validate_data": False if a.no_validate else None,
    }
    if a.districts is not None:
        overrides["districts"] = a.districts
    if a.subdistricts is not None:
        overrides["subdistricts"] = a.subdistricts
    try:
        cfg = load_config(a.config, overrides)
        if a.boundary:
            cfg.boundary.path = _abs(a.boundary)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    from src.pipeline import run  # heavy imports only when running

    try:
        report = run(cfg)
    except ValueError as exc:  # boundary/selection errors carry actionable messages
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    statuses = {v: r.get("status") for v, r in report["variables"].items()}
    print("\nVariables:", statuses)
    print("Validation:", report["validation_summary"])
    print("Reports:", *report["report_files"], sep="\n  ")
    if report["warnings"]:
        print("Warnings:", *report["warnings"], sep="\n  - ")
    ok = all(s == "processed" for s in statuses.values()) and report["validation_summary"]["all_passed"] is not False
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
