"""Sentinel-2 band download as float32 GeoTIFFs via stackstac COG reads."""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.request import urlopen

import numpy as np
import pandas as pd
import rasterio
import stackstac
from affine import Affine
from rasterio.crs import CRS

logger = logging.getLogger(__name__)

# From processing baseline 04.00 (in production since 2022-01-25) ESA adds an
# offset of +1000 to every L2A digital number, so reflectance = (DN - 1000) / 10000.
# The Planetary Computer serves the products as ESA ships them (not harmonised).
BOA_ADD_OFFSET = -1000
_OFFSET_BASELINE = (4, 0)
_OFFSET_START_DATE = "2022-01-25"
# GeoTIFF tag written on every harmonised band; files without it predate the fix.
HARMONISED_TAG = "S2_BOA_ADD_OFFSET_APPLIED"


def boa_add_offset(stac_item: Any) -> int:
    """Offset to add to this item's DN before dividing by 10000 (0 or -1000).

    Read from the item's ``s2:processing_baseline``; when the property is
    missing, inferred from the acquisition date (with a warning).
    """
    props = getattr(stac_item, "properties", {}) or {}
    baseline = props.get("s2:processing_baseline")
    if baseline:
        try:
            major, minor = (int(part) for part in str(baseline).split(".")[:2])
        except ValueError:
            logger.warning(
                "Unreadable processing baseline %r; using the date", baseline
            )
        else:
            return BOA_ADD_OFFSET if (major, minor) >= _OFFSET_BASELINE else 0
    date = str(props.get("datetime", ""))[:10]
    logger.warning(
        "No processing baseline on item %s; offset inferred from its date (%s)",
        getattr(stac_item, "id", "?"),
        date or "unknown",
    )
    return BOA_ADD_OFFSET if date >= _OFFSET_START_DATE else 0


# BOA_ADD_OFFSET band_id order in MTD_MSIL2A.xml (B10 has no L2A product).
_BAND_IDS = ("B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A",
             "B09", "B10", "B11", "B12")  # fmt: skip
_OFFSET_RE = re.compile(r'<BOA_ADD_OFFSET\s+band_id="(\d+)"\s*>\s*(-?\d+)\s*<')


def parse_boa_offsets(product_metadata_xml: str) -> dict[str, int]:
    """Per-band ``BOA_ADD_OFFSET`` values declared in an MTD_MSIL2A.xml.

    Products from before processing baseline 04.00 declare none and get {}.
    """
    offsets: dict[str, int] = {}
    for band_id, value in _OFFSET_RE.findall(product_metadata_xml):
        index = int(band_id)
        if index < len(_BAND_IDS):
            offsets[_BAND_IDS[index]] = int(value)
    return offsets


def band_offsets(stac_item: Any, bands: tuple[str, ...]) -> dict[str, int]:
    """Offset to add to each band's DN, read from the product itself.

    ESA advises reading ``BOA_ADD_OFFSET`` from each product's metadata rather
    than hard-coding it. The ``product-metadata`` asset is read when available;
    if it cannot be read, the offset falls back to :func:`boa_add_offset`
    (processing baseline, then acquisition date) for every band.
    """
    asset = getattr(stac_item, "assets", {}).get("product-metadata")
    if asset is not None:
        try:
            with urlopen(asset.href, timeout=60) as response:
                declared = parse_boa_offsets(response.read().decode("utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning(
                "Product metadata of %s unreadable (%s); using the baseline",
                getattr(stac_item, "id", "?"),
                exc,
            )
        else:
            return {band: declared.get(band, 0) for band in bands}
    fallback = boa_add_offset(stac_item)
    return dict.fromkeys(bands, fallback)


def harmonize_dn(arr: np.ndarray, offset: int) -> np.ndarray:
    """Remove the L2A offset from a DN array, keeping 0 as nodata.

    Valid pixels darker than the offset (true reflectance <= 0) are floored at
    1 rather than 0, so they are not mistaken for nodata downstream.
    """
    out = arr.astype(np.float32, copy=True)
    if offset == 0:
        return out
    valid = out > 0
    out[valid] = np.maximum(out[valid] + offset, 1.0)
    return out


@dataclass
class STACBandDownloadConfig:
    """Configuration for COG band downloads via stackstac.

    ``resolution`` is the output pixel size in metres (20 m matches native B8A);
    ``epsg`` is the output CRS (32630 = UTM zone 30N).
    """

    output_dir: Path
    resolution: int = 20
    n_threads: int = 4
    bands: tuple[str, ...] = ("B02", "B03", "B04", "B05", "B8A", "B11", "B12")
    epsg: int = 32630


class STACCOGBandDownloader:
    """Download Sentinel-2 L2A bands using stackstac COG range requests.

    Only the byte ranges intersecting the AOI bounding box are transferred, so
    the full ~1 GB SAFE archive per scene is never downloaded. Output layout:
    ``<output_dir>/<YYYY-MM-DD>/<MGRS_tile>/<band>.tif``.

    Tiles that fail are skipped so one bad scene does not abort a long run, but
    they are recorded in :attr:`failed_tiles` and summarised at ``WARNING``:
    indices computed from a partial download would otherwise carry silent gaps.
    """

    def __init__(self, config: STACBandDownloadConfig) -> None:
        self._config = config
        self._write_lock = Lock()
        self._config.output_dir.mkdir(parents=True, exist_ok=True)
        # (date, tile_id, error) for every tile that could not be downloaded.
        self.failed_tiles: list[tuple[str, str, str]] = []

    @staticmethod
    def _affine_from_dataarray(data: Any, resolution: int) -> Affine:
        """Build an Affine transform from the DataArray pixel-centre coordinates."""
        res = resolution
        x_origin = float(data.x.min()) - res / 2
        y_origin = float(data.y.max()) + res / 2
        return Affine(res, 0.0, x_origin, 0.0, -res, y_origin)

    def _download_scene_stackstac(
        self,
        stac_item: Any,
        scene_date: str,
        tile_id: str,
        bbox_latlon: list[float],
    ) -> dict[str, Path]:
        """Download every configured band for a single MGRS tile and date."""
        output_scene_dir = self._config.output_dir / scene_date / tile_id
        output_scene_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Reading bands via COG for %s (%s)...", tile_id, scene_date)

        try:
            stack = stackstac.stack(
                items=[stac_item],
                assets=list(self._config.bands),
                resolution=self._config.resolution,
                epsg=self._config.epsg,
                bounds_latlon=bbox_latlon,
                dtype="float32",
                rescale=False,
                fill_value=np.float32(0),
            )
            data = stack.compute()  # xarray DataArray [time, band, y, x]
        except Exception as exc:  # stackstac wraps varied network/raster errors
            raise RuntimeError(
                f"stackstac download failed for {tile_id}: {exc}"
            ) from exc

        transform = self._affine_from_dataarray(data, self._config.resolution)
        crs = CRS.from_epsg(self._config.epsg)
        offsets = band_offsets(stac_item, tuple(self._config.bands))
        downloaded: dict[str, Path] = {}

        for band_name in self._config.bands:
            out_path = output_scene_dir / f"{band_name}.tif"
            if out_path.exists():
                with rasterio.open(out_path) as existing:
                    harmonised = HARMONISED_TAG in existing.tags()
                if harmonised:
                    logger.info("SKIP: %s (%s) already exists.", band_name, tile_id)
                    downloaded[band_name] = out_path
                    continue
                # Written before the offset fix: its DN may still carry +1000.
                logger.warning(
                    "REWRITE: %s (%s) predates baseline harmonisation.",
                    band_name,
                    tile_id,
                )

            # single acquisition [y, x], DN with the L2A offset removed
            offset = offsets[band_name]
            arr = harmonize_dn(data.sel(band=band_name).values[0], offset)
            profile = {
                "driver": "GTiff",
                "dtype": "float32",
                "count": 1,
                "height": arr.shape[0],
                "width": arr.shape[1],
                "crs": crs,
                "transform": transform,
                "compress": "lzw",
                "tiled": True,
                "blockxsize": 256,
                "blockysize": 256,
                "nodata": 0.0,
            }
            with self._write_lock, rasterio.open(out_path, "w", **profile) as dst:
                dst.write(arr, 1)
                dst.update_tags(**{HARMONISED_TAG: str(offset)})
            downloaded[band_name] = out_path
            logger.info("OK: %s (%s) -> %s", band_name, tile_id, out_path)

        return downloaded

    def download_all_scenes(
        self,
        clean_df: pd.DataFrame,
        aoi_info: dict[str, Any],
    ) -> dict[str, dict[str, dict[str, Path]]]:
        """Download every configured band for all dates and tiles in ``clean_df``."""
        if clean_df.empty:
            raise ValueError("clean_df is empty. Run the search step first.")
        required = {"stac_item", "scene_name", "date", "tile_id"}
        missing = required - set(clean_df.columns)
        if missing:
            raise ValueError(f"clean_df is missing columns: {missing}")
        if "bbox_stac" not in aoi_info:
            raise ValueError("aoi_info is missing 'bbox_stac'. Load the AOI first.")

        bbox_latlon = aoi_info["bbox_stac"]
        dates = sorted(clean_df["date"].unique())
        all_results: dict[str, dict[str, dict[str, Path]]] = {}
        attempted = 0
        self.failed_tiles = []

        for d_idx, date in enumerate(dates):
            date_scenes = clean_df[clean_df["date"] == date]
            logger.info(
                "[%d/%d] %s - %d tile(s)", d_idx + 1, len(dates), date, len(date_scenes)
            )
            all_results[date] = {}
            for _, row in date_scenes.iterrows():
                tile_id = row["tile_id"]
                attempted += 1
                try:
                    all_results[date][tile_id] = self._download_scene_stackstac(
                        row["stac_item"], date, tile_id, bbox_latlon
                    )
                except RuntimeError as exc:
                    # Recorded, not just logged: a caller computing indices from
                    # the result needs to know the coverage is incomplete.
                    self.failed_tiles.append((date, tile_id, str(exc)))
                    logger.error("%s (%s) skipped - %s", tile_id, date, exc)

        if self.failed_tiles:
            logger.warning(
                "INCOMPLETE DOWNLOAD: %d of %d tiles failed. Indices computed from "
                "this output will have gaps: %s",
                len(self.failed_tiles),
                attempted,
                ", ".join(f"{d}/{t}" for d, t, _ in self.failed_tiles),
            )
        logger.info(
            "Download complete. %d dates, %d/%d tiles -> %s",
            len(all_results),
            attempted - len(self.failed_tiles),
            attempted,
            self._config.output_dir,
        )
        return all_results
