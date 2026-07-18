"""Spectral index computation (NDVI, SAVI, EVI, NBR, NDRE, NDWI) and plotting.

Multi-tile aware: when an AOI spans several MGRS tiles the bands are merged
before being clipped to the exact polygon boundary. One clipped float32 LZW
GeoTIFF is written per index per scene.
"""

import logging
import warnings
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

import geopandas as gpd
import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from matplotlib.figure import Figure
from rasterio.io import MemoryFile
from rasterio.mask import mask as rasterio_mask
from rasterio.merge import merge as rasterio_merge
from scipy.ndimage import zoom
from shapely.geometry import shape

warnings.filterwarnings("ignore", category=rasterio.errors.NotGeoreferencedWarning)
logger = logging.getLogger(__name__)

# Display ranges and colour maps for the diagnostic dashboard.
_INDEX_DISPLAY: dict[str, dict[str, Any]] = {
    "NDVI": {"vmin": -0.1, "vmax": 0.8, "cmap": "RdYlGn"},
    "SAVI": {"vmin": -0.1, "vmax": 0.8, "cmap": "RdYlGn"},
    "EVI": {"vmin": -0.1, "vmax": 0.8, "cmap": "RdYlGn"},
    "NBR": {"vmin": -0.5, "vmax": 0.8, "cmap": "RdYlGn"},
    "NDRE": {"vmin": -0.1, "vmax": 0.7, "cmap": "RdYlGn"},
    "NDWI": {"vmin": -0.5, "vmax": 0.5, "cmap": "RdBu"},
}
_INDEX_NAMES = list(_INDEX_DISPLAY)


@dataclass
class IndexComputationConfig:
    """Configuration for spectral index computation.

    ``savi_l`` is the SAVI soil-adjustment factor (0 = dense canopy, 1 = sparse);
    ``nodata_value`` fills pixels outside the AOI polygon and invalid pixels.
    """

    bands_root: Path
    output_root: Path
    aoi_path: Path
    savi_l: float = 0.5
    nodata_value: float = -9999.0
    n_threads: int = 1


class SpectralIndexComputer:
    """Compute six spectral indices from Sentinel-2 L2A bands, clipped to the AOI."""

    def __init__(self, config: IndexComputationConfig) -> None:
        self._config = config
        self._write_lock = Lock()
        self._config.output_root.mkdir(parents=True, exist_ok=True)
        self._aoi_geom = self._load_aoi_geometry()

    def _load_aoi_geometry(self) -> list[dict[str, Any]]:
        if not self._config.aoi_path.exists():
            raise FileNotFoundError(
                f"AOI GeoPackage not found: {self._config.aoi_path}"
            )
        gdf = gpd.read_file(self._config.aoi_path)
        if gdf.empty or gdf.geometry.is_empty.all():
            raise ValueError("AOI GeoPackage contains no valid geometries.")
        if gdf.crs is None:
            raise ValueError("AOI GeoPackage has no CRS defined.")
        if gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(epsg=4326)
        return [geom.__geo_interface__ for geom in gdf.geometry]

    def _merge_and_clip_band(
        self, band_paths: list[Path]
    ) -> tuple[np.ndarray, dict[str, Any]]:
        """Merge tile GeoTIFFs (if several) and clip to the exact AOI polygon."""
        for path in band_paths:
            if not path.exists():
                raise FileNotFoundError(f"Band file not found: {path}")

        if len(band_paths) == 1:
            with rasterio.open(band_paths[0]) as src:
                data = src.read(1).astype(np.float32)
                profile = src.profile.copy()
                src_crs = src.crs
        else:
            datasets = [rasterio.open(p) for p in band_paths]
            try:
                merged, transform = rasterio_merge(datasets)
                profile = datasets[0].profile.copy()
                profile.update(
                    height=merged.shape[1], width=merged.shape[2], transform=transform
                )
                src_crs = datasets[0].crs
                data = merged[0].astype(np.float32)
            finally:
                for dataset in datasets:
                    dataset.close()

        mem_profile = profile.copy()
        mem_profile.update(dtype="float32", driver="GTiff")

        with MemoryFile() as memfile:
            with memfile.open(**mem_profile) as mem_dst:
                mem_dst.write(data, 1)
            with memfile.open() as mem_src:
                aoi_gdf = gpd.GeoDataFrame(
                    geometry=[shape(g) for g in self._aoi_geom], crs="EPSG:4326"
                )
                if src_crs.to_epsg() != 4326:
                    aoi_gdf = aoi_gdf.to_crs(src_crs)
                geoms = [g.__geo_interface__ for g in aoi_gdf.geometry]
                try:
                    clipped, clip_transform = rasterio_mask(
                        mem_src,
                        geoms,
                        crop=True,
                        nodata=self._config.nodata_value,
                        filled=True,
                        all_touched=True,
                    )
                except (ValueError, rasterio.errors.RasterioError) as exc:
                    raise RuntimeError(f"rasterio.mask failed: {exc}") from exc

                out_profile = mem_src.profile.copy()
                out_profile.update(
                    driver="GTiff",
                    dtype="float32",
                    count=1,
                    nodata=self._config.nodata_value,
                    height=clipped.shape[1],
                    width=clipped.shape[2],
                    transform=clip_transform,
                    compress="lzw",
                    tiled=True,
                    blockxsize=256,
                    blockysize=256,
                )
                return clipped[0].astype(np.float32), out_profile

    def _resample_to_target(
        self, arr: np.ndarray, target_shape: tuple[int, int]
    ) -> np.ndarray:
        if arr.shape == target_shape:
            return arr.astype(np.float32)
        zoom_y = target_shape[0] / arr.shape[0]
        zoom_x = target_shape[1] / arr.shape[1]
        return zoom(arr, (zoom_y, zoom_x), order=1).astype(np.float32)

    def _mask_nodata(self, *arrays: np.ndarray) -> np.ndarray:
        """Return True where a pixel is invalid (DN <= 0 or non-finite)."""
        invalid = np.zeros(arrays[0].shape, dtype=bool)
        for arr in arrays:
            invalid |= (arr <= 0) | ~np.isfinite(arr)
        return invalid

    def _compute_indices(
        self,
        b02: np.ndarray,
        b03: np.ndarray,
        b04: np.ndarray,
        b05: np.ndarray,
        b8a: np.ndarray,
        b12: np.ndarray,
        nodata_mask: np.ndarray,
    ) -> dict[str, np.ndarray]:
        scale = 10000.0
        blue, green, red = b02 / scale, b03 / scale, b04 / scale
        re1, nir, swir = b05 / scale, b8a / scale, b12 / scale
        nd = self._config.nodata_value
        soil_factor = self._config.savi_l

        with np.errstate(divide="ignore", invalid="ignore"):
            denom = nir + red
            ndvi = np.where(denom != 0, (nir - red) / denom, nd).astype(np.float32)
            denom = nir + red + soil_factor
            savi = np.where(
                denom != 0, ((nir - red) / denom) * (1.0 + soil_factor), nd
            ).astype(np.float32)
            denom = nir + 6.0 * red - 7.5 * blue + 1.0
            evi = np.where(denom != 0, 2.5 * (nir - red) / denom, nd).astype(np.float32)
            denom = nir + swir
            nbr = np.where(denom != 0, (nir - swir) / denom, nd).astype(np.float32)
            denom = nir + re1
            ndre = np.where(denom != 0, (nir - re1) / denom, nd).astype(np.float32)
            denom = green + nir
            ndwi = np.where(denom != 0, (green - nir) / denom, nd).astype(np.float32)

        for arr in (ndvi, savi, evi, nbr, ndre, ndwi):
            arr[nodata_mask] = nd

        def clamp(arr: np.ndarray, lo: float, hi: float) -> np.ndarray:
            return np.where((arr != nd) & ((arr < lo) | (arr > hi)), nd, arr).astype(
                np.float32
            )

        return {
            "NDVI": clamp(ndvi, -1.0, 1.0),
            "SAVI": clamp(savi, -1.5, 1.5),
            "EVI": clamp(evi, -1.0, 1.0),
            "NBR": clamp(nbr, -1.0, 1.0),
            "NDRE": clamp(ndre, -1.0, 1.0),
            "NDWI": clamp(ndwi, -1.0, 1.0),
        }

    def _write_index(
        self,
        index_name: str,
        array: np.ndarray,
        profile: dict[str, Any],
        output_path: Path,
    ) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with self._write_lock, rasterio.open(output_path, "w", **profile) as dst:
            dst.write(array, 1)
        logger.info("OK: %s -> %s", index_name, output_path)

    def process_date(self, date: str, tile_ids: list[str]) -> dict[str, Path]:
        """Compute and write all six indices for one date across its tiles."""
        logger.info("Processing date: %s - tiles: %s", date, tile_ids)
        bands = ["B8A", "B02", "B03", "B04", "B05", "B12"]
        band_arrays: dict[str, np.ndarray] = {}
        ref_profile: dict[str, Any] | None = None

        for band in bands:
            band_paths = [
                self._config.bands_root / date / tile / f"{band}.tif"
                for tile in tile_ids
            ]
            arr, profile = self._merge_and_clip_band(band_paths)
            band_arrays[band] = arr
            if band == "B8A":
                ref_profile = profile

        assert ref_profile is not None  # B8A is always processed first
        target_shape = band_arrays["B8A"].shape
        b02 = self._resample_to_target(band_arrays["B02"], target_shape)
        b03 = self._resample_to_target(band_arrays["B03"], target_shape)
        b04 = self._resample_to_target(band_arrays["B04"], target_shape)
        b05 = band_arrays["B05"].astype(np.float32)
        b8a = band_arrays["B8A"].astype(np.float32)
        b12 = band_arrays["B12"].astype(np.float32)

        nodata_mask = self._mask_nodata(b02, b03, b04, b05, b8a, b12)
        indices = self._compute_indices(b02, b03, b04, b05, b8a, b12, nodata_mask)

        output_dir = self._config.output_root / date
        output_paths: dict[str, Path] = {}
        for index_name, index_arr in indices.items():
            output_path = output_dir / f"{index_name}.tif"
            if output_path.exists():
                logger.info("SKIP: %s already exists.", index_name)
            else:
                self._write_index(index_name, index_arr, ref_profile, output_path)
            output_paths[index_name] = output_path
        return output_paths

    def process_all_scenes(self, clean_df: pd.DataFrame) -> dict[str, dict[str, Path]]:
        """Compute indices for every date in ``clean_df``."""
        if clean_df.empty:
            raise ValueError("clean_df is empty. Run the search step first.")
        dates = sorted(clean_df["date"].unique())
        all_results: dict[str, dict[str, Path]] = {}
        for d_idx, date in enumerate(dates):
            tile_ids = clean_df[clean_df["date"] == date]["tile_id"].tolist()
            logger.info("[%d/%d] %s", d_idx + 1, len(dates), date)
            try:
                all_results[date] = self.process_date(date, tile_ids)
            except (FileNotFoundError, ValueError, RuntimeError) as exc:
                logger.error("%s - skipping date.", exc)
        logger.info(
            "Index computation complete. %d dates -> %s",
            len(all_results),
            self._config.output_root,
        )
        return all_results


def _read_index_data(scene_dir: Path) -> dict[str, dict[str, np.ndarray]]:
    """Read each index GeoTIFF, returning the display array and its valid pixels."""
    data_by_index: dict[str, dict[str, np.ndarray]] = {}
    for index_name in _INDEX_NAMES:
        tif_path = scene_dir / f"{index_name}.tif"
        if not tif_path.exists():
            continue
        with rasterio.open(tif_path) as src:
            arr = src.read(1).astype(np.float32)
            valid_mask = (arr != src.nodata) & np.isfinite(arr)
        data_by_index[index_name] = {
            "display": np.where(valid_mask, arr, np.nan),
            "valid": arr[valid_mask],
        }
    return data_by_index


def plot_scene_indices(indices_root: Path) -> None:
    """Save a spatial-map + histogram dashboard PNG for every computed scene."""
    scene_dirs = sorted(
        d for d in indices_root.rglob("*") if d.is_dir() and any(d.glob("NDVI.tif"))
    )
    if not scene_dirs:
        logger.warning("No scenes found in %s", indices_root)
        return

    for scene_dir in scene_dirs:
        scene_date = scene_dir.name
        data_by_index = _read_index_data(scene_dir)
        if not data_by_index:
            continue

        n_indices = len(data_by_index)
        fig: Figure = plt.figure(figsize=(5 * n_indices, 8))
        fig.suptitle(
            f"Spectral Index Dashboard - {scene_date}",
            fontsize=11,
            fontweight="bold",
            y=1.01,
        )
        grid = gridspec.GridSpec(2, n_indices, figure=fig, hspace=0.45, wspace=0.35)

        for col, (index_name, data) in enumerate(data_by_index.items()):
            cfg = _INDEX_DISPLAY[index_name]
            valid = data["valid"]

            ax_map = fig.add_subplot(grid[0, col])
            image = ax_map.imshow(
                data["display"],
                cmap=cfg["cmap"],
                vmin=cfg["vmin"],
                vmax=cfg["vmax"],
                aspect="auto",
            )
            fig.colorbar(image, ax=ax_map, fraction=0.046, pad=0.04)
            ax_map.set_title(index_name, fontsize=11, fontweight="bold")
            ax_map.set_xticks([])
            ax_map.set_yticks([])

            ax_hist = fig.add_subplot(grid[1, col])
            if len(valid) > 0:
                ax_hist.hist(valid, bins=40, color="#2ecc71", edgecolor="#27ae60")
                ax_hist.axvline(
                    float(valid.mean()),
                    color="#e74c3c",
                    linestyle="--",
                    label=f"Mean {valid.mean():.3f}",
                )
                ax_hist.axvline(
                    float(np.median(valid)),
                    color="#3498db",
                    linestyle=":",
                    label=f"Median {np.median(valid):.3f}",
                )
                ax_hist.legend(fontsize=7, loc="upper right")
            ax_hist.set_xlabel(index_name, fontsize=9)
            ax_hist.set_ylabel("Pixel count", fontsize=9)

        out_png = indices_root / f"dashboard_{scene_date}.png"
        fig.savefig(out_png, dpi=150, bbox_inches="tight")
        plt.close(fig)
        logger.info("Dashboard saved: %s", out_png)
