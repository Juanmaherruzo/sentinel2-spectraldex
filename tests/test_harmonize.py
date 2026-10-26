"""Tests for the Sentinel-2 processing-baseline 04.00 harmonisation.

From 2022-01-25 L2A digital numbers carry +1000; without removing it every ratio
index is biased low. Nothing here touches the network: ``stackstac.stack`` is
replaced with a stub returning a small in-memory DataArray.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import geopandas as gpd
import numpy as np
import pytest
import rasterio
import xarray as xr
from shapely.geometry import box

from sentinel2 import download
from sentinel2.download import (
    HARMONISED_TAG,
    STACBandDownloadConfig,
    STACCOGBandDownloader,
    band_offsets,
    boa_add_offset,
    harmonize_dn,
    parse_boa_offsets,
)
from sentinel2.indices import IndexComputationConfig, SpectralIndexComputer


def _item(**props: Any) -> SimpleNamespace:
    return SimpleNamespace(id="S2X_test", properties=props)


# Excerpt in the layout of a real MTD_MSIL2A.xml (baseline 05.10).
_MTD_PB0510 = (
    """<Product_Image_Characteristics>
<QUANTIFICATION_VALUES_LIST>
<BOA_QUANTIFICATION_VALUE unit="none">10000</BOA_QUANTIFICATION_VALUE>
</QUANTIFICATION_VALUES_LIST>
<BOA_ADD_OFFSET_VALUES_LIST>
"""
    + "".join(
        f'<BOA_ADD_OFFSET band_id="{i}">-1000</BOA_ADD_OFFSET>\n' for i in range(13)
    )
    + """</BOA_ADD_OFFSET_VALUES_LIST>
</Product_Image_Characteristics>"""
)


def test_offsets_are_parsed_per_band_from_product_metadata() -> None:
    offsets = parse_boa_offsets(_MTD_PB0510)
    assert offsets["B02"] == -1000
    assert offsets["B8A"] == -1000  # band_id 8, not B09
    assert offsets["B12"] == -1000
    assert parse_boa_offsets("<Product_Image_Characteristics/>") == {}


class _Response:
    def __init__(self, text: str) -> None:
        self._text = text

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._text.encode("utf-8")


def _item_with_metadata(**props: Any) -> SimpleNamespace:
    item = _item(**props)
    item.assets = {"product-metadata": SimpleNamespace(href="https://x/MTD.xml")}
    return item


def test_band_offsets_come_from_the_product_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(download, "urlopen", lambda *_, **__: _Response(_MTD_PB0510))
    # The metadata wins even over a baseline that would say otherwise.
    item = _item_with_metadata(**{"s2:processing_baseline": "03.00"})
    assert band_offsets(item, ("B04", "B8A")) == {"B04": -1000, "B8A": -1000}


def test_old_products_without_declared_offsets_get_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        download, "urlopen", lambda *_, **__: _Response("<Product_Image/>")
    )
    item = _item_with_metadata(**{"s2:processing_baseline": "03.00"})
    assert band_offsets(item, ("B04",)) == {"B04": 0}


def test_unreadable_metadata_falls_back_to_the_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_: object, **__: object) -> None:
        raise OSError("network down")

    monkeypatch.setattr(download, "urlopen", boom)
    item = _item_with_metadata(**{"s2:processing_baseline": "05.10"})
    assert band_offsets(item, ("B04", "B8A")) == {"B04": -1000, "B8A": -1000}


@pytest.mark.parametrize(
    ("baseline", "expected"),
    [("05.10", -1000), ("04.00", -1000), ("03.01", 0), ("02.14", 0)],
)
def test_offset_follows_the_processing_baseline(baseline: str, expected: int) -> None:
    assert boa_add_offset(_item(**{"s2:processing_baseline": baseline})) == expected


@pytest.mark.parametrize(
    ("date", "expected"),
    [
        ("2023-07-01T10:56:21Z", -1000),
        ("2022-01-25T00:00:00Z", -1000),
        ("2021-12-31T10:00:00Z", 0),
    ],
)
def test_offset_falls_back_to_the_date(date: str, expected: int) -> None:
    assert boa_add_offset(_item(datetime=date)) == expected


def test_harmonize_keeps_nodata_and_floors_dark_pixels() -> None:
    dn = np.array([0.0, 1500.0, 1000.0, 950.0], dtype=np.float32)
    out = harmonize_dn(dn, -1000)
    assert out.tolist() == [0.0, 500.0, 1.0, 1.0]  # 0 stays nodata
    assert harmonize_dn(dn, 0).tolist() == dn.tolist()


def test_offset_changes_ndvi_as_expected(tmp_path: Path) -> None:
    aoi_path = tmp_path / "aoi.gpkg"
    gpd.GeoDataFrame(
        {"geometry": [box(-3.8, 37.7, -3.7, 37.8)]}, crs="EPSG:4326"
    ).to_file(aoi_path, driver="GPKG")
    computer = SpectralIndexComputer(
        IndexComputationConfig(
            aoi_path=aoi_path,
            bands_root=tmp_path / "bands",
            output_root=tmp_path / "out",
        )
    )
    shape = (2, 2)
    true = {"b02": 300, "b03": 600, "b04": 500, "b05": 900, "b8a": 3000, "b12": 1500}
    raw = {k: np.full(shape, v + 1000, np.float32) for k, v in true.items()}
    clean = {k: np.full(shape, v, np.float32) for k, v in true.items()}

    fixed = computer.compute_indices(**raw, boa_add_offset=-1000)["NDVI"]
    reference = computer.compute_indices(**clean)["NDVI"]
    biased = computer.compute_indices(**raw)["NDVI"]

    np.testing.assert_allclose(fixed, reference, rtol=1e-6)
    assert reference[0, 0] == pytest.approx((3000 - 500) / (3000 + 500), rel=1e-6)
    assert biased[0, 0] < reference[0, 0] - 0.1  # the bug this guards against


def _fake_stack(values: dict[str, float]) -> Any:
    bands = list(values)
    data = np.stack([np.full((1, 3, 3), v, np.float32) for v in values.values()], 1)
    arr = xr.DataArray(
        data,
        dims=("time", "band", "y", "x"),
        coords={"band": bands, "y": [30.0, 10.0, -10.0], "x": [0.0, 20.0, 40.0]},
    )
    return SimpleNamespace(compute=lambda: arr)


def test_download_harmonises_tags_and_rewrites_stale_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = STACBandDownloadConfig(output_dir=tmp_path, bands=("B04", "B8A"))
    dl = STACCOGBandDownloader(cfg)
    monkeypatch.setattr(
        download.stackstac, "stack", lambda **_: _fake_stack({"B04": 1500, "B8A": 4000})
    )
    item = _item(**{"s2:processing_baseline": "05.10"})

    # A band written before the fix: no tag, DN still carrying +1000.
    stale = tmp_path / "2024-06-01" / "T30SUH" / "B04.tif"
    stale.parent.mkdir(parents=True)
    with rasterio.open(
        stale, "w", driver="GTiff", dtype="float32", count=1, width=3, height=3
    ) as dst:
        dst.write(np.full((3, 3), 1500, np.float32), 1)

    paths = dl._download_scene_stackstac(item, "2024-06-01", "T30SUH", [0, 0, 1, 1])

    for band, expected in (("B04", 500.0), ("B8A", 3000.0)):
        with rasterio.open(paths[band]) as src:
            assert src.tags()[HARMONISED_TAG] == "-1000"
            assert float(src.read(1)[0, 0]) == expected
