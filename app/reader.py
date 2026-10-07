"""Read Shapefile (.zip) and KML files into plain feature records."""
import math
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import numpy as np
import pyogrio
import shapely
from pyogrio import raw as ogr_raw
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app import config

# Bookkeeping columns GDAL's KML driver adds to every feature; they are noise as "properties".
KML_NOISE = {
    "id", "timestamp", "begin", "end", "altitudeMode",
    "tessellate", "extrude", "visibility", "drawOrder", "icon",
}


class ReaderError(Exception):
    """The file is unreadable or unusable; the message is safe to show to the client."""


@dataclass
class ParsedFeature:
    index: int
    layer: str | None
    geometry: BaseGeometry | None
    crs: CRS
    properties: dict[str, Any] = field(default_factory=dict)


def crs_label(crs: CRS) -> str:
    epsg = crs.to_epsg()
    return f"EPSG:{epsg}" if epsg else (crs.name or "UNKNOWN")


def _clean(value: Any) -> Any:
    """Convert a GDAL field value to something JSON-safe, or None to drop it."""
    if value is None:
        return None
    if isinstance(value, np.datetime64):
        return None if np.isnat(value) else str(value)
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _safe_extract(zip_path: Path, dest: Path) -> None:
    max_bytes = config.MAX_UNZIPPED_MB * 1024 * 1024
    try:
        with zipfile.ZipFile(zip_path) as zf:
            infos = zf.infolist()
            if len(infos) > config.MAX_ZIP_ENTRIES:
                raise ReaderError(f"Archive has too many entries (limit {config.MAX_ZIP_ENTRIES})")
            if sum(i.file_size for i in infos) > max_bytes:
                raise ReaderError(f"Archive expands beyond {config.MAX_UNZIPPED_MB:g} MB")
            root = dest.resolve()
            for info in infos:
                name = PurePosixPath(info.filename.replace("\\", "/"))
                if info.is_dir() or "__MACOSX" in name.parts or name.name.startswith("._"):
                    continue
                if name.is_absolute() or ".." in name.parts:
                    raise ReaderError("Archive contains an unsafe path")
                target = (root / name).resolve()
                if root not in target.parents:
                    raise ReaderError("Archive contains an unsafe path")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    out.write(src.read())
    except zipfile.BadZipFile as exc:
        raise ReaderError("File is not a valid zip archive") from exc


def _read_layers(path: Path, kind: str) -> list[ParsedFeature]:
    try:
        layers = [row[0] for row in pyogrio.list_layers(path)]
    except Exception as exc:
        raise ReaderError(f"Could not read file as {kind}: {exc}") from exc

    features: list[ParsedFeature] = []
    for layer in layers:
        try:
            meta, _fids, geometries, field_data = ogr_raw.read(path, layer=layer)
        except Exception as exc:
            raise ReaderError(f"Could not read layer '{layer}': {exc}") from exc
        if len(geometries) == 0:
            continue

        if meta.get("crs"):
            try:
                crs = CRS.from_user_input(meta["crs"])
            except Exception as exc:
                raise ReaderError(f"Unrecognised CRS in layer '{layer}'") from exc
        elif kind == "kml":
            crs = CRS.from_epsg(4326)  # the KML spec mandates WGS84 lon/lat
        else:
            raise ReaderError(
                f"Layer '{layer}' has no CRS (a Shapefile needs a .prj file); "
                "refusing to guess because measurements would be meaningless"
            )

        names = [str(n) for n in meta["fields"]]
        drop = KML_NOISE if kind == "kml" else set()
        for i, wkb in enumerate(geometries):
            props = {}
            for name, column in zip(names, field_data):
                if name in drop:
                    continue
                value = _clean(column[i])
                if value is not None:
                    props[name] = value
            features.append(
                ParsedFeature(
                    index=len(features),
                    layer=layer,
                    geometry=shapely.from_wkb(wkb) if wkb is not None else None,
                    crs=crs,
                    properties=props,
                )
            )
    return features


def read_geospatial_file(path: Path, kind: str, workdir: Path) -> list[ParsedFeature]:
    """kind is 'kml' or 'shapefile' (a zip containing one or more .shp files)."""
    if kind == "kml":
        features = _read_layers(path, kind)
    else:
        extract_dir = workdir / "unzipped"
        extract_dir.mkdir()
        _safe_extract(path, extract_dir)
        shapefiles = sorted(p for p in extract_dir.rglob("*") if p.suffix.lower() == ".shp")
        if not shapefiles:
            raise ReaderError("Zip archive does not contain a .shp file")
        features = []
        for shp in shapefiles:
            for feat in _read_layers(shp, kind):
                feat.index = len(features)
                feat.layer = shp.stem
                features.append(feat)
    if not features:
        raise ReaderError("File contains no features")
    return features
