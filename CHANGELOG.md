# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
