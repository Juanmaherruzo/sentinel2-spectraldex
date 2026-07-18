"""End-to-end orchestration: AOI -> STAC search -> download -> indices -> plots."""

import argparse
import logging
from pathlib import Path

from sentinel2.aoi import load_aoi
from sentinel2.download import STACBandDownloadConfig, STACCOGBandDownloader
from sentinel2.indices import (
    IndexComputationConfig,
    SpectralIndexComputer,
    plot_scene_indices,
)
from sentinel2.search import (
    STACSceneDeduplicator,
    STACSceneSearch,
    STACSearchConfig,
    summarise_deduplicated,
    summarise_results,
)

logger = logging.getLogger(__name__)


def run(
    aoi_path: Path,
    date_start: str,
    date_end: str,
    output_dir: Path,
    max_cloud_cover: float = 10.0,
    max_results: int = 20,
    resolution: int = 20,
    epsg: int = 32630,
    savi_l: float = 0.5,
) -> dict[str, dict[str, Path]]:
    """Run the full pipeline and return the computed index paths per date."""
    bands_root = output_dir / "raw_bands"
    indices_root = output_dir / "indices"

    aoi_info = load_aoi(aoi_path)

    search_cfg = STACSearchConfig(
        date_start=date_start,
        date_end=date_end,
        max_cloud_cover=max_cloud_cover,
        max_results=max_results,
    )
    scenes_df = STACSceneSearch(search_cfg).search(aoi_info)
    summarise_results(scenes_df)
    if scenes_df.empty:
        return {}

    clean_df = STACSceneDeduplicator(scenes_df).deduplicate()
    summarise_deduplicated(scenes_df, clean_df)

    download_cfg = STACBandDownloadConfig(
        output_dir=bands_root, resolution=resolution, epsg=epsg
    )
    STACCOGBandDownloader(download_cfg).download_all_scenes(clean_df, aoi_info)

    index_cfg = IndexComputationConfig(
        bands_root=bands_root,
        output_root=indices_root,
        aoi_path=aoi_path,
        savi_l=savi_l,
    )
    results = SpectralIndexComputer(index_cfg).process_all_scenes(clean_df)
    plot_scene_indices(indices_root)
    return results


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sentinel-2 spectral index pipeline (STAC -> COG -> indices)."
    )
    parser.add_argument("--aoi", type=Path, required=True, help="AOI GeoPackage")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--max-cloud", type=float, default=10.0)
    parser.add_argument("--max-results", type=int, default=20)
    parser.add_argument("--resolution", type=int, default=20)
    parser.add_argument("--epsg", type=int, default=32630, help="Output CRS EPSG code")
    parser.add_argument("--savi-l", type=float, default=0.5)
    return parser


def main() -> None:
    """Console-script entry point (``s2-indices``)."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = _build_arg_parser().parse_args()
    run(
        aoi_path=args.aoi,
        date_start=args.start,
        date_end=args.end,
        output_dir=args.output_dir,
        max_cloud_cover=args.max_cloud,
        max_results=args.max_results,
        resolution=args.resolution,
        epsg=args.epsg,
        savi_l=args.savi_l,
    )


if __name__ == "__main__":
    main()
