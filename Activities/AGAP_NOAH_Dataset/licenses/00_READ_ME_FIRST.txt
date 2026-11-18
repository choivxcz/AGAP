DATA LICENSES AND ATTRIBUTION
Project AGAP NOAH Flood-Risk Dataset, Santa Cruz, Laguna (v2.7)
Prepared November 5, 2026. License terms were checked on the providers' websites on that date.

This folder holds the license of every outside data source used to build agap_noah_dataset.csv and
agap_noah_training.csv. This is a summary for the research team and the adviser. It is not legal advice.
The full license texts in this folder, and the providers' own websites, take precedence over this summary.


1. SOURCES, LICENSES AND FILES
-------------------------------

Source: Project NOAH flood hazard maps (UP NOAH Center), Laguna, 5-, 25- and 100-year rain
  Columns:    FloodHazard5yr, FloodHazard25yr, FloodHazard100yr
  License:    Open Data Commons Open Database License (ODbL) 1.0
  Files:      Project_NOAH_Open_Data_License.pdf, ODbL-1.0_Open_Database_License.txt
  Credit:     Project NOAH (UP NOAH Center) and its contributors, licensed under ODbL 1.0.
  Must do:    Credit Project NOAH. Any dataset built on it that is shared must be shared under ODbL.

Source: MGB Detailed Flood Susceptibility (Mines and Geosciences Bureau, Geospatial Data Inventory)
  Column:     MGBFloodSusceptibility (and through it, the flood hazard in RiskLevel)
  License:    No license stated; public government data
  File:       MGB_Flood_Susceptibility_Source.txt
  Credit:     Mines and Geosciences Bureau (MGB), Detailed Flood Susceptibility, MGB Geospatial Data Inventory.
  Must do:    Credit MGB. Confirm reuse with MGB before publishing the dataset.

Source: Copernicus DEM GLO-30 (European Union and ESA)
  Columns:    ElevationMeters, SlopeDegrees (slope derived by the researchers)
  License:    Licence for the use of the Copernicus WorldDEM-30 (free, worldwide)
  File:       Copernicus_WorldDEM-30_Licence.pdf
  Credit:     produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space
              GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved.
  Must do:    Show the credit above. When the data is shared, also include the sentence: "The organisations
              in charge of the Copernicus programme by law or by delegation do not incur any liability for
              any use of the Copernicus WorldDEM-30". Do not imply endorsement by Copernicus or Airbus.

Source: OpenStreetMap waterways (river, stream, canal, drain, ditch)
  Columns:    DistanceToWaterwayM, LocationType
  License:    ODbL 1.0
  Files:      OpenStreetMap_Copyright.txt, ODbL-1.0_Open_Database_License.txt
  Credit:     © OpenStreetMap contributors, available under the Open Database License
              (https://www.openstreetmap.org/copyright)
  Must do:    Credit OSM and state that the data is under ODbL. Share-alike applies, as for NOAH.

Source: ESA WorldCover 2021 v200
  Column:     LandCoverType
  License:    Creative Commons Attribution 4.0 International (CC BY 4.0)
  Files:      ESA_WorldCover_License.txt, CC-BY-4.0_Creative_Commons_Attribution.txt
  Credit:     © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by
              ESA WorldCover consortium. Cite Zanaga et al. (2022).

Source: JRC Global Surface Water v1.5 (1984-2024)
  Column:     HistoricalWaterOccurrencePct
  License:    Free of charge, without restriction of use (Copernicus Programme)
  File:       JRC_Global_Surface_Water_License.txt
  Credit:     Source: EC JRC/Google. Cite Pekel et al. (2016), Nature 540, 418-422.

Source: NASA POWER daily rainfall (PRECTOTCORR), 1991-2025, point 14.281 N, 121.398 E
  Columns:    Rainfall24hMm, Rainfall3DayMm, Rainfall7DayMm (and EventDate)
  License:    Free to use; NASA asks for a reference and acknowledgement
  File:       NASA_POWER_Referencing_Terms.txt
  Credit:     The data was obtained from National Aeronautics and Space Administration (NASA) Langley
              Research Center's Prediction Of Worldwide Energy Resources (POWER) project funded through the
              NASA Earth Science Division.
  Must do:    Give the service name, version and access date. NASA asks to be sent a copy or reference of
              publications that use the data (larc-power-project@mail.nasa.gov).

Source: Overture Maps buildings theme (Google Open Buildings and Microsoft Global ML Building Footprints)
  Columns:    Latitude, Longitude (reference columns only; not in agap_noah_training.csv)
  License:    Theme under ODbL 1.0; Microsoft footprints ODbL; Google Open Buildings CC BY 4.0
  File:       Overture_Buildings_Attribution.txt
  Credit:     Building footprints © Overture Maps Foundation; Microsoft Global ML Building Footprints (ODbL);
              Google Open Buildings (CC BY 4.0).

Source: GEODATA Systems Technologies, Inc., Philippine Barangay Boundary layer (ArcGIS service)
  Used for:   Barangay column, and the barangay areas behind FloodHazard5yr/25yr/100yr
  License:    PROPRIETARY. See section 3.
  File:       GEODATA_Barangay_Boundaries_License.txt
  Credit:     Barangay boundaries: GEODATA Systems Technologies, Inc.

Synthetic columns (no outside license): Age, HousingMaterial, HouseMembers, ChildrenCount, ElderCount,
PWDCount and RiskLevel were generated by the research team (AGAP v1.0 profiles and the documented
risk rule). They do not come from any outside dataset.


2. WHAT THIS MEANS FOR THE DATASET
-----------------------------------

a) Share-alike (ODbL). The dataset contains values derived from Project NOAH, OpenStreetMap and Overture,
   all under ODbL 1.0. If the dataset, or a database built from it, is shared publicly, it must be shared
   under ODbL 1.0, with the credits above. ODbL section 4.6 also requires offering the derived database
   itself, or a file of the changes and the method used to make it. The build scripts in the dataset
   folder (build_noah_dataset.py, verify_dataset.py) can serve this purpose.

b) The other open sources (Copernicus DEM, ESA WorldCover, JRC, NASA POWER) are compatible with this,
   as long as their credits and the Copernicus liability sentence are kept.

c) Outputs such as maps, reports and the system's predictions are "Produced Works" under ODbL. When they
   are shown publicly, ODbL section 4.3 requires a notice that the content came from the ODbL sources
   and is available under ODbL. Put the credits in section 4 below in the thesis and on the system's
   credits page.

d) ODbL defines "Publicly" as sharing with anyone other than yourselves (or people under your control).
   Giving the dataset to the adviser or the panel therefore counts. Include this licenses folder whenever
   the dataset is shared, so the ODbL notice and all credits travel with it.


3. WARNING: BARANGAY BOUNDARIES (GEODATA)
-----------------------------------------

The barangay boundaries come from an ArcGIS service whose terms are the GEODATA Systems Data License
Agreement. It is a proprietary license. It allows internal use and printed maps that credit GEODATA,
but it does not allow the data, or products derived from it, to be distributed in digital form to
third parties. It also conflicts with the ODbL share-alike requirement in section 2a.

In this dataset, the boundaries decide each household's barangay and the barangay areas used for the
three FloodHazard columns.

Recommendation:
  - For the thesis and internal review: generally accepted for academic work, with the credit
    "Barangay boundaries: GEODATA Systems Technologies, Inc." Read strictly, though, the terms forbid
    giving derived products to anyone outside the licensee in digital form, so the safest path is the
    replacement below.
  - Before publishing the dataset or deploying the system publicly: replace the boundaries with an
    openly licensed set (for example PSA or NAMRIA boundaries, after confirming their license), then
    rebuild the dataset. The NOAH shares and FloodHazard classes would need to be recomputed.


4. CREDITS TO COPY INTO THE THESIS AND THE SYSTEM
--------------------------------------------------

Flood hazard maps: Project NOAH (UP NOAH Center) and its contributors, licensed under the Open Data
Commons Open Database License (ODbL) 1.0.
Elevation: Produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH
2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved. The organisations
in charge of the Copernicus programme by law or by delegation do not incur any liability for any use of
the Copernicus WorldDEM-30.
Waterways: © OpenStreetMap contributors, available under the Open Database License
(https://www.openstreetmap.org/copyright).
Land cover: © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by
ESA WorldCover consortium.
Surface water: Source: EC JRC/Google.
Rainfall: The data was obtained from National Aeronautics and Space Administration (NASA) Langley Research
Center's Prediction Of Worldwide Energy Resources (POWER) project funded through the NASA Earth Science
Division.
Building footprints: © Overture Maps Foundation; Microsoft Global ML Building Footprints (ODbL); Google
Open Buildings (CC BY 4.0).
Flood susceptibility: Mines and Geosciences Bureau (MGB), Detailed Flood Susceptibility, MGB Geospatial Data
Inventory.
Barangay boundaries: GEODATA Systems Technologies, Inc.
