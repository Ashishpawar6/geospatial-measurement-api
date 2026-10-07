import io
import zipfile

import pytest
import shapely
from pyproj import Geod
from shapely.geometry import LineString, Point, box

from tests.conftest import (
    kml, kml_line, kml_multigeometry, kml_point, kml_polygon, shapefile_zip, square, upload,
)

GEOD = Geod(ellps="WGS84")


def sample_kml():
    return kml(
        kml_polygon("Plot A", [(*p, 0) for p in square(77.0, 28.0, 0.01)]),
        kml_line("Road", [(77.0, 28.0), (77.05, 28.05)]),
        kml_point("Gate", (77.0, 28.0)),
    )


# ---- upload + file info ---------------------------------------------------------------------

def test_upload_kml_returns_file_info(client):
    resp = upload(client, "survey.kml", sample_kml())
    assert resp.status_code == 201
    body = resp.json()
    assert body["filename"] == "survey.kml"
    assert body["feature_count"] == 3
    assert body["crs"] == "EPSG:4326"
    assert body["status"] == "COMPLETED"


def test_get_file_info_matches_upload(client):
    created = upload(client, "survey.kml", sample_kml()).json()
    resp = client.get(f"/api/files/{created['id']}/")
    assert resp.status_code == 200
    assert resp.json() == created


def test_unknown_file_is_404(client):
    assert client.get("/api/files/nope/").status_code == 404
    assert client.get("/api/files/nope/measurements/").status_code == 404


def test_upload_shapefile_zip(client):
    data = shapefile_zip([box(77.0, 28.0, 77.01, 28.01), box(77.1, 28.1, 77.11, 28.11)])
    resp = upload(client, "parcels.zip", data)
    assert resp.status_code == 201
    assert resp.json()["feature_count"] == 2
    assert resp.json()["crs"] == "EPSG:4326"


# ---- feature extraction ---------------------------------------------------------------------

def test_features_expose_index_type_geometry_crs_properties(client):
    fid = upload(client, "survey.kml", sample_kml()).json()["id"]
    features = client.get(f"/api/files/{fid}/measurements/").json()["features"]
    assert [f["index"] for f in features] == [0, 1, 2]
    assert [f["geometry_type"] for f in features] == ["Polygon", "LineString", "Point"]
    assert all(f["crs"] == "EPSG:4326" for f in features)
    assert features[0]["properties"] == {"Name": "Plot A"}  # KML bookkeeping columns are dropped
    assert features[2]["geometry"]["type"] == "Point"
    assert features[2]["geometry"]["coordinates"] == [77.0, 28.0]


def test_shapefile_attributes_are_extracted(client):
    data = shapefile_zip([box(77.0, 28.0, 77.01, 28.01)], names=["North field"])
    fid = upload(client, "p.zip", data).json()["id"]
    feature = client.get(f"/api/files/{fid}/measurements/").json()["features"][0]
    assert feature["properties"] == {"name": "North field"}


# ---- measurements ---------------------------------------------------------------------------

def test_kml_measurements_use_projected_crs(client):
    fid = upload(client, "survey.kml", sample_kml()).json()["id"]
    f = client.get(f"/api/files/{fid}/measurements/").json()["features"]

    area = f[0]["measurement"]
    expected_area = abs(GEOD.geometry_area_perimeter(box(77.0, 28.0, 77.01, 28.01))[0])
    assert area["status"] == "MEASURED" and area["type"] == "area" and area["unit"] == "m2"
    assert area["projected_crs"] == "EPSG:32643"
    assert area["value"] == pytest.approx(expected_area, rel=0.005)

    length = f[1]["measurement"]
    expected_len = GEOD.geometry_length(LineString([(77.0, 28.0), (77.05, 28.05)]))
    assert length["type"] == "length" and length["unit"] == "m"
    assert length["value"] == pytest.approx(expected_len, rel=0.002)

    assert f[2]["measurement"]["status"] == "NOT_REQUIRED"
    assert f[2]["measurement"]["value"] is None


def test_shapefile_in_projected_crs_is_measured_in_metres(client):
    data = shapefile_zip([box(500000, 3100000, 500100, 3100100)], crs="EPSG:32643")
    fid = upload(client, "utm.zip", data).json()["id"]
    assert client.get(f"/api/files/{fid}/").json()["crs"] == "EPSG:32643"
    m = client.get(f"/api/files/{fid}/measurements/").json()["features"][0]["measurement"]
    assert m["value"] == pytest.approx(10_000, rel=1e-6)


def test_shapefile_lines_and_points(client):
    lines = shapefile_zip(
        [LineString([(77.0, 28.0), (77.0, 28.1)])], geometry_type="LineString"
    )
    fid = upload(client, "l.zip", lines).json()["id"]
    m = client.get(f"/api/files/{fid}/measurements/").json()["features"][0]["measurement"]
    assert m["type"] == "length" and m["value"] == pytest.approx(11_100, rel=0.02)

    points = shapefile_zip([Point(77.0, 28.0)], geometry_type="Point")
    fid = upload(client, "p.zip", points).json()["id"]
    m = client.get(f"/api/files/{fid}/measurements/").json()["features"][0]["measurement"]
    assert m["status"] == "NOT_REQUIRED"


def test_unsupported_geometry_is_handled_gracefully(client):
    data = kml(
        kml_multigeometry("Mixed", (77.0, 28.0), [(77.0, 28.0), (77.1, 28.1)]),
        kml_polygon("Plot", square(77.0, 28.0, 0.01)),
    )
    resp = upload(client, "mixed.kml", data)
    assert resp.status_code == 201
    f = client.get(f"/api/files/{resp.json()['id']}/measurements/").json()["features"]
    assert f[0]["measurement"]["status"] == "UNSUPPORTED"
    assert f[0]["measurement"]["note"]
    assert f[1]["measurement"]["status"] == "MEASURED"  # other features are unaffected


# ---- rejected / failed uploads --------------------------------------------------------------

def test_unsupported_extension_is_415(client):
    assert upload(client, "notes.txt", b"hello").status_code == 415
    assert upload(client, "geo.geojson", b"{}").status_code == 415


def test_empty_file_is_rejected(client):
    assert upload(client, "empty.kml", b"").status_code == 422


def test_oversized_upload_is_413(client):
    assert upload(client, "big.kml", b"x" * (1024 * 1024 + 1)).status_code == 413


def test_corrupt_kml_fails_with_recorded_status(client):
    resp = upload(client, "bad.kml", b"<kml><not closed")
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    info = client.get(f"/api/files/{detail['file_id']}/").json()
    assert info["status"] == "FAILED" and info["error"]
    assert client.get(f"/api/files/{detail['file_id']}/measurements/").status_code == 409


def test_zip_that_is_not_a_zip_fails(client):
    resp = upload(client, "fake.zip", b"definitely not a zip")
    assert resp.status_code == 422
    assert "zip" in resp.json()["detail"]["message"].lower()


def test_zip_without_shp_fails(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "hi")
    resp = upload(client, "empty.zip", buf.getvalue())
    assert resp.status_code == 422
    assert ".shp" in resp.json()["detail"]["message"]


def test_shapefile_without_crs_is_rejected_not_guessed(client):
    data = shapefile_zip([box(0, 0, 1, 1)], with_prj=False)
    resp = upload(client, "noprj.zip", data)
    assert resp.status_code == 422
    assert "CRS" in resp.json()["detail"]["message"]


def test_zip_slip_is_rejected(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.shp", "x")
    resp = upload(client, "evil.zip", buf.getvalue())
    assert resp.status_code == 422
    assert "unsafe" in resp.json()["detail"]["message"]


def test_kml_with_no_features_fails(client):
    resp = upload(client, "none.kml", kml())
    assert resp.status_code == 422
