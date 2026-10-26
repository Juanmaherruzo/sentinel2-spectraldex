# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.1] - 2026-10-27

### Fixed
- **Sentinel-2 processing baseline 04.00 offset.** Since 2022-01-25 L2A digital
  numbers carry an offset of +1000 (reflectance = (DN - 1000) / 10000), and the
  Planetary Computer serves them as ESA ships them. Bands were divided by 10000
  without removing it, so every index from scenes processed with baseline 04.00
  or later was wrong: ratio indices most, because the offset adds 0.1 to both
  terms of the denominator. On two 2026 scenes of tile 30SUH the scene-mean NDVI
  was 0.444 instead of 0.679 (March) and 0.180 instead of 0.263 (June); EVI moved
  by under 0.02, as its offsets nearly cancel. A tell-tale sign in the downloaded
  bands: B02 never fell below about 1000 DN.
  `STACCOGBandDownloader` now reads each band's `BOA_ADD_OFFSET` from the
  product metadata (`MTD_MSIL2A.xml`), as ESA recommends, falling back to the
  item's `s2:processing_baseline` and then to the acquisition date (both with a
  warning) if the metadata cannot be read. It removes the offset
  before writing, keeps 0 as nodata, floors valid pixels darker than the offset
  at 1 DN so they are not taken for nodata, and tags each GeoTIFF with
  `S2_BOA_ADD_OFFSET_APPLIED`. Band files written by earlier versions carry no
  tag and are rewritten instead of being reused from the cache. Indices
  computed by 0.2.0 or earlier from post-2022 scenes should be regenerated.
- `SpectralIndexComputer.compute_indices()` takes `boa_add_offset` for callers
  passing raw, unharmonised DN.
- `sentinel2.__version__` said 0.1.0 while the package was 0.2.0.

### Added
- `tests/test_harmonize.py`: offsets parsed from product metadata, offset from
  the baseline and from the date, nodata
  and dark-pixel handling, NDVI with and without the offset, and a stale untagged
  band being rewritten. Nothing touches the network.

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
