import io
import os
import tempfile
import zipfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="geo-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp / 'test.db'}"
os.environ["MAX_UPLOAD_MB"] = "1"

import numpy as np  # noqa: E402
import pytest  # noqa: E402
import shapely  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pyogrio.raw import write as ogr_write  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ---- fixture builders ----------------------------------------------------------------------

def kml(*placemarks: str) -> bytes:
    body = "".join(placemarks)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>' + body + "</Document></kml>"
    ).encode()


def _coords(points) -> str:
    return " ".join(",".join(str(v) for v in p) for p in points)


def kml_polygon(name, points) -> str:
    return (
        f"<Placemark><name>{name}</name><Polygon><outerBoundaryIs><LinearRing>"
        f"<coordinates>{_coords(points)}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
    )


def kml_line(name, points) -> str:
    return f"<Placemark><name>{name}</name><LineString><coordinates>{_coords(points)}</coordinates></LineString></Placemark>"


def kml_point(name, point) -> str:
    return f"<Placemark><name>{name}</name><Point><coordinates>{_coords([point])}</coordinates></Point></Placemark>"


def kml_multigeometry(name, point, line) -> str:
    return (
        f"<Placemark><name>{name}</name><MultiGeometry>"
        f"<Point><coordinates>{_coords([point])}</coordinates></Point>"
        f"<LineString><coordinates>{_coords(line)}</coordinates></LineString>"
        "</MultiGeometry></Placemark>"
    )


def square(lon, lat, size):
    return [(lon, lat), (lon + size, lat), (lon + size, lat + size), (lon, lat + size), (lon, lat)]


def shapefile_zip(geoms, crs="EPSG:4326", geometry_type="Polygon", names=None, with_prj=True) -> bytes:
    """Write a real Shapefile with GDAL and return it zipped."""
    names = names or [f"feature-{i}" for i in range(len(geoms))]
    with tempfile.TemporaryDirectory() as d:
        ogr_write(
            Path(d) / "data.shp",
            geometry=[shapely.to_wkb(g) for g in geoms],
            field_data=[np.array(names, dtype=object)],
            fields=["name"],
            geometry_type=geometry_type,
            crs=crs if with_prj else None,
            driver="ESRI Shapefile",
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for p in sorted(Path(d).iterdir()):
                zf.write(p, p.name)
        return buf.getvalue()


def upload(client, filename, data, content_type="application/octet-stream"):
    return client.post("/api/files/", files={"file": (filename, data, content_type)})
