"""Area-of-interest loading and interactive preview."""

import json
import logging
from pathlib import Path
from typing import Any

import folium
import geopandas as gpd

logger = logging.getLogger(__name__)


def load_aoi(gpkg_path: Path, layer_name: str | None = None) -> dict[str, Any]:
    """Load a GeoPackage, reproject to EPSG:4326 and dissolve to one polygon.

    Returns the geometry metadata used by every downstream stage: the STAC bbox
    ``[W, S, E, N]``, a WKT string, the dissolved GeoJSON, bounds and area (ha).
    """
    gdf = gpd.read_file(gpkg_path, layer=layer_name)
    logger.info("AOI: %d features, CRS %s", len(gdf), gdf.crs)

    if gdf.crs is None:
        raise ValueError("The GeoPackage has no CRS defined. Assign one first.")
    if gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)
        logger.info("Reprojected AOI to EPSG:4326")

    aoi_dissolved = gdf.dissolve()
    aoi_geom = aoi_dissolved.geometry.iloc[0]
    area_ha = gdf.to_crs(epsg=3857).dissolve().geometry.area.iloc[0] / 10_000
    bounds = aoi_geom.bounds  # (minx, miny, maxx, maxy)

    if aoi_geom.geom_type == "MultiPolygon":
        aoi_geom = max(aoi_geom.geoms, key=lambda g: g.area)
        logger.info("MultiPolygon detected - using the largest polygon")

    wkt_coords = ", ".join(f"{x} {y}" for x, y in aoi_geom.exterior.coords)
    logger.info("AOI bbox (lon/lat): %s | area: %.2f ha", list(bounds), area_ha)

    return {
        "wkt": f"POLYGON(({wkt_coords}))",
        "bbox_stac": [bounds[0], bounds[1], bounds[2], bounds[3]],
        "geojson": json.loads(aoi_dissolved.to_json()),
        "bounds": bounds,
        "area_ha": round(area_ha, 2),
        "crs_original": str(gdf.crs),
        "n_features": len(gdf),
        "gdf": gdf,
    }


def preview_aoi(aoi_info: dict[str, Any]) -> folium.Map:
    """Render the AOI on an interactive Esri satellite basemap."""
    bounds = aoi_info["bounds"]
    center_lat = (bounds[1] + bounds[3]) / 2
    center_lon = (bounds[0] + bounds[2]) / 2

    fmap = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=13,
        tiles=(
            "https://server.arcgisonline.com/ArcGIS/rest/services"
            "/World_Imagery/MapServer/tile/{z}/{y}/{x}"
        ),
        attr="Esri World Imagery",
    )
    folium.GeoJson(
        aoi_info["geojson"],
        name="AOI",
        style_function=lambda _: {
            "fillColor": "#00ff88",
            "color": "#00cc66",
            "weight": 2,
            "fillOpacity": 0.2,
        },
    ).add_to(fmap)
    folium.Marker(
        location=[center_lat, center_lon],
        popup=folium.Popup(
            f"<b>Area:</b> {aoi_info['area_ha']} ha<br>"
            f"<b>Original CRS:</b> {aoi_info['crs_original']}<br>"
            f"<b>Features:</b> {aoi_info['n_features']}",
            max_width=250,
        ),
        icon=folium.Icon(color="green", icon="leaf"),
    ).add_to(fmap)
    folium.LayerControl().add_to(fmap)
    return fmap
