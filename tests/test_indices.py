"""Tests for spectral index computation on synthetic band arrays.

These exercise the public ``compute_indices`` / ``mask_nodata`` entry points
rather than private helpers, so the suite pins observable behaviour and survives
internal refactoring.
"""

from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import box

from sentinel2.indices import IndexComputationConfig, SpectralIndexComputer

_ALL_INDICES = {"NDVI", "SAVI", "EVI", "NBR", "NDRE", "NDWI"}


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
    indices = computer.compute_indices(others, others, b04, others, b8a, others)
    assert np.allclose(indices["NDVI"], 1 / 3, atol=1e-4)


def test_nbr_matches_the_formula(computer: SpectralIndexComputer) -> None:
    # nir = 0.4, swir2 = 0.1 -> NBR = 0.3 / 0.5 = 0.6
    b8a, b12 = _band(4000), _band(1000)
    others = _band(3000)
    indices = computer.compute_indices(others, others, others, others, b8a, b12)
    assert np.allclose(indices["NBR"], 0.6, atol=1e-4)


def test_ndwi_is_negative_over_vegetation(computer: SpectralIndexComputer) -> None:
    """Green < NIR on vegetation, so NDWI must come out negative."""
    b03, b8a = _band(1200), _band(4000)
    others = _band(1000)
    indices = computer.compute_indices(others, b03, others, others, b8a, others)
    assert np.all(indices["NDWI"] < 0)


def test_indices_are_all_float32(computer: SpectralIndexComputer) -> None:
    band = _band(4000)
    indices = computer.compute_indices(band, band, band, band, band, band)
    assert set(indices) == _ALL_INDICES
    assert all(a.dtype == np.float32 for a in indices.values())


def test_nodata_mask_propagates(computer: SpectralIndexComputer) -> None:
    band = _band(4000)
    mask = np.array([[True, False], [False, False]])
    indices = computer.compute_indices(band, band, band, band, band, band, mask)
    nodata = computer._config.nodata_value
    for name in _ALL_INDICES:
        assert indices[name][0, 0] == nodata, name
        assert indices[name][1, 1] != nodata, name


def test_nodata_mask_is_derived_when_omitted(
    computer: SpectralIndexComputer,
) -> None:
    """A zero DN marks a gap and must not silently become a real value."""
    good = _band(4000)
    bad = good.copy()
    bad[0, 0] = 0.0
    indices = computer.compute_indices(good, good, bad, good, good, good)
    assert indices["NDVI"][0, 0] == computer._config.nodata_value


def test_mask_nodata_flags_non_positive_and_non_finite(
    computer: SpectralIndexComputer,
) -> None:
    good = _band(4000)
    bad = good.copy()
    bad[0, 0] = 0.0
    bad[0, 1] = np.nan
    invalid = computer.mask_nodata(good, bad)
    assert invalid[0, 0]
    assert invalid[0, 1]
    assert not invalid[1, 1]


def test_out_of_range_values_are_clamped_to_nodata(
    computer: SpectralIndexComputer,
) -> None:
    """EVI's denominator can drive it far outside [-1, 1] on odd pixels."""
    nodata = computer._config.nodata_value
    b02 = _band(9000)  # a very blue pixel pushes EVI out of range
    b04, b8a = _band(500), _band(9500)
    others = _band(3000)
    indices = computer.compute_indices(b02, others, b04, others, b8a, others)
    assert np.all((indices["EVI"] == nodata) | (np.abs(indices["EVI"]) <= 1.0))
