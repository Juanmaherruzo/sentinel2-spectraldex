# sentinel2-cdse-pipeline

End-to-end Jupyter pipeline for searching, downloading, and computing spectral indices from **Sentinel-2 Level-2A** imagery via the [Copernicus Data Space Ecosystem (CDSE)](https://dataspace.copernicus.eu/) free API.

## Key features

- **No subscription required** — uses the free CDSE OData HTTP API with OAuth2 authentication
- **Band-selective download** — fetches only the 7 bands you need, not the full ~1 GB SAFE archive
- **Multi-tile support** — automatically merges MGRS tiles when your AOI spans tile boundaries
- **Six spectral indices**: NDVI, SAVI (configurable L factor), EVI, NBR, NDRE, NDWI
- **Validation dashboard** — per-index spatial map + histogram + statistics for every processed scene
- **AOI-driven workflow** — load any polygon from a `.gpkg` file; everything else is automated

## Pipeline overview

| Step | Cell | Description |
|------|------|-------------|
| 0 | `cell 0` | CDSE OAuth2 authentication + HTTP band download helper |
| 1 | `cell 1` | Load AOI from `.gpkg`, reproject to WGS84, interactive map preview |
| 2 | `cell 2` | Search Sentinel-2 L2A scenes (date window, cloud cover, AOI intersection) + deduplication |
| 3 | `cell 3` | Download bands B02, B03, B04, B05, B8A, B11, B12 via HTTP streaming |
| 4 | `cell 4` | Compute indices, merge tiles, clip to AOI → LZW-compressed float32 GeoTIFF |
| 5 | `cell 5` | Validation dashboard: spatial maps + histograms + per-index statistics |

## Requirements

```bash
pip install -r requirements.txt
```

## Credentials

Register for a free account at [dataspace.copernicus.eu](https://dataspace.copernicus.eu/).

**Option A — environment variables (recommended):**

```bash
# Linux / macOS
export CDSE_USER="your_email@example.com"
export CDSE_PASSWORD="your_password"

# Windows (PowerShell)
$env:CDSE_USER = "your_email@example.com"
$env:CDSE_PASSWORD = "your_password"
```

**Option B — `.env` file:**

```bash
cp .env.example .env
# edit .env with your credentials
```

`.env` is listed in `.gitignore` and will never be committed.

## Customisation

Search for `# Change it` comments — those are the only lines you need to edit:

| Step | What to change |
|------|----------------|
| Step 0 | CDSE credentials (or set env vars) |
| Step 1 | Path to your AOI `.gpkg` file |
| Step 2 | Date window (`date_start`, `date_end`) and `max_cloud_cover` |
| Step 3 | Output directory for raw bands |
| Step 4 | Output directory for index GeoTIFFs, SAVI `L` factor |

## SAVI L factor guide

| L value | Fractional canopy cover |
|---------|-------------------------|
| 0.0 | Dense canopy (FCC > 90 %) |
| 0.25 | Moderately dense (FCC 60–90 %) |
| 0.5 | Intermediate (FCC 30–60 %) |
| 0.75 | Sparse (FCC 10–30 %) |
| 1.0 | Bare soil or very sparse (FCC < 10 %) |

## Output structure

```
sentinel2_data/
├── raw_bands/
│   └── 2024-06-15/
│       ├── T30SUH/
│       │   ├── B02.jp2
│       │   ├── B03.jp2
│       │   ├── B04.jp2
│       │   ├── B05.jp2
│       │   ├── B8A.jp2
│       │   ├── B11.jp2
│       │   └── B12.jp2
│       └── T30SVF/
│           └── ...
└── index_results/
    └── 2024-06-15/
        ├── NDVI.tif
        ├── SAVI.tif
        ├── EVI.tif
        ├── NBR.tif
        ├── NDRE.tif
        ├── NDWI.tif
        └── dashboard_2024-06-15.png
```

## Index reference

| Index | Formula | Primary use |
|-------|---------|-------------|
| NDVI | (B8A − B04) / (B8A + B04) | General vegetation greenness |
| SAVI | ((B8A − B04) / (B8A + B04 + L)) × (1 + L) | Vegetation with soil adjustment |
| EVI | 2.5 × (B8A−B04) / (B8A+6×B04−7.5×B02+1) | Vegetation, no canopy saturation |
| NBR | (B8A − B12) / (B8A + B12) | Burn severity, fire scars |
| NDRE | (B8A − B05) / (B8A + B05) | Canopy chlorophyll (dense canopy) |
| NDWI | (B03 − B8A) / (B03 + B8A) | Surface water and soil moisture |

> **Note on NDRE**: particularly useful in silvopastoral systems and closed-canopy forests where NDVI saturates — NDRE remains sensitive to chlorophyll content at high biomass levels.

## Notes

- CDSE rate-limits downloads. The pipeline retries on HTTP 429 with a 30-second backoff; keep `n_threads = 1` on large batches.
- OAuth2 tokens expire after 10 minutes. The pipeline refreshes the token before every HTTP request.
- Output GeoTIFFs are float32 LZW-compressed tiled rasters (256 × 256 blocks), ready for QGIS, GDAL, or rasterio.
