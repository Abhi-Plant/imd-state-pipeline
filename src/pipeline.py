"""Orchestrates: discovery -> acquire -> inspect -> boundary -> extract -> QC -> export -> validate -> report."""

from __future__ import annotations

import json
import logging
import platform
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pandas as pd

from . import export as ex
from .boundary_processing import load_region
from .config import PipelineConfig
from .downloader import download_year, make_session
from .importer import import_manual_files
from .inspect_data import CorruptFile, read_raw
from .quality_control import qc_variable, summarise_series, tmax_tmin_consistency
from .raw_store import InvalidRawFile, Manifest, check_raw_content, make_read_only, raw_path, sha256, utcnow
from .source_discovery import check_availability, describe, resolve_dataset
from .spatial_extraction import area_weighted_series, cell_weights, region_bbox_subset, subset_to_region
from .temporal_extraction import combine_years, slice_time
from . import validation as val

log = logging.getLogger("imd_pipeline")

LIBS = ["numpy", "pandas", "xarray", "netCDF4", "geopandas", "shapely", "pyproj", "rasterio", "requests", "PyYAML"]


def _versions() -> dict:
    out = {"python": platform.python_version()}
    for lib in LIBS:
        try:
            out[lib] = version(lib)
        except PackageNotFoundError:
            out[lib] = None
    return out


def setup_logging(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"pipeline_{datetime.now():%Y%m%d_%H%M%S}.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        if getattr(h, "_imd_pipeline", False):
            root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    for h in (logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler()):
        h.setFormatter(fmt)
        h._imd_pipeline = True
        root.addHandler(h)
    return path


def acquire(cfg: PipelineConfig, datasets: dict, years: dict, manifest: Manifest, session=None) -> dict:
    """Make sure raw files exist for requested years. Returns per-dataset acquisition log."""
    acq = {ds.id: {"files": [], "imports": []} for ds in datasets.values()}

    # Files placed directly in raw/ without going through the pipeline: check and record, origin unknown.
    for ds in datasets.values():
        for y in years[ds.id]:
            p = raw_path(cfg.raw_dir, ds, y)
            if p.exists() and not manifest.get(p):
                try:
                    check_raw_content(ds, p, y)
                    make_read_only(p)
                    manifest.record(p, dataset_id=ds.id, variable=ds.variable, year=y, status="complete",
                                    origin="preexisting_unrecorded", sha256=sha256(p), size=p.stat().st_size,
                                    recorded_utc=utcnow())
                except InvalidRawFile as exc:
                    acq[ds.id]["files"].append({"year": y, "status": "invalid_preexisting", "reason": str(exc)})

    if cfg.acquisition_mode in ("auto", "manual") and cfg.manual_import_dir:
        res = import_manual_files(cfg.manual_import_dir, list(datasets.values()), years, cfg.raw_dir, manifest)
        for r in res:
            acq.get(r.get("dataset_id"), {"imports": []})["imports"].append(r)
        acq["_unrecognised_import_files"] = [r for r in res if r["status"] == "unrecognised"]

    if cfg.acquisition_mode in ("auto", "download"):
        session = session or make_session()
        for ds in datasets.values():
            blocked = None
            for y in years[ds.id]:
                p = raw_path(cfg.raw_dir, ds, y)
                if cfg.resume_download and manifest.verify(p)[0]:
                    acq[ds.id]["files"].append({"year": y, "status": "present",
                                                "origin": manifest.get(p).get("origin")})
                    continue
                if blocked:
                    acq[ds.id]["files"].append({"year": y, "status": "not_attempted", "reason": blocked})
                    continue
                r = download_year(ds, y, cfg.raw_dir, manifest, session=session, resume=cfg.resume_download,
                                  timeout=cfg.timeout_seconds, max_retries=cfg.max_retries)
                acq[ds.id]["files"].append(r)
                if r["status"] == "failed" and "intercepted" in r.get("reason", ""):
                    blocked = "skipped: source blocked on this network (see first failure)"
    else:
        for ds in datasets.values():
            for y in years[ds.id]:
                p = raw_path(cfg.raw_dir, ds, y)
                ok, why = manifest.verify(p)
                acq[ds.id]["files"].append({"year": y, "status": "present" if ok else "missing",
                                            "origin": (manifest.get(p) or {}).get("origin"),
                                            **({} if ok else {"reason": why})})
    return acq


def run(cfg: PipelineConfig, session=None) -> dict:
    started = utcnow()
    log_path = setup_logging(cfg.logs_dir)
    for d in (cfg.raw_dir, cfg.processed_dir, cfg.exports_dir, cfg.reports_dir):
        d.mkdir(parents=True, exist_ok=True)

    report: dict = {
        "run_started_utc": started,
        "config": cfg.to_dict(),
        "config_fingerprint": cfg.fingerprint(),
        "software": _versions(),
        "log_file": str(log_path),
        "warnings": [],
        "variables": {},
        "files_created": [],
    }
    datasets = {v: resolve_dataset(v, cfg.source, cfg.datasets) for v in cfg.variables}
    report["sources"] = {v: describe(ds) for v, ds in datasets.items()}

    region = load_region(cfg.boundary, cfg.state, cfg.districts, cfg.subdistricts)
    rsum = region.summary()
    report["region"] = rsum
    report["warnings"] += region.warnings

    avail = {ds.id: check_availability(ds, cfg.start_date, cfg.end_date) for ds in datasets.values()}
    report["availability_check"] = avail
    for a in avail.values():
        if a["years_before_dataset_start"]:
            report["warnings"].append(f"{a['dataset_id']}: years {a['years_before_dataset_start']} precede the dataset start.")
        if a["years_after_documented_end"]:
            report["warnings"].append(f"{a['dataset_id']}: years {a['years_after_documented_end']} are after the last "
                                      "documented year; attempted, and reported as missing if IMD has not published them.")

    manifest = Manifest(cfg.raw_dir)
    years = {ds_id: a["attemptable_years"] for ds_id, a in avail.items()}
    report["acquisition"] = acquire(cfg, datasets, years, manifest, session)

    processed: dict = {}
    validation: list[dict] = []
    weights_cache: dict = {}
    for var, ds in datasets.items():
        vrep: dict = {"dataset_id": ds.id, "files_used": [], "file_problems": []}
        report["variables"][var] = vrep
        parts = []
        for y in years[ds.id]:
            p = raw_path(cfg.raw_dir, ds, y)
            ok, why = manifest.verify(p)
            if not ok:
                if p.exists() or why.startswith("checksum"):
                    vrep["file_problems"].append({"year": y, "reason": why})
                continue
            try:
                da, info = read_raw(p, ds, y)
            except CorruptFile as exc:
                vrep["file_problems"].append({"year": y, "reason": str(exc)})
                continue
            entry = manifest.get(p)
            info["origin"] = entry.get("origin")
            info["sha256"] = entry.get("sha256")
            vrep["files_used"].append(info)
            parts.append(da)

        if not parts:
            vrep["status"] = "no_data"
            vrep["next_step"] = _next_step(report["acquisition"].get(ds.id, {}), ds)
            report["warnings"].append(f"{var}: no usable raw files for the requested period. {vrep['next_step']}")
            continue

        full = combine_years(parts)
        da, trep = slice_time(full, cfg.start_date, cfg.end_date)
        vrep["time"] = trep
        if trep["missing_days_count"]:
            report["warnings"].append(f"{var}: {trep['missing_days_count']} requested days have no data "
                                      f"({trep['missing_day_ranges'][:3]}...). Not filled.")

        key = ds.grid
        if key not in weights_cache:
            weights_cache[key] = cell_weights(region.geometry, ds.grid, cfg.cell_selection)
        weights = weights_cache[key]
        sub, mask = subset_to_region(da, weights, ds.grid)
        nsel = int(mask.sum())
        vrep["spatial"] = {"cell_selection": cfg.cell_selection, "n_selected_cells": nsel,
                           "n_overlapping_cells": int((weights["overlap_km2"] > 0).sum()),
                           "resolution_deg": ds.grid.res,
                           "approx_cell_area_km2": round(float(weights["cell_area_km2"].mean()), 1),
                           "lat_range": [float(sub["lat"].min()), float(sub["lat"].max())],
                           "lon_range": [float(sub["lon"].min()), float(sub["lon"].max())]}
        if region.area_km2 < weights["cell_area_km2"].mean():
            report["warnings"].append(
                f"{var}: region area ({region.area_km2:.0f} km2) is smaller than one {ds.grid.res} deg cell "
                f"(~{weights['cell_area_km2'].mean():.0f} km2). Values describe the whole cell, not the region.")

        vrep["qc"] = qc_variable(sub, var, mask)
        series, sinfo = area_weighted_series(region_bbox_subset(da, weights), weights)
        vrep["region_series"] = sinfo
        summaries = summarise_series(series, var, cfg.mean_min_valid_fraction)
        processed[var] = {"da": sub, "series": series}

        stem = ex.base_name(ds, region.slug, cfg.start_date, cfg.end_date)
        files = []
        # Always-written processed products.
        sp = cfg.processed_dir / f"{stem}_region_daily_areaweighted.csv"
        series.rename("value").to_frame().assign(variable=var, units=ds.units,
                                                 method="area_weighted_mean_of_native_grid_cells").to_csv(sp, float_format="%.3f")
        files.append(sp)
        for name, table in summaries.items():
            p = cfg.processed_dir / f"{stem}_region_{name}.csv"
            table.to_csv(p, index=False, float_format="%.3f")
            files.append(p)
        wp = cfg.processed_dir / f"IMD_{ds.resolution_label}_{region.slug}_{cfg.cell_selection}_cell_weights.csv"
        weights.drop(columns="geometry").to_csv(wp, index=False, float_format="%.6f")
        files.append(wp)

        checks = []
        if cfg.validate_data:
            checks += val.validate_spatial(weights, region.geometry, ds.grid.res, cfg.cell_selection)
            checks += val.validate_time(sub, trep)
        if "NetCDF" in cfg.output_format:
            p = ex.to_netcdf(sub, mask, weights, ds, cfg.exports_dir / f"{stem}.nc", cfg.cell_selection, rsum)
            files.append(p)
            if cfg.validate_data:
                checks += val.validate_netcdf(p, sub, var, ds.units)
        if "GeoTIFF" in cfg.output_format:
            tifs = ex.to_geotiffs(sub, ds, cfg.exports_dir / "geotiff", stem)
            files += tifs
            if cfg.validate_data:
                checks += val.validate_geotiffs(tifs, sub)
        if "CSV" in cfg.output_format:
            p, rows = ex.to_csv(sub, mask, ds, cfg.exports_dir / f"{stem}_cells_daily.csv")
            files.append(p)
            if cfg.validate_data:
                checks += val.validate_csv(p, nsel * sub.sizes["time"], rows)
        processed[var]["stats"] = ex.cell_period_stats(sub, mask, var)
        processed[var]["weights"] = weights
        if cfg.reference_comparison and var == "rainfall":
            vrep["reference_comparison"] = val.compare_reference(summaries["annual"], cfg.reference_comparison)

        for c in checks:
            c["variable"] = var
        validation += checks
        vrep["status"] = "processed"
        vrep["outputs"] = [str(f) for f in files]
        report["files_created"] += [str(f) for f in files]

    if "GeoPackage" in cfg.output_format and processed:
        by_grid: dict = {}
        for var, ds in datasets.items():
            if var in processed:
                by_grid.setdefault(ds.resolution_label, []).append(var)
        for res_label, vars_ in by_grid.items():
            stats = None
            for v in vars_:
                s = processed[v]["stats"]
                stats = s if stats is None else stats.merge(s, on="cell_id", how="outer")
            p = cfg.exports_dir / f"IMD_{res_label}_{region.slug}_grid_cells.gpkg"
            ex.to_geopackage(processed[vars_[0]]["weights"], region.features, stats, p)
            report["files_created"].append(str(p))

    if "tmax" in processed and "tmin" in processed:
        report["tmax_tmin_consistency"] = tmax_tmin_consistency(processed["tmax"]["da"], processed["tmin"]["da"])
        if report["tmax_tmin_consistency"]["n_tmax_below_tmin"]:
            report["warnings"].append(f"{report['tmax_tmin_consistency']['n_tmax_below_tmin']} cell-days have tmax < tmin (flagged, not changed).")

    report["run_finished_utc"] = utcnow()
    vreport = {"config_fingerprint": cfg.fingerprint(), "generated_utc": report["run_finished_utc"],
               "all_passed": all(c["passed"] for c in validation) if validation else None,
               "n_checks": len(validation), "checks": validation}
    report["validation_summary"] = {"all_passed": vreport["all_passed"], "n_checks": len(validation),
                                    "failed": [c["check"] for c in validation if not c["passed"]]}
    vp = cfg.reports_dir / "validation_report.json"
    vp.write_text(json.dumps(vreport, indent=2, default=str), encoding="utf-8")
    jp = cfg.reports_dir / "run_report.json"
    jp.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    mp = cfg.reports_dir / "run_report.md"
    mp.write_text(render_markdown(report), encoding="utf-8")
    report["report_files"] = [str(jp), str(mp), str(vp)]
    log.info("Reports written to %s", cfg.reports_dir)
    return report


def _next_step(acq: dict, ds) -> str:
    reasons = [f.get("reason", "") for f in acq.get("files", []) if f.get("reason")]
    if any("intercepted" in r for r in reasons):
        return next(r for r in reasons if "intercepted" in r)
    if any("HTML page" in r for r in reasons):
        return ("IMD returned an HTML page instead of data for the requested years (often: year not yet published). "
                f"Check {ds.documented_page} for the available years.")
    return (f"Download the yearly files from {ds.documented_page} and put them in manual_import_dir, "
            "or run with acquisition_mode: auto on a network that can reach imdpune.gov.in.")


def render_markdown(r: dict) -> str:
    cfg = r["config"]
    L = [f"# IMD extraction report: {r['region']['state']}", "",
         f"- Run: {r['run_started_utc']} → {r.get('run_finished_utc')}  ",
         f"- Config fingerprint: `{r['config_fingerprint']}`  ",
         f"- Region: {r['region']['level']} = {', '.join(r['region']['names'])} "
         f"({r['region']['area_km2']} km²) from `{r['region']['boundary_file']}`  ",
         f"- Requested period: {cfg['start_date']} to {cfg['end_date']}  ",
         f"- Cell selection: `{cfg['cell_selection']}`  ", ""]
    L += ["## Sources", "", "| Variable | Dataset | Resolution | Units | Missing code | Documented page |",
          "|---|---|---|---|---|---|"]
    for v, s in r["sources"].items():
        L.append(f"| {v} | {s['dataset_id']} | {s['resolution_deg']}° | {s['units']} | {s['missing_value']} | {s['documented_page']} |")
    L += ["", "## Acquisition (origin of raw files)", ""]
    for ds_id, a in r["acquisition"].items():
        if ds_id.startswith("_"):
            continue
        counts: dict = {}
        for f in a["files"]:
            k = f.get("origin") or f["status"]
            if f["status"] in ("failed", "unavailable_or_invalid", "missing", "not_attempted", "error"):
                k = f["status"]
            counts[k] = counts.get(k, 0) + 1
        imp = sum(1 for i in a["imports"] if i["status"] == "imported")
        L.append(f"- **{ds_id}**: {counts or 'no download step'}; newly imported manually this run: {imp}")
        for f in a["files"]:
            if f.get("reason"):
                L.append(f"  - {f.get('year')}: {f['status']} — {f['reason']}")
                break
    L += ["", "## Variables", ""]
    for v, vr in r["variables"].items():
        L.append(f"### {v} ({vr['dataset_id']}) — {vr.get('status')}")
        if vr.get("status") != "processed":
            L += [f"- Next step: {vr.get('next_step')}", ""]
            continue
        t, q, s, sp = vr["time"], vr["qc"], vr["region_series"], vr["spatial"]
        L += [f"- Available: {t['available_start']} → {t['available_end']}; missing requested days: {t['missing_days_count']}",
              f"- Cells: {sp['n_selected_cells']} selected of {sp['n_overlapping_cells']} overlapping "
              f"(native {sp['resolution_deg']}°, ~{sp['approx_cell_area_km2']} km² per cell)",
              f"- Missing cell-days: {q['n_missing_cell_days']} ({q['missing_fraction']}); "
              f"range {q['min']} – {q['max']}; values filled/modified: 0",
              f"- Regional series: {s['method']}; days set missing because a cell was missing: "
              f"{s['n_days_set_missing_due_to_missing_cells']}",
              f"- Raw files used: {len(vr['files_used'])} "
              f"({sorted(set(f['origin'] for f in vr['files_used']))}); problems: {len(vr['file_problems'])}", ""]
    L += ["## Validation", "", f"- Checks: {r['validation_summary']['n_checks']}, all passed: "
          f"{r['validation_summary']['all_passed']}, failed: {r['validation_summary']['failed']}", ""]
    if r["warnings"]:
        L += ["## Warnings", ""] + [f"- {w}" for w in r["warnings"]] + [""]
    L += ["## Files created", ""] + [f"- `{f}`" for f in r["files_created"]] + [""]
    L += ["## Citation", ""] + sorted({f"- {s['citation']}" for s in r["sources"].values()}) + [""]
    return "\n".join(L)
