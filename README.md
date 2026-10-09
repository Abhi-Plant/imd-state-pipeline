# IMD state pipeline

Downloads (or imports), clips, quality-checks and exports **official IMD gridded daily data** for an Indian state, or for chosen districts or sub-districts within it:

- rainfall at 0.25°
- maximum and minimum temperature at 1.0°

The first use is historical rainfall for citrus *Phytophthora* root rot and gummosis risk.

- Source facts, evidence and access terms: [docs/SOURCE_REPORT.md](docs/SOURCE_REPORT.md)
- Example output from a synthetic run: [sample_output/reports/run_report.md](sample_output/reports/run_report.md) and [validation_report.json](sample_output/reports/validation_report.json)

## What is tested vs. not

| Part | Status |
|---|---|
| Binary `.grd` reader (rain 0.25°, Tmax/Tmin 1°) | **Tested.** Identical to `imdlib`'s reader on the same files |
| IMD-style NetCDF reader (either dimension order, descending latitude, `RAINFALL`/`rf` names) | **Tested** on synthetic IMD-style files. Not tested on a real IMD NetCDF |
| Boundary loading, state/district/sub-district selection, geometry repair, CRS checks | **Tested** (fictional boundary) |
| Cell selection (centre/intersect), area fractions, area-weighted regional series | **Tested** (small hand-checkable grid plus synthetic state) |
| Date slicing, leap years, duplicate and missing days | **Tested** |
| QC, monthly/seasonal/annual summaries (incomplete periods left blank) | **Tested** |
| NetCDF / GeoTIFF / CSV / GeoPackage export and reload checks | **Tested** |
| Download logic: retries, `.part` files, interruption and restart, HTML-instead-of-data, upstream-changed conflict, web-filter detection | **Tested with a mocked HTTP session** |
| **Live download from imdpune.gov.in** | **Not tested.** This workstation's Seqrite web filter blocks the site. The endpoints come from `imdlib`'s source (see source report) |
| `IMD_RF025_NC` download endpoint (`RF25.php`) | **Unverified** (single third-party source). Use binary or manual import instead |
| Reference comparison (`reference_comparison`) | Implemented, but only a small code path. No test with a real reference series |

## Install

Python 3.12. The versions in `requirements.txt` are the ones tested.

```powershell
cd imd_state_pipeline
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

On Windows, if `pip` cannot build `rasterio` or `geopandas`, install them from conda-forge instead: `conda install -c conda-forge geopandas rasterio netcdf4`.

## Run

```powershell
# 1. Check whether IMD is reachable from your network (exit code 2 = not reachable; reason printed)
python main.py --probe

# 2. See the dataset registry (grids, periods, endpoints, citations)
python main.py --list-sources

# 3. Offline demo on synthetic data (fictional state "Synthetica")
python scripts/make_synthetic_data.py
python main.py --config config/sample_synthetic.yaml

# 4. Real run: put a boundary file in boundaries/, edit config/config.yaml, then
python main.py --config config/config.yaml

# CLI overrides (anything in the YAML can be set here)
python main.py --config config/config.yaml --state "Maharashtra" --districts Nagpur Amravati `
    --start-date 2001-01-01 --end-date 2024-12-31 --variables rainfall tmax tmin `
    --output-format NetCDF CSV GeoPackage --cell-selection intersect

# Tests (about 25 s)
python -m pytest -q
```

### If the IMD site is blocked or unreachable

1. On a network that can reach the site, open the official pages (`Rainfall_25_Bin.html`, `Max_1_Bin.html`, `Min_1_Bin.html`) and download the yearly files you need.
2. Put them, unrenamed, in `data/manual_downloads/` (the `manual_import_dir` setting).
3. Run with `acquisition_mode: manual` (no network) or `auto`.

Imported files are copied, never moved, then checked by size and content and recorded with `origin: manual_import`.

## Configuration (`config/config.yaml`)

| Key | Meaning |
|---|---|
| `state` | State name as written in your boundary file. Case and punctuation are ignored, and close matches are suggested |
| `districts`, `subdistricts` | Optional filters. Sub-district covers taluk, tehsil, block or village, whichever field you point to |
| `start_date`, `end_date` | ISO dates, inclusive |
| `variables` | `rainfall`, `tmax`, `tmin` |
| `source` / `datasets` | `IMD`. Optional per-variable dataset id override |
| `output_dir` | Root for `raw/`, `processed/`, `exports/`, `reports/`, `logs/` |
| `output_format` | Any of `NetCDF`, `GeoTIFF`, `CSV`, `GeoPackage` |
| `boundary_source` | `path` (GeoJSON/Shapefile/GeoPackage), optional `layer` and `*_field` names, `source_note` |
| `acquisition_mode` | `auto` (existing raw, then manual import, then download), `download`, or `manual` |
| `manual_import_dir` | Folder of files you downloaded yourself |
| `resume_download` | `true`: skip verified years. `false`: re-fetch every year. A changed upstream file is saved alongside as `*.upstream-<hash>` and the original is never overwritten |
| `validate_data` | Run the output checks and write `validation_report.json` |
| `cell_selection` | `centre` or `intersect` (see below) |
| `mean_min_valid_fraction` | Minimum share of valid days for temperature means (default 1.0). Rainfall totals always need every day |
| `reference_comparison` | Optional external CSV (`year,value`), reported separately |

You must supply the boundary file. The pipeline never invents administrative boundaries. Common sources:

- Survey of India
- Census / LGD-based layers
- DataMeet
- geoBoundaries

Record which one you used in `source_note`.

## Data flow

```
config.yaml / CLI
   │
   ▼
source_discovery ── dataset registry, availability check, reachability probe
   │
   ▼
acquire: raw/ files already present → importer (manual files) → downloader (POST per year)
   │        all go into raw/<dataset>/, read-only, SHA-256 in raw/manifest.json
   ▼
inspect_data ── checksum re-verified, file read → (time, lat, lon); −999 / 99.9 → NaN, counted
   │
boundary_processing ── read, check CRS, repair, select state/districts/sub-districts, dissolve
   │
   ▼
temporal_extraction ── combine years, slice dates, report missing/duplicate days and leap days
   │
spatial_extraction ── per-cell overlap in equal-area CRS → selection mask → crop to bbox;
   │                  area-weighted regional daily series (derived)
   ▼
quality_control ── missing, negative/implausible values, all-zero cells, Tmax<Tmin; summaries
   │
export ── NetCDF, GeoTIFF per year, long CSV, GeoPackage of grid cells
   │
validation ── extent vs boundary, daily axis, requested-vs-available, reload equality
   │
reports/ run_report.json + run_report.md + validation_report.json, logs/pipeline_*.log
```

## Spatial extraction: what the numbers mean

- **Native grid only.** Outputs keep IMD's cell centres and spacing: 0.25° (about 27 km) for rain, 1° (about 110 km) for temperature. Nothing is resampled or interpolated, and no value is filled.
- **`centre`** keeps cells whose centre lies inside the region.
- **`intersect`** keeps every cell that overlaps the region.
- In both cases, unselected cells inside the bounding box are NoData. Each cell's `cell_area_fraction_in_region` is stored in the NetCDF and the GeoPackage.
- **Regional series** (`processed/*_region_daily_areaweighted.csv`): each overlapping cell is weighted by its overlap area with the region, computed in EPSG:6933.
  - On any day where an overlapping cell is missing, the regional value is set missing; it is not re-weighted.
  - Cells with no data on any day (for example sea cells on a coastline) are excluded, and the excluded share is reported.
  - This is an average of grid cells, not a district, block or village measurement.
- **Small regions.** When the region is smaller than one cell, the run warns that values describe the whole cell. With `centre`, a small region may contain no cell centre at all; the run then stops and tells you to use `intersect`.

## Outputs

File names follow `IMD_<variable>_<resolution>_<region>_<YYYYMMDD-YYYYMMDD>`.

| Folder | File | Content |
|---|---|---|
| `raw/` | IMD's own file names | Unchanged source files plus `manifest.json` (origin, checksum, size, URL, time) |
| `processed/` | `*_region_daily_areaweighted.csv` | Regional daily series |
| `processed/` | `*_region_monthly.csv`, `*_seasonal.csv`, `*_annual.csv` | Summaries with `n_days`, `n_valid` and `calendar_days`. Totals are blank unless the period is complete |
| `processed/` | `*_cell_weights.csv` | Every overlapping cell: overlap km², fraction, selected flag |
| `exports/` | `.nc` | CF-1.8 file with CRS variable, `in_selection` and area-fraction layers, `_FillValue` −999, source citation |
| `exports/geotiff/` | `*_<year>.tif` | One per year. Band = day, band description = ISO date, EPSG:4326, NoData −999, units in tags |
| `exports/` | `*_cells_daily.csv` | `date, cell_id, lat, lon, variable, value, units, qc_flag, source`. Missing values are blank with `qc_flag=missing` |
| `exports/` | `*_grid_cells.gpkg` | Layer `grid_cells`: polygons with selection info and per-cell statistics. Layer `region_boundary` |
| `reports/` | `run_report.*`, `validation_report.json` | Sources, raw-file origins, requested vs available dates, QC, warnings, files created, software versions, config fingerprint |

A full-state, multi-decade daily CSV is large: Madhya Pradesh (about 308,000 km²) covers roughly 440 cells at 0.25°, so 24 years of rainfall is about 3.9 M rows. NetCDF is the compact format.

## Limitations

- **Live download is unverified from this machine** (local Seqrite web filter). The endpoints come from `imdlib` and may change, as they did in 2022. The downloader rejects anything that is not a correctly sized IMD file, including HTML pages, so a broken endpoint fails loudly instead of producing bad data.
- **Coverage:**
  - Rainfall 1901 to 2024 per the official page; later years are tried and reported missing if not published.
  - Temperature from 1951; the current end year is unconfirmed.
- **Resolution:** 0.25° rain and 1° temperature cells. At village or orchard scale these are cell values. Temperature cells (about 12,000 km²) are especially coarse for sub-district work.
- **Station density** varies over time; temperature after 2008 uses about 180 real-time stations. IMD's 0.25° rainfall has edge cells that report 0 mm on every day; the QC report counts them but leaves them unchanged.
- **Datum:** IMD does not state one; EPSG:4326 is assumed. At 0.25° this is negligible.
- **Rainfall date-label convention** (whether the 08:30 IST day is labelled by its start or end) is not confirmed; see the source report.
- **Not implemented:**
  - real-time or provisional IMD products
  - the merged GPM rainfall
  - 1° rainfall
  - automatic boundary download
  - HTTP byte-range resume (resume works per yearly file)

## Extending

**Another state, district or tehsil.** Change `state`, `districts` or `subdistricts`, and if needed `boundary_source`. No code changes are needed. Set `cell_selection: intersect` for small units.

**Another IMD variable or product** (for example the merged GPM rainfall, once its page and format are confirmed):

1. Add a `DatasetInfo` to `DATASETS` in [src/source_discovery.py](src/source_discovery.py) with:
   - `GridSpec`
   - missing value
   - units
   - first and last documented year
   - official page
   - endpoint and POST field (only if documented; otherwise leave it for manual import)
   - file-name template
   - citation
   - `endpoint_provenance`
2. If it introduces a new variable name, add aliases to `VARIABLE_ALIASES` and `DEFAULT_DATASET`, plausible limits to `PLAUSIBLE` in [src/quality_control.py](src/quality_control.py), and CF `standard_name`/`cell_methods` in [src/export.py](src/export.py).
3. If the format is new, add a reader in [src/inspect_data.py](src/inspect_data.py) that returns `(time, lat, lon)` with missing values as NaN, plus a content check in [src/raw_store.py](src/raw_store.py).
4. Add synthetic files to [src/synthetic.py](src/synthetic.py) and a test.

## Licence

The source code is released under the [MIT License](LICENSE).

The licence covers this software only. **IMD gridded data are not included and are not covered by it.** Data you download or import remain subject to India Meteorological Department's terms of use; acknowledge IMD and cite the dataset papers listed in [docs/SOURCE_REPORT.md](docs/SOURCE_REPORT.md). Administrative boundary files you supply remain under their own licences. The files in `sample_output/` are generated from synthetic data for a fictional region and contain no IMD data.
