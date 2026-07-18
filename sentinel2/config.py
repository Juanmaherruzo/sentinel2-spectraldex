"""Shared constants and a lazy STAC catalogue accessor.

The Planetary Computer catalogue is a public STAC endpoint requiring no
credentials; COG asset URLs are auto-signed by the ``planetary_computer``
modifier. ``get_catalog`` is a function (not a module-level object) so importing
this package never triggers a network connection.
"""

from typing import Any

import planetary_computer
import pystac_client

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"
COLLECTION = "sentinel-2-l2a"

# Bands required to compute all six spectral indices.
# B02=Blue, B03=Green, B04=Red, B05=RedEdge1, B8A=NIR narrow, B11=SWIR1, B12=SWIR2
BANDS_NEEDED: list[str] = ["B02", "B03", "B04", "B05", "B8A", "B11", "B12"]


def get_catalog(stac_url: str = STAC_URL) -> Any:
    """Open the STAC catalogue with automatic Planetary Computer URL signing."""
    return pystac_client.Client.open(stac_url, modifier=planetary_computer.sign_inplace)
