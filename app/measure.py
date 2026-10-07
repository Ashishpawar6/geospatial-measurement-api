"""Measurement of area/length using a projected CRS.

Pipeline for each measurable feature:
    source CRS --> WGS84 lon/lat --> pick a UTM zone from the feature centroid --> measure in metres
Degrees are never used to compute area or distance.
"""
import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import shapely
from pyproj import CRS, Transformer
from shapely.geometry.base import BaseGeometry

WGS84 = CRS.from_epsg(4326)
POLYGONS = {"Polygon", "MultiPolygon"}
LINES = {"LineString", "MultiLineString", "LinearRing"}
POINTS = {"Point", "MultiPoint"}

# status values
MEASURED = "MEASURED"
NOT_REQUIRED = "NOT_REQUIRED"  # points
UNSUPPORTED = "UNSUPPORTED"  # e.g. GeometryCollection
SKIPPED = "SKIPPED"  # missing / empty geometry
ERROR = "ERROR"


@dataclass(frozen=True)
class Measurement:
    status: str
    type: str | None = None
    value: float | None = None
    unit: str | None = None
    projected_crs: str | None = None
    note: str | None = None


def utm_epsg(lon: float, lat: float) -> int:
    """EPSG code of the UTM zone containing (lon, lat); UPS for the polar caps."""
    if lat >= 84:
        return 32661  # WGS 84 / UPS North
    if lat < -80:
        return 32761  # WGS 84 / UPS South
    lon = ((lon + 180) % 360) - 180
    zone = min(int((lon + 180) // 6) + 1, 60)
    return (32600 if lat >= 0 else 32700) + zone


@lru_cache(maxsize=256)
def _transformer(src: CRS, dst: CRS) -> Transformer:
    return Transformer.from_crs(src, dst, always_xy=True)


def _reproject(geom: BaseGeometry, transformer: Transformer) -> BaseGeometry:
    def apply(coords: np.ndarray) -> np.ndarray:
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack((x, y))

    return shapely.transform(geom, apply)


def _round(value: float) -> float:
    return round(value, 4)


def measure(geom: BaseGeometry | None, src_crs: CRS) -> Measurement:
    if geom is None or geom.is_empty:
        return Measurement(SKIPPED, note="Feature has no geometry")

    gtype = geom.geom_type
    if gtype in POINTS:
        return Measurement(NOT_REQUIRED)
    if gtype in POLYGONS:
        kind, unit = "area", "m2"
    elif gtype in LINES:
        kind, unit = "length", "m"
    else:
        return Measurement(UNSUPPORTED, note=f"Measurement is not supported for {gtype}")

    try:
        flat = shapely.force_2d(geom)
        lonlat = _reproject(flat, _transformer(src_crs, WGS84))
        centroid = lonlat.centroid
        if not (math.isfinite(centroid.x) and math.isfinite(centroid.y)):
            return Measurement(ERROR, note="Coordinates could not be transformed to WGS84")

        target = CRS.from_epsg(utm_epsg(centroid.x, centroid.y))
        projected = _reproject(lonlat, _transformer(WGS84, target))
        value = projected.area if kind == "area" else projected.length
        if not math.isfinite(value):
            return Measurement(ERROR, note="Measurement produced a non-finite value")
        return Measurement(MEASURED, kind, _round(value), unit, f"EPSG:{target.to_epsg()}")
    except Exception as exc:  # one odd feature must not fail the whole file
        return Measurement(ERROR, note=f"Measurement failed: {exc}")
