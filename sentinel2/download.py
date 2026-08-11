"""Sentinel-2 band download as float32 GeoTIFFs via stackstac COG reads."""

import logging
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

import numpy as np
import pandas as pd
import rasterio
import stackstac
from affine import Affine
from rasterio.crs import CRS

logger = logging.getLogger(__name__)


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
        downloaded: dict[str, Path] = {}

        for band_name in self._config.bands:
            out_path = output_scene_dir / f"{band_name}.tif"
            if out_path.exists():
                logger.info("SKIP: %s (%s) already exists.", band_name, tile_id)
                downloaded[band_name] = out_path
                continue

            arr = data.sel(band=band_name).values[0]  # single acquisition [y, x]
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
