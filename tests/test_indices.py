"""Tests for spectral index computation on synthetic band arrays."""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import box

from sentinel2.indices import IndexComputationConfig, SpectralIndexComputer


@pytest.fixture
def computer(tmp_path: Path) -> SpectralIndexComputer:
    aoi_path = tmp_path / "aoi.gpkg"
    gpd.GeoDataFrame(
        {"geometry": [box(-3.8, 37.7, -3.7, 37.8)]}, crs="EPSG:4326"
    ).to_file(aoi_path, driver="GPKG")
    config = IndexComputationConfig(
        bands_root=tmp_path / "bands",
        output_root=tmp_path / "out",
        aoi_path=aoi_path,
    )
    return SpectralIndexComputer(config)


def _band(value: float) -> np.ndarray:
    return np.full((2, 2), value, dtype=np.float32)


def test_ndvi_matches_the_formula(computer: SpectralIndexComputer) -> None:
    # nir = 0.5, red = 0.25 -> NDVI = (0.5 - 0.25) / (0.5 + 0.25) = 1/3
    b04, b8a = _band(2500), _band(5000)
    others = _band(3000)
    mask = np.zeros((2, 2), dtype=bool)
    indices = computer._compute_indices(others, others, b04, others, b8a, others, mask)
    assert np.allclose(indices["NDVI"], 1 / 3, atol=1e-4)


def test_indices_are_all_float32(computer: SpectralIndexComputer) -> None:
    band = _band(4000)
    mask = np.zeros((2, 2), dtype=bool)
    indices = computer._compute_indices(band, band, band, band, band, band, mask)
    assert set(indices) == {"NDVI", "SAVI", "EVI", "NBR", "NDRE", "NDWI"}
    assert all(a.dtype == np.float32 for a in indices.values())


def test_nodata_mask_propagates(computer: SpectralIndexComputer) -> None:
    band = _band(4000)
    mask = np.array([[True, False], [False, False]])
    indices = computer._compute_indices(band, band, band, band, band, band, mask)
    assert indices["NDVI"][0, 0] == computer._config.nodata_value


def test_mask_nodata_flags_non_positive_pixels(
    computer: SpectralIndexComputer,
) -> None:
    good = _band(4000)
    bad = good.copy()
    bad[0, 0] = 0.0
    invalid = computer._mask_nodata(good, bad)
    assert invalid[0, 0]
    assert not invalid[1, 1]
