"""Tests for the COG band downloader.

``download.py`` was previously untested despite holding the module's only broad
exception handler and all of its input validation. Nothing here touches the
network: ``_download_scene_stackstac`` is replaced with a stub so the orchestration,
the validation and the failure accounting can be exercised offline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from sentinel2.download import STACBandDownloadConfig, STACCOGBandDownloader


@pytest.fixture
def downloader(tmp_path: Path) -> STACCOGBandDownloader:
    return STACCOGBandDownloader(
        STACBandDownloadConfig(output_dir=tmp_path / "bands", resolution=20)
    )


def _scenes(n_tiles: int = 2) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "stac_item": [object() for _ in range(n_tiles)],
            "scene_name": [f"S2A_scene_{i}" for i in range(n_tiles)],
            "date": ["2024-06-01"] * n_tiles,
            "tile_id": [f"T30SV{chr(ord('A') + i)}" for i in range(n_tiles)],
        }
    )


_AOI = {"bbox_stac": [-3.8, 37.7, -3.7, 37.8]}


def test_output_directory_is_created(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "bands"
    STACCOGBandDownloader(STACBandDownloadConfig(output_dir=target))
    assert target.is_dir()


def test_empty_dataframe_is_rejected(downloader: STACCOGBandDownloader) -> None:
    with pytest.raises(ValueError, match="empty"):
        downloader.download_all_scenes(pd.DataFrame(), _AOI)


def test_missing_columns_are_named_in_the_error(
    downloader: STACCOGBandDownloader,
) -> None:
    incomplete = _scenes().drop(columns=["tile_id"])
    with pytest.raises(ValueError, match="tile_id"):
        downloader.download_all_scenes(incomplete, _AOI)


def test_missing_bbox_is_rejected(downloader: STACCOGBandDownloader) -> None:
    with pytest.raises(ValueError, match="bbox_stac"):
        downloader.download_all_scenes(_scenes(), {})


def test_successful_run_reports_no_failures(
    downloader: STACCOGBandDownloader, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_download(
        stac_item: Any, scene_date: str, tile_id: str, bbox: list[float]
    ) -> dict[str, Path]:
        return {"B04": Path(f"{scene_date}/{tile_id}/B04.tif")}

    monkeypatch.setattr(downloader, "_download_scene_stackstac", fake_download)
    results = downloader.download_all_scenes(_scenes(2), _AOI)

    assert set(results["2024-06-01"]) == {"T30SVA", "T30SVB"}
    assert downloader.failed_tiles == []


def test_a_failing_tile_is_recorded_and_does_not_abort_the_run(
    downloader: STACCOGBandDownloader, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partial download must be visible, not silently thinner."""

    def flaky(
        stac_item: Any, scene_date: str, tile_id: str, bbox: list[float]
    ) -> dict[str, Path]:
        if tile_id == "T30SVA":
            raise RuntimeError("stackstac download failed: connection reset")
        return {"B04": Path("B04.tif")}

    monkeypatch.setattr(downloader, "_download_scene_stackstac", flaky)
    results = downloader.download_all_scenes(_scenes(2), _AOI)

    # The good tile still came through.
    assert "T30SVB" in results["2024-06-01"]
    assert "T30SVA" not in results["2024-06-01"]
    # And the bad one is on the record with its reason.
    assert len(downloader.failed_tiles) == 1
    date, tile, reason = downloader.failed_tiles[0]
    assert (date, tile) == ("2024-06-01", "T30SVA")
    assert "connection reset" in reason


def test_failed_tiles_reset_between_runs(
    downloader: STACCOGBandDownloader, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        downloader,
        "_download_scene_stackstac",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    downloader.download_all_scenes(_scenes(2), _AOI)
    assert len(downloader.failed_tiles) == 2
    downloader.download_all_scenes(_scenes(1), _AOI)
    assert len(downloader.failed_tiles) == 1


def test_affine_is_anchored_at_the_pixel_corner() -> None:
    """stackstac gives pixel centres; a GeoTIFF transform needs the corner."""
    import numpy as np
    import xarray as xr

    res = 20
    data = xr.DataArray(
        np.zeros((2, 2), dtype="float32"),
        dims=("y", "x"),
        coords={"y": [4_180_010.0, 4_179_990.0], "x": [500_010.0, 500_030.0]},
    )
    transform = STACCOGBandDownloader._affine_from_dataarray(data, res)

    # Origin is half a pixel up-left of the first centre.
    assert transform.c == pytest.approx(500_010.0 - res / 2)
    assert transform.f == pytest.approx(4_180_010.0 + res / 2)
    assert transform.a == pytest.approx(res)
    assert transform.e == pytest.approx(-res)  # north-up rasters have negative y
