"""Tests for STAC scene deduplication on a synthetic DataFrame."""

import pandas as pd
import pytest

from sentinel2.search import STACSceneDeduplicator, STACSearchConfig


def _scenes() -> pd.DataFrame:
    # Same tile + date + satellite, two processing timestamps -> keep the latest.
    # A second satellite (S2B) on the same tile/date with lower cloud -> wins pass 2.
    return pd.DataFrame(
        {
            "scene_name": [
                "S2A_MSIL2A_20240601_T30SVF_20240601T120000",
                "S2A_MSIL2A_20240601_T30SVF_20240605T120000",  # newer processing
                "S2B_MSIL2A_20240601_T30SVF_20240602T120000",  # lower cloud
            ],
            "date": ["2024-06-01", "2024-06-01", "2024-06-01"],
            "tile_id": ["T30SVF", "T30SVF", "T30SVF"],
            "cloud_cover_pct": [8.0, 8.0, 2.0],
        }
    )


def test_deduplicate_collapses_to_one_scene_per_tile_date() -> None:
    clean = STACSceneDeduplicator(_scenes()).deduplicate()
    assert len(clean) == 1
    # The S2B scene has the lowest cloud cover, so pass 2 keeps it.
    assert clean.iloc[0]["cloud_cover_pct"] == 2.0
    # Helper columns are dropped from the result.
    assert "satellite" not in clean.columns


def test_deduplicator_rejects_empty_frame() -> None:
    with pytest.raises(ValueError, match="empty"):
        STACSceneDeduplicator(pd.DataFrame())


def test_search_config_validates_dates_and_cloud() -> None:
    with pytest.raises(ValueError, match="earlier than"):
        STACSearchConfig("2024-09-30", "2024-06-01", max_cloud_cover=10)
    with pytest.raises(ValueError, match="max_cloud_cover"):
        STACSearchConfig("2024-06-01", "2024-09-30", max_cloud_cover=150)
    with pytest.raises(ValueError, match="date format"):
        STACSearchConfig("06/01/2024", "2024-09-30", max_cloud_cover=10)
