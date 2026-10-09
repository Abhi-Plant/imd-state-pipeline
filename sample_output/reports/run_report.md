# IMD extraction report: Synthetica

- Run: 2026-10-09T08:49:53+00:00 → 2026-10-09T08:49:58+00:00  
- Config fingerprint: `a631d7cd10a88e6f`  
- Region: state = Synthetica (80053.8 km²) from `C:\Work\.py files\IMD data\imd_state_pipeline\sample_data\boundaries\synthetica_FICTIONAL.geojson`  
- Requested period: 2019-01-01 to 2020-12-31  
- Cell selection: `centre`  

## Sources

| Variable | Dataset | Resolution | Units | Missing code | Documented page |
|---|---|---|---|---|---|
| rainfall | IMD_RF025_GRD | 0.25° | mm | -999.0 | https://www.imdpune.gov.in/cmpg/Griddata/Rainfall_25_Bin.html |
| tmax | IMD_TMAX_1DEG_GRD | 1.0° | degC | 99.9 | https://www.imdpune.gov.in/cmpg/Griddata/Max_1_Bin.html |
| tmin | IMD_TMIN_1DEG_GRD | 1.0° | degC | 99.9 | https://www.imdpune.gov.in/cmpg/Griddata/Min_1_Bin.html |

## Acquisition (origin of raw files)

- **IMD_RF025_GRD**: {'manual_import': 2}; newly imported manually this run: 2
- **IMD_TMAX_1DEG_GRD**: {'manual_import': 2}; newly imported manually this run: 2
- **IMD_TMIN_1DEG_GRD**: {'manual_import': 2}; newly imported manually this run: 2

## Variables

### rainfall (IMD_RF025_GRD) — processed
- Available: 2019-01-01 → 2020-12-31; missing requested days: 0
- Cells: 111 selected of 140 overlapping (native 0.25°, ~713.0 km² per cell)
- Missing cell-days: 112 (0.00138); range 0.0 – 135.645; values filled/modified: 0
- Regional series: overlap-area-weighted mean of native grid-cell values (equal-area CRS EPSG:6933); days set missing because a cell was missing: 2
- Raw files used: 2 (['manual_import']); problems: 0

### tmax (IMD_TMAX_1DEG_GRD) — processed
- Available: 2019-01-01 → 2020-12-31; missing requested days: 0
- Cells: 8 selected of 12 overlapping (native 1.0°, ~11393.1 km² per cell)
- Missing cell-days: 0 (0.0); range 5.0 – 44.3261; values filled/modified: 0
- Regional series: overlap-area-weighted mean of native grid-cell values (equal-area CRS EPSG:6933); days set missing because a cell was missing: 0
- Raw files used: 2 (['manual_import']); problems: 0

### tmin (IMD_TMIN_1DEG_GRD) — processed
- Available: 2019-01-01 → 2020-12-31; missing requested days: 0
- Cells: 8 selected of 12 overlapping (native 1.0°, ~11393.1 km² per cell)
- Missing cell-days: 0 (0.0); range 8.5482 – 30.832; values filled/modified: 0
- Regional series: overlap-area-weighted mean of native grid-cell values (equal-area CRS EPSG:6933); days set missing because a cell was missing: 0
- Raw files used: 2 (['manual_import']); problems: 0

## Validation

- Checks: 33, all passed: True, failed: []

## Warnings

- 1 cell-days have tmax < tmin (flagged, not changed).

## Files created

- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\processed\IMD_rainfall_0p25deg_synthetica_20190101-20201231_region_daily_areaweighted.csv`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\processed\IMD_rainfall_0p25deg_synthetica_20190101-20201231_region_monthly.csv`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\processed\IMD_rainfall_0p25deg_synthetica_20190101-20201231_region_seasonal.csv`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\processed\IMD_rainfall_0p25deg_synthetica_20190101-20201231_region_annual.csv`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\processed\IMD_0p25deg_synthetica_centre_cell_weights.csv`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\exports\IMD_rainfall_0p25deg_synthetica_20190101-20201231.nc`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\exports\geotiff\IMD_rainfall_0p25deg_synthetica_20190101-20201231_2019.tif`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\exports\geotiff\IMD_rainfall_0p25deg_synthetica_20190101-20201231_2020.tif`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\exports\IMD_rainfall_0p25deg_synthetica_20190101-20201231_cells_daily.csv`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\processed\IMD_tmax_1deg_synthetica_20190101-20201231_region_daily_areaweighted.csv`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\processed\IMD_tmax_1deg_synthetica_20190101-20201231_region_monthly.csv`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\processed\IMD_tmax_1deg_synthetica_20190101-20201231_region_seasonal.csv`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\processed\IMD_tmax_1deg_synthetica_20190101-20201231_region_annual.csv`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\processed\IMD_1deg_synthetica_centre_cell_weights.csv`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\exports\IMD_tmax_1deg_synthetica_20190101-20201231.nc`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\exports\geotiff\IMD_tmax_1deg_synthetica_20190101-20201231_2019.tif`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\exports\geotiff\IMD_tmax_1deg_synthetica_20190101-20201231_2020.tif`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\exports\IMD_tmax_1deg_synthetica_20190101-20201231_cells_daily.csv`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\processed\IMD_tmin_1deg_synthetica_20190101-20201231_region_daily_areaweighted.csv`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\processed\IMD_tmin_1deg_synthetica_20190101-20201231_region_monthly.csv`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\processed\IMD_tmin_1deg_synthetica_20190101-20201231_region_seasonal.csv`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\processed\IMD_tmin_1deg_synthetica_20190101-20201231_region_annual.csv`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\processed\IMD_1deg_synthetica_centre_cell_weights.csv`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\exports\IMD_tmin_1deg_synthetica_20190101-20201231.nc`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\exports\geotiff\IMD_tmin_1deg_synthetica_20190101-20201231_2019.tif`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\exports\geotiff\IMD_tmin_1deg_synthetica_20190101-20201231_2020.tif`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\exports\IMD_tmin_1deg_synthetica_20190101-20201231_cells_daily.csv`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\exports\IMD_0p25deg_synthetica_grid_cells.gpkg`
- `C:\Work\.py files\IMD data\imd_state_pipeline\sample_output\exports\IMD_1deg_synthetica_grid_cells.gpkg`

## Citation

- Pai D.S., Sridhar L., Rajeevan M., Sreejith O.P., Satbhai N.S., Mukhopadhyay B. (2014). Development of a new high spatial resolution (0.25 x 0.25 degree) long period (1901-2010) daily gridded rainfall data set over India and its comparison with existing data sets over the region. MAUSAM 65(1):1-18. doi:10.54302/mausam.v65i1.851
- Srivastava A.K., Rajeevan M., Kshirsagar S.R. (2009). Development of a high resolution daily gridded temperature data set (1969-2005) for the Indian region. Atmospheric Science Letters 10(4):249-254. doi:10.1002/asl.232
