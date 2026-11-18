# Sta. Cruz, Laguna — barangay GIS download package

This package prepares **five GIS layers for all 26 barangays of Santa Cruz, Laguna**:

1. Elevation — Copernicus DEM GLO-30 (about 30 m).
2. Slope in degrees — derived from that DEM.
3. Land cover — ESA WorldCover 2021 (about 10 m).
4. Distance to river in metres — derived from OpenStreetMap mapped waterways, on the 30 m DEM grid.
5. Historical surface-water occurrence (%) — JRC Global Surface Water v1.5, 1984–2024 (about 30 m).

## Run it

1. Install 64-bit Python 3.10–3.13. Check **Add Python to PATH** during installation.
2. Double-click `run_download.bat`.
3. When it finishes, use the files in `output/barangays/<barangay-name>/` in QGIS, ArcGIS, or another GIS program.

The first run downloads roughly 110–150 MB of source files and produces 104 clipped rasters: elevation, slope, land cover, and historical water for every barangay. A municipal river network and each barangay's distance-to-river raster are also produced. All outputs use UTM Zone 51N (EPSG:32651), so values in the distance raster are metres.

## Important data notes

- The barangay boundaries are a publicly available GEODATA service. It reports **26** features for `CITYMUN = Santa Cruz`, `PROVINCE = Laguna`, but warns that its boundaries are not an official nationwide barangay boundary dataset. Verify them with the LGU/PSA before using results for legal, cadastral, or high-stakes decisions.
- Copernicus DEM is a **surface** model, so it can include vegetation/buildings. Slope is a standard 3×3 finite-difference derivative of that grid.
- ESA WorldCover is a 2021 land-cover classification; it is not an up-to-date land-use survey.
- JRC's occurrence layer is the percentage of valid Landsat observations classified as water over 1984–2024. It is historical-water evidence, not a flood-depth layer.
- Distance-to-river reflects mapped OSM `river`, `stream`, `canal`, `drain`, and `ditch` features. Check against local hydrography if completeness is critical.

## Sources and attribution

- Copernicus DEM GLO-30: https://registry.opendata.aws/copernicus-dem/
- ESA WorldCover 2021 v200: https://esa-worldcover.org/en/data-access
- JRC Global Surface Water v1.5: https://global-surface-water.appspot.com/download
- Barangay boundaries: https://services7.arcgis.com/poQdgvLD6DHnbpsT/ArcGIS/rest/services/Philippine_Administrative_Boundaries/FeatureServer/3
- River lines: © OpenStreetMap contributors, https://www.openstreetmap.org/copyright

The script preserves a `sources.json` file with the exact input URLs and creates `metadata.json` with the processing date, CRS, layer definitions, and barangay count.
