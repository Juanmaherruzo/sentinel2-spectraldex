# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-08-11

### Added
- `SpectralIndexComputer.compute_indices()` and `.mask_nodata()` as public entry
  points, so the index mathematics can be used and tested without a raster on
  disk. The tests previously reached into private helpers and were coupled to
  internals while covering no public surface.
- `tests/test_download.py`: the download orchestration, its input validation and
  its failure path were entirely untested despite holding the module's only
  broad exception handler. Nothing in the suite touches the network.
- Index tests for NBR, NDWI, nodata derivation and out-of-range clamping.

### Changed
- `STACCOGBandDownloader` records failed tiles in `failed_tiles` and logs an
  explicit warning naming them. Previously a tile that failed was logged and
  dropped, so indices could be computed from a partial download with no signal
  that coverage was incomplete.

## [0.1.0] - 2026-07-18

### Added
- Initial release: Sentinel-2 L2A spectral index pipeline over the Microsoft
  Planetary Computer STAC catalogue.
- `sentinel2` package: AOI loading, STAC search + deduplication, stackstac COG
  band download, and NDVI/SAVI/EVI/NBR/NDRE/NDWI computation with plotting.
- Console entry point `s2-indices` for the end-to-end pipeline.
- `pyproject.toml` packaging, CI pipeline (ruff, black, mypy, pytest) and tests.

### Changed
- Converted the single notebook into importable, typed modules.
- Made the STAC catalogue connection lazy (no network access on import).
