import pytest
from pyproj import CRS, Geod
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Point, Polygon, box

from app.measure import ERROR, MEASURED, NOT_REQUIRED, SKIPPED, UNSUPPORTED, measure, utm_epsg

WGS84 = CRS.from_epsg(4326)
GEOD = Geod(ellps="WGS84")


@pytest.mark.parametrize(
    "lon,lat,expected",
    [
        (77.0, 28.0, 32643),    # northern India
        (151.2, -33.8, 32756),  # Sydney, southern hemisphere
        (-0.1, 51.5, 32630),    # London
        (-179.9, 10, 32601),    # western edge
        (180.0, 10, 32601),     # antimeridian wraps
        (0, 85, 32661),         # UPS north
        (0, -85, 32761),        # UPS south
    ],
)
def test_utm_epsg(lon, lat, expected):
    assert utm_epsg(lon, lat) == expected


def test_polygon_area_matches_geodesic_area_not_degrees():
    poly = box(77.0, 28.0, 77.01, 28.01)
    m = measure(poly, WGS84)
    geodesic = abs(GEOD.geometry_area_perimeter(poly)[0])
    assert m.status == MEASURED and m.type == "area" and m.unit == "m2"
    assert m.projected_crs == "EPSG:32643"
    assert m.value == pytest.approx(geodesic, rel=0.005)
    assert m.value > 1e5  # square metres, not ~1e-4 square degrees


def test_line_length_matches_geodesic_length():
    line = LineString([(77.0, 28.0), (77.05, 28.05)])
    m = measure(line, WGS84)
    expected = GEOD.geometry_length(line)
    assert m.type == "length" and m.unit == "m"
    assert m.value == pytest.approx(expected, rel=0.002)


def test_multipolygon_area_is_sum_of_parts():
    a, b = box(77.0, 28.0, 77.01, 28.01), box(77.02, 28.0, 77.03, 28.01)
    total = measure(MultiPolygon([a, b]), WGS84).value
    assert total == pytest.approx(measure(a, WGS84).value + measure(b, WGS84).value, rel=1e-6)


def test_polygon_with_hole_subtracts_hole():
    outer = [(77.0, 28.0), (77.02, 28.0), (77.02, 28.02), (77.0, 28.02)]
    hole = [(77.005, 28.005), (77.015, 28.005), (77.015, 28.015), (77.005, 28.015)]
    with_hole = measure(Polygon(outer, [hole]), WGS84).value
    solid = measure(Polygon(outer), WGS84).value
    assert with_hole < solid


def test_z_coordinates_are_ignored():
    flat = measure(box(77.0, 28.0, 77.01, 28.01), WGS84).value
    z = Polygon([(77.0, 28.0, 100), (77.01, 28.0, 100), (77.01, 28.01, 100), (77.0, 28.01, 100)])
    assert measure(z, WGS84).value == pytest.approx(flat)


def test_projected_source_crs_is_handled():
    utm43 = CRS.from_epsg(32643)
    m = measure(box(500000, 3100000, 500100, 3100100), utm43)  # 100 m x 100 m
    assert m.value == pytest.approx(10_000, rel=1e-6)


def test_point_needs_no_measurement():
    assert measure(Point(77, 28), WGS84).status == NOT_REQUIRED


def test_unsupported_geometry_is_reported_not_raised():
    m = measure(GeometryCollection([Point(0, 0), LineString([(0, 0), (1, 1)])]), WGS84)
    assert m.status == UNSUPPORTED and "GeometryCollection" in m.note


def test_missing_and_empty_geometry_are_skipped():
    assert measure(None, WGS84).status == SKIPPED
    assert measure(Polygon(), WGS84).status == SKIPPED


def test_untransformable_coordinates_become_error_not_crash():
    m = measure(box(1e12, 1e12, 1e12 + 1, 1e12 + 1), WGS84)
    assert m.status == ERROR
