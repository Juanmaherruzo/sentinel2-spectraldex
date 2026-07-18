"""Sentinel-2 L2A scene search and deduplication over the STAC catalogue."""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from sentinel2.config import BANDS_NEEDED, STAC_URL, get_catalog

logger = logging.getLogger(__name__)


@dataclass
class STACSearchConfig:
    """STAC search parameters for the Sentinel-2 L2A collection."""

    date_start: str
    date_end: str
    max_cloud_cover: float
    max_results: int = 100
    collection: str = "sentinel-2-l2a"
    stac_url: str = STAC_URL

    def __post_init__(self) -> None:
        fmt = "%Y-%m-%d"
        try:
            t_start = datetime.strptime(self.date_start, fmt)
            t_end = datetime.strptime(self.date_end, fmt)
        except ValueError as exc:
            raise ValueError(
                f"Invalid date format, expected YYYY-MM-DD: {exc}"
            ) from exc
        if t_start >= t_end:
            raise ValueError(
                f"date_start ({self.date_start}) must be earlier than "
                f"date_end ({self.date_end})."
            )
        if not 0.0 <= self.max_cloud_cover <= 100.0:
            raise ValueError(
                f"max_cloud_cover must be in [0, 100]. Got: {self.max_cloud_cover}"
            )


class STACSceneSearch:
    """Query the Planetary Computer STAC API for Sentinel-2 L2A scenes."""

    def __init__(self, config: STACSearchConfig) -> None:
        self._config = config
        self._catalog = get_catalog(config.stac_url)

    def _extract_tile_id(self, item: Any) -> str:
        """Extract the MGRS tile identifier from a STAC item's properties."""
        tile = item.properties.get("s2:mgrs_tile", "")
        if tile:
            return f"T{tile}" if not tile.startswith("T") else tile
        for part in item.id.split("_"):
            if len(part) == 6 and part.startswith("T") and part[1:].isalnum():
                return str(part)
        return "UNKNOWN"

    def _item_to_record(self, item: Any) -> dict[str, Any]:
        """Convert a STAC Item to a flat dictionary for DataFrame construction."""
        band_urls = {
            band: item.assets[band].href for band in BANDS_NEEDED if band in item.assets
        }
        return {
            "scene_id": item.id,
            "scene_name": item.id,
            "tile_id": self._extract_tile_id(item),
            "date": item.properties.get("datetime", "")[:10],
            "cloud_cover_pct": float(item.properties.get("eo:cloud_cover", -1.0)),
            "band_urls": band_urls,
            "stac_item": item,
            "origin": "STAC_PC",
        }

    def search(self, aoi_info: dict[str, Any]) -> pd.DataFrame:
        """Execute the STAC search and return a chronologically sorted DataFrame."""
        if "bbox_stac" not in aoi_info:
            raise ValueError("aoi_info is missing 'bbox_stac'. Load the AOI first.")

        bbox = aoi_info["bbox_stac"]
        date_range = f"{self._config.date_start}/{self._config.date_end}"
        logger.info(
            "Searching STAC: %s | bbox %s | %s | cloud < %s%%",
            self._config.collection,
            bbox,
            date_range,
            self._config.max_cloud_cover,
        )

        search = self._catalog.search(
            collections=[self._config.collection],
            bbox=bbox,
            datetime=date_range,
            query={"eo:cloud_cover": {"lt": self._config.max_cloud_cover}},
            max_items=self._config.max_results,
            sortby="+datetime",
        )
        items = list(search.items())
        if not items:
            logger.warning("No scenes found for the given AOI, dates and cloud cover.")
            return pd.DataFrame()

        records = [self._item_to_record(item) for item in items]
        return pd.DataFrame(records).sort_values("date").reset_index(drop=True)


class STACSceneDeduplicator:
    """Remove duplicate scenes from the STAC search results.

    Pass 1 keeps the most recently processed scene per tile + date + satellite;
    pass 2 keeps the lowest cloud cover per tile + date across satellites.
    """

    def __init__(self, scenes_df: pd.DataFrame) -> None:
        if scenes_df.empty:
            raise ValueError("DataFrame is empty. Run STACSceneSearch.search() first.")
        self._df = scenes_df.copy()

    @staticmethod
    def _extract_satellite(scene_name: str) -> str:
        """Extract the satellite identifier (S2A, S2B, S2C) from the scene name."""
        try:
            return scene_name.split("_")[0]
        except (IndexError, AttributeError):
            return "UNKNOWN"

    @staticmethod
    def _extract_processing_ts(scene_name: str) -> str:
        """Extract the processing timestamp (last token) from the scene name."""
        try:
            return scene_name.split("_")[-1]
        except (IndexError, AttributeError):
            return ""

    def deduplicate(self) -> pd.DataFrame:
        """Apply both deduplication passes and return the cleaned DataFrame."""
        df = self._df.copy()
        df["satellite"] = df["scene_name"].apply(self._extract_satellite)
        df["processing_ts"] = df["scene_name"].apply(self._extract_processing_ts)

        df = (
            df.sort_values("processing_ts", ascending=False)
            .drop_duplicates(subset=["date", "tile_id", "satellite"], keep="first")
            .reset_index(drop=True)
        )
        df = (
            df.sort_values("cloud_cover_pct", ascending=True)
            .drop_duplicates(subset=["date", "tile_id"], keep="first")
            .reset_index(drop=True)
        )
        df = df.drop(columns=["satellite", "processing_ts"])
        return df.sort_values("date").reset_index(drop=True)


def summarise_results(df: pd.DataFrame) -> None:
    """Log a summary of the raw STAC search results."""
    if df.empty:
        logger.info("No results found.")
        return
    logger.info(
        "Total scenes: %d | dates %s to %s | tiles %s | mean cloud %.1f%%",
        len(df),
        df["date"].min(),
        df["date"].max(),
        sorted(df["tile_id"].unique().tolist()),
        df["cloud_cover_pct"].mean(),
    )


def summarise_deduplicated(original_df: pd.DataFrame, clean_df: pd.DataFrame) -> None:
    """Log a before/after comparison of the deduplication step."""
    logger.info(
        "Deduplication: %d -> %d scenes (%d duplicates removed)",
        len(original_df),
        len(clean_df),
        len(original_df) - len(clean_df),
    )
