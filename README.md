# sentinel2-spectraldex

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![License](https://img.shields.io/badge/License-MIT-green)
![Platform](https://img.shields.io/badge/Platform-Microsoft%20Planetary%20Computer-blue)

Automated Sentinel-2 L2A spectral index pipeline powered by **Microsoft Planetary Computer**. Given a polygon AOI and a date range, the pipeline delivers six analysis-ready GeoTIFFs — NDVI, SAVI, EVI, NBR, NDRE, NDWI — plus a per-scene validation dashboard, without requiring any credentials or data subscriptions.

![Dashboard example](assets/dashboard_example.png)

---

## Pipeline overview

| Cell | Step | Description |
|------|------|-------------|
| 0 | Setup | Connect to the Planetary Computer STAC catalogue |
| 1 | AOI | Load a GeoPackage, reproject to EPSG:4326, preview on an interactive map |
| 2 | Search | Query Sentinel-2 L2A scenes by date, cloud cover, and spatial extent |
| 3 | Download | Fetch only the required bands via COG HTTP range requests |
| 4 | Compute | Merge multi-tile scenes, clip to the AOI polygon, compute all six indices |
| 5 | Dashboard | Generate spatial maps and histograms for each processed scene |

### Why Planetary Computer?

- **No credentials required** — the public STAC API auto-signs COG download URLs via the `planetary_computer` library.
- **Bandwidth-efficient** — `stackstac` issues HTTP range requests against Cloud Optimised GeoTIFFs, transferring only the pixels that fall within your bounding box (~30–200 MB per scene instead of the full ~1 GB SAFE archive).
- **Multi-tile support** — scenes that span two or more MGRS tiles are automatically merged with `rasterio` before polygon clipping.

---

## Spectral indices

| Index | Formula | Application |
|-------|---------|-------------|
| NDVI | (B8A − B04) / (B8A + B04) | General vegetation greenness |
| SAVI | ((B8A − B04) / (B8A + B04 + L)) × (1 + L) | Vegetation over sparse or bare soils |
| EVI | 2.5 × (B8A − B04) / (B8A + 6·B04 − 7.5·B02 + 1) | Dense canopy without saturation |
| NBR | (B8A − B12) / (B8A + B12) | Burn severity and post-fire mapping |
| NDRE | (B8A − B05) / (B8A + B05) | Canopy chlorophyll in dense forests (red-edge) |
| NDWI | (B03 − B8A) / (B03 + B8A) | Surface water and soil moisture |

All bands are scaled from Sentinel-2 DN to surface reflectance (DN / 10 000) prior to index computation. Output values are clamped to physically meaningful ranges and pixels outside the AOI polygon are set to −9999.

---

## Installation

```bash
conda create -n sentinel2 python=3.11
conda activate sentinel2
pip install -r requirements.txt
```

---

## Usage

1. Open `sentinel2_pipeline.ipynb`.
2. **Cell 0** — if you have a PostGIS / PostgreSQL installation that overrides `PROJ_DATA`, uncomment the workaround block and set the correct path (see the comment in that cell).
3. **Cell 1** — set `GPKG_PATH` to your area of interest GeoPackage.
4. **Cell 2** — adjust `date_start`, `date_end`, and `max_cloud_cover`.
5. **Cell 4** — set `aoi_path` to the same GeoPackage and adjust `savi_L` if needed (`0.5` is a general-purpose default; use `0.1` for dense forest, `1.0` for bare soil).
6. Run all cells in order (Kernel → Run All).

---

## Output structure

```
output/
├── raw_bands/
│   └── YYYY-MM-DD/
│       └── T30SVF/
│           ├── B02.tif
│           ├── B03.tif
│           └── ...
└── indices/
    └── YYYY-MM-DD/
        ├── NDVI.tif
        ├── SAVI.tif
        ├── EVI.tif
        ├── NBR.tif
        ├── NDRE.tif
        ├── NDWI.tif
        └── dashboard_YYYY-MM-DD.png
```

All index rasters are **float32, LZW-compressed, tiled GeoTIFFs** ready for QGIS, GDAL, or any rasterio-based workflow. Nodata value is −9999.

---

## Requirements

See `requirements.txt`. Key dependencies:

| Package | Role |
|---------|------|
| `pystac-client` | STAC catalogue queries |
| `planetary-computer` | Planetary Computer URL signing |
| `stackstac` ≥ 0.5.1 | Lazy COG reading into xarray DataArrays |
| `rasterio` | GeoTIFF I/O, polygon masking, tile merging |
| `geopandas` | AOI loading, CRS management, geometry reprojection |
| `scipy` | Band resampling (10 m → 20 m via bilinear zoom) |
| `folium` | Interactive AOI preview map |
| `matplotlib` | Validation dashboard figures |

---

## Citation
If you use this work in your research, please cite:

@software{herruzo2026sentinel2-spectraldex,
author  = {Herruzo, Juan Manuel},
title   = {sentinel2-spectraldex},
year    = {2026},
url     = {[https://github.com/Juanmaherruzo/sentinel2-spectraldex](https://github.com/Juanmaherruzo/sentinel2-spectraldex)}
}

---

## Contact

**Juan Manuel Herruzo**  juanmherruzo@gmail.com

---

## License

Distributed under the [MIT License](LICENSE).

---
