# IMD gridded data: source and access report

Investigated 2026-10-09. Provider for every dataset: **India Meteorological Department (IMD), Climate Research & Services (CRS), Pune**, "Gridded Data Archive" pages under `https://www.imdpune.gov.in/cmpg/Griddata/`.

## How each fact was established

| Evidence | What it gave | Status |
|---|---|---|
| Official IMD page `Rainfall_25_Bin.html` (as indexed by a search engine; live fetch blocked, see below) | 0.25° rainfall, **1901–2024**, 135 × 129 grid, first point 6.5N 66.5E, last 38.5N 100.0E, 365/366 records per year, mm | Official text, read from the search index, not from a live fetch |
| `imdlib` 0.1.22 source code (installed here) | Form endpoints `rainfall.php` / `maxtemp.php` / `mintemp.php`, POST field = `rain` / `maxtemp` / `mintemp`, value = year; file names; float32 layout; −999 rain missing code; temperature grid 31 × 31 from 7.5N 67.5E | Independent implementation; endpoints labelled "new url dated Oct 10 2022" |
| `imdR` R package (CRAN) | Same binaries downloaded from IMD Pune: rain 0.25° 1901–present, temperature 1.0° 1951–present; real-time temperature is a separate 0.5° product | Second independent implementation |
| Blog walkthrough quoting `Max_1_Bin.html` and an `ncdump` of IMD's NetCDF | Temperature missing value **99.9**, °C, 4-byte floats; NetCDF variable `RAINFALL(LONGITUDE, LATITUDE, TIME)`, `_FillValue −999`, units `mm`, `days since 1900-12-31`, Gregorian calendar | Secondary source |
| Data4WA script | NetCDF endpoint `RF25.php`, field `RF25`; variable names changed to `rf`/`time` for recent years | **Single third-party source, unverified** |
| Stanford / IRI catalogue entries for "IMD HRDGT" | 1° daily Tmax/Tmin/Tmean in GRD + ASCII; from 2008 based on ~180 real-time stations | Catalogue metadata |
| My own binary reader compared with `imdlib`'s reader on identical files | Byte order and lat/lon orientation agree (identical on 5.9 M rain and 328 k temperature cell-days) | Tested here |

### Live access from this workstation: blocked

Every request to `imdpune.gov.in` from this machine is intercepted by **Seqrite Endpoint Protection** (web-filter category "News", "Access to website is blocked. Please contact your administrator"). HTTPS handshakes get a plain-HTTP block page, which surfaces as `SSL: WRONG_VERSION_NUMBER`. The pipeline's `--probe` and downloader detect this and report it as is. I did **not** try to get around the filter. As a result, none of the endpoints were re-verified live on 2026-10-09.

## Datasets

| | Rainfall 0.25° (binary) | Rainfall 0.25° (NetCDF) | Tmax 1.0° | Tmin 1.0° |
|---|---|---|---|---|
| Pipeline id | `IMD_RF025_GRD` (default) | `IMD_RF025_NC` | `IMD_TMAX_1DEG_GRD` | `IMD_TMIN_1DEG_GRD` |
| Official page | `Rainfall_25_Bin.html` | `Rainfall_25_NetCDF.html` | `Max_1_Bin.html` | `Min_1_Bin.html` |
| Variable / units | daily rainfall, mm | daily rainfall, mm | daily max air temp, °C | daily min air temp, °C |
| Period | 1901 – 2024 (page text); later years may now exist | 1901 – (same data) | 1951 – (current end year not confirmed) | 1951 – (not confirmed) |
| Frequency | daily (365/366 per yearly file) | daily | daily | daily |
| Grid | 135 lon × 129 lat, 66.5–100.0E, 6.5–38.5N | same | 31 × 31, 67.5–97.5E, 7.5–37.5N | same |
| CRS | regular lat/lon; datum not stated by IMD; treated as WGS84 (EPSG:4326) | same | same | same |
| Format | headerless little-endian float32, per day: lat rows S→N × lon cols W→E | NetCDF-3 classic | as rain, 31 × 31 | as rain, 31 × 31 |
| Missing value | −999 | −999 (`_FillValue`) | 99.9 | 99.9 |
| Bulk download | one POST per year (no multi-year archive known) | one POST per year | one POST per year | one POST per year |
| Registration / payment | none observed for these pages | none observed | none observed | none observed |
| Download mechanism | form POST `rainfall.php`, `rain=<year>` | form POST `RF25.php`, `RF25=<year>` (**unverified**) | `maxtemp.php`, `maxtemp=<year>` | `mintemp.php`, `mintemp=<year>` |

Other IMD products noted but **not** implemented: the IMD–NCMRWF merged satellite–gauge (GPM) 0.25° rainfall (`Rainfall_25_NetCDF_Merged.html`); real-time 0.25° rainfall and 0.5° temperature (provisional, recent years only, on a different grid); the 1° rainfall product. Station data come through the **IMD Data Supply Portal** (`dsp.imdpune.gov.in`), which requires registration and payment (Bharatkosh, plus GST) and is outside this pipeline.

## Terms and citation

Use the data under IMD's terms on the download pages, and confirm the exact wording there. The usual requirement is to acknowledge IMD and cite the dataset papers:

- Rainfall: Pai D.S. et al. (2014) *MAUSAM* 65(1):1–18, doi:10.54302/mausam.v65i1.851.
- Temperature: Srivastava A.K., Rajeevan M., Kshirsagar S.R. (2009) *Atmospheric Science Letters* 10:249–254, doi:10.1002/asl.232.

## Scientific caveats that affect *Phytophthora* risk work

- **Rainfall date convention:** IMD's observational rainfall day is the 24 hours ending 08:30 IST, so each daily value spans two calendar days. I could not confirm whether the gridded files label that period by its start date or its end date. The pipeline keeps IMD's labels unchanged. Check the convention on the IMD page or in Pai et al. (2014) before lagging rainfall by a day against disease or soil-moisture observations.
- **Station density changes over time**, especially for temperature after 2008 (about 180 stations). Long-term trends in early decades or in sparse regions need care.
- **0.25° ≈ 27 km and 1° ≈ 110 km cells.** An orchard, village or tehsil gets the value of the cell it lies in. That is a grid-cell value, not a local measurement.

## Sources

- IMD Pune gridded rainfall page (search-index copy): https://imdpune.gov.in/cmpg/Griddata/Rainfall_25_Bin.html
- IMD merged GPM rainfall page: https://www.imdpune.gov.in/cmpg/Griddata/Rainfall_25_NetCDF_Merged.html
- imdlib documentation: https://imdlib.readthedocs.io/en/latest/Usage.html
- imdR reference manual: https://ftp.fau.de/cran/web/packages/imdR/refman/imdR.html
- Data4WA IMD rainfall notes: https://waterinag.github.io/Data4WA/datasets/pcp_imd/
- NetCDF/binary walkthrough: https://ankitdeshmukh.com/post/2024-10-03-working-with-netcdf/
- Pai et al. 2014 (MAUSAM): https://mausamjournal.imd.gov.in/index.php/MAUSAM/article/view/851
- IMD HRDGT catalogue record: https://purl.stanford.edu/qv323kc9789
- IRI Data Library IMD HRDGT: https://iridl.ldeo.columbia.edu/SOURCES/.IMD/.HRDGT/.v1951-2015_monthly
- IMD data supply procedure (example regional notice): https://mausam.imd.gov.in/chennai/mcdata/data_supply.pdf
