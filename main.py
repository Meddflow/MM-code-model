"""
MMM 2023 Address Lookup API
----------------------------
FastAPI endpoint: POST /mmm  →  { address } → MM category

Setup:
    pip install fastapi uvicorn geopandas shapely requests

Run:
    uvicorn main:app --reload --port 8000

Then hit:
    POST http://localhost:8000/mmm
    Content-Type: application/json
    { "address": "350 Collins Street Melbourne VIC" }
"""

import time
from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from shapely.geometry import Point

# ── Config ────────────────────────────────────────────────────────────────────

# Update this path to wherever your MMM_2023b folder lives
SHP_PATH = Path(__file__).parent / "data" / "MMM2023.shp"

MMM_LABELS = {
    1: "Major city",
    2: "Regional centre",
    3: "Large rural town",
    4: "Medium rural town",
    5: "Small rural town",
    6: "Remote community",
    7: "Very remote community",
}

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="MMM 2023 Address Lookup",
    description="Send an Australian address, get back its Modified Monash Model category (MM 1–7).",
    version="1.0.0",
)

# ── Shapefile (loaded once at startup) ───────────────────────────────────────

@lru_cache(maxsize=1)
def get_gdf() -> tuple[gpd.GeoDataFrame, str]:
    """Load shapefile once and cache it. Returns (gdf, mmm_column_name)."""
    if not SHP_PATH.exists():
        raise RuntimeError(
            f"Shapefile not found at {SHP_PATH}. "
            "Update SHP_PATH in main.py to point to your MMM2023.shp file."
        )
    gdf = gpd.read_file(SHP_PATH)
    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)

    # Auto-detect the MMM column
    mmm_col = next(
        (c for c in gdf.columns if "mmm" in c.lower()),
        None,
    )
    if mmm_col is None:
        raise RuntimeError(
            f"Could not find MMM column in shapefile. Columns: {list(gdf.columns)}"
        )
    return gdf, mmm_col


@app.on_event("startup")
async def startup():
    """Pre-load the shapefile so the first request isn't slow."""
    get_gdf()
    print("✓ MMM 2023 shapefile loaded.")


# ── Schemas ───────────────────────────────────────────────────────────────────

class AddressRequest(BaseModel):
    address: str

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"address": "350 Collins Street Melbourne VIC 3000"},
                {"address": "1 Main Street Broken Hill NSW 2880"},
            ]
        }
    }


class MMMLookupResponse(BaseModel):
    address: str
    mmm: int
    label: str
    description: str
    lat: float
    lon: float


# ── Helpers ───────────────────────────────────────────────────────────────────

def geocode(address: str) -> tuple[float, float]:
    """Geocode via Nominatim. Returns (lat, lon)."""
    resp = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": address, "countrycodes": "au", "format": "json", "limit": 1},
        headers={"User-Agent": "mmm-api/1.0"},
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise ValueError(f"Address not found: {address!r}")
    return float(results[0]["lat"]), float(results[0]["lon"])


def point_in_polygon_mmm(lat: float, lon: float) -> int:
    """Find the MMM category for a lat/lon point."""
    gdf, mmm_col = get_gdf()
    point = Point(lon, lat)
    matches = gdf[gdf.geometry.contains(point)]
    if matches.empty:
        raise ValueError(
            f"Coordinates ({lat:.5f}, {lon:.5f}) are outside all MMM polygons. "
            "Check the address is within Australia."
        )
    return int(matches.iloc[0][mmm_col])


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.post("/mmm", response_model=MMMLookupResponse, summary="Get MMM category for an address")
def get_mmm(req: AddressRequest):
    """
    Send an Australian address and receive its MMM 2023 classification.

    - **address**: Free-text Australian address string

    Returns MM 1 (major city) through MM 7 (very remote).
    """
    try:
        lat, lon = geocode(req.address)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Geocoding service error: {e}")

    try:
        mmm_val = point_in_polygon_mmm(lat, lon)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return MMMLookupResponse(
        address=req.address,
        mmm=mmm_val,
        label=f"MM {mmm_val}",
        description=MMM_LABELS.get(mmm_val, "Unknown"),
        lat=lat,
        lon=lon,
    )


@app.get("/health", summary="Health check")
def health():
    return {"status": "ok"}


@app.get("/", summary="API info")
def root():
    return {
        "name": "MMM 2023 Address Lookup API",
        "usage": "POST /mmm with JSON body: { \"address\": \"your address here\" }",
        "docs": "/docs",
    }