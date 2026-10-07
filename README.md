# Geospatial File Measurement API

A FastAPI service that accepts a geospatial file (a **zipped Shapefile** or a **KML**), extracts every feature, and returns the **area** of polygons and the **length** of lines, calculated in a projected coordinate system, never in degrees.

**Stack:** Python 3.11+, FastAPI, SQLAlchemy + SQLite, pyogrio (GDAL), Shapely, pyproj, pytest.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

The API runs at http://127.0.0.1:8000 and interactive docs are at http://127.0.0.1:8000/docs. The SQLite database is created under `./data/`.
`pyogrio`, `shapely` and `pyproj` ship binary wheels with GDAL/PROJ bundled, so no system GDAL install is needed.

Run the tests with `pytest`.

Optional environment variables: `DATABASE_URL` (default `sqlite:///./data/geo.db`), `MAX_UPLOAD_MB` (25), `MAX_UNZIPPED_MB` (200), `MAX_ZIP_ENTRIES` (2000).

## API

### `POST /api/files/` Upload

Multipart upload with a `file` field: a `.kml`, or a `.zip` containing a Shapefile (`.shp`, `.shx`, `.dbf`, `.prj`).

```bash
curl -F "file=@survey.kml" http://127.0.0.1:8000/api/files/
```

`201 Created`

```json
{"id": "9b1c...e2", "filename": "survey.kml", "feature_count": 3, "crs": "EPSG:4326", "status": "COMPLETED", "error": null}
```

| Status | When |
|---|---|
| `415` | Extension is not `.kml` or `.zip` |
| `413` | Upload exceeds `MAX_UPLOAD_MB` |
| `422` | Empty, corrupt or unusable file (not a zip, no `.shp` inside, Shapefile without `.prj`, unsafe zip paths, no features). The response includes a `file_id`; that file is stored with `status: FAILED` and the reason in `error`. |

### `GET /api/files/{id}/` File information

Returns the same object as the upload response. `crs` is `"MIXED"` if the file has layers in different CRSs.

### `GET /api/files/{id}/measurements/` Features and measurements

```json
{
  "id": "9b1c...e2",
  "filename": "survey.kml",
  "status": "COMPLETED",
  "feature_count": 3,
  "features": [
    {
      "index": 0,
      "layer": "survey",
      "geometry_type": "Polygon",
      "geometry": {"type": "Polygon", "coordinates": [[[77.0, 28.0, 0.0], "..."]]},
      "crs": "EPSG:4326",
      "properties": {"Name": "Plot A"},
      "measurement": {"status": "MEASURED", "type": "area", "value": 1090165.1617, "unit": "m2", "projected_crs": "EPSG:32643", "note": null}
    },
    {
      "index": 1, "layer": "survey", "geometry_type": "LineString", "crs": "EPSG:4326", "properties": {"Name": "Road"},
      "geometry": {"type": "LineString", "coordinates": [[77.0, 28.0], [77.05, 28.05]]},
      "measurement": {"status": "MEASURED", "type": "length", "value": 7408.7019, "unit": "m", "projected_crs": "EPSG:32643", "note": null}
    },
    {
      "index": 2, "layer": "survey", "geometry_type": "Point", "crs": "EPSG:4326", "properties": {"Name": "Gate"},
      "geometry": {"type": "Point", "coordinates": [77.0, 28.0]},
      "measurement": {"status": "NOT_REQUIRED", "type": null, "value": null, "unit": null, "projected_crs": null, "note": null}
    }
  ]
}
```

Returns `404` for an unknown id and `409` if the file failed processing.

Measurement `status` values:

| Status | Meaning |
|---|---|
| `MEASURED` | Polygon/MultiPolygon → area in m²; LineString/MultiLineString → length in m |
| `NOT_REQUIRED` | Point/MultiPoint |
| `UNSUPPORTED` | Any other geometry (e.g. a KML `MultiGeometry` mixing types). Reported with a `note`, never a crash. |
| `SKIPPED` | Feature has no/empty geometry |
| `ERROR` | Reprojection or measurement failed for this feature only |

## Architecture

```
app/
  main.py      FastAPI app, creates tables on startup
  routers.py   HTTP layer: upload validation, size limits, response shaping
  service.py   Orchestrates: parse -> measure -> persist (never raises; records FAILED)
  reader.py    Shapefile/KML parsing via GDAL (pyogrio), safe zip extraction
  measure.py   CRS handling and area/length calculation (pure functions)
  models.py    UploadedFile and Feature tables
  schemas.py   Response models
```

**File-processing flow**

1. `routers.upload_file` checks the extension, reads the body in chunks enforcing the size limit, and rejects empty uploads.
2. `service.ingest_file` creates an `UploadedFile` row (`PROCESSING`) and writes the bytes to a temporary directory.
3. `reader` safely extracts a zip (rejecting path traversal, too many entries, or an excessive uncompressed size), finds every `.shp`, and reads each layer with GDAL. KML layers are read the same way. The temp directory is deleted afterwards.
4. For each feature it captures the index, layer, geometry type, geometry (as GeoJSON), CRS and properties. GDAL's KML bookkeeping columns (`tessellate`, `extrude`, …) and null values are dropped.
5. Each feature is measured, then all features are saved and the file is marked `COMPLETED`. Any `ReaderError` marks it `FAILED` with a client-safe message.

**Measurement flow** (`measure.measure`)

1. Points → `NOT_REQUIRED`; polygons → area; lines → length; anything else → `UNSUPPORTED`.
2. Z values are dropped; only planar 2D area/length is measured.
3. The geometry is converted from its source CRS to WGS84 lon/lat.
4. The UTM zone is chosen from the feature's centroid (`326xx` north, `327xx` south, UPS for the polar caps).
5. The geometry is reprojected to that UTM zone, and Shapely's `.area` / `.length` give metres.

**CRS handling**

- The CRS is read from the file: `.prj` for Shapefiles, and KML is always WGS84 lon/lat by specification.
- A Shapefile with no `.prj` is **rejected** with a clear message instead of assuming WGS84. A wrong guess would silently produce wrong numbers.
- Source CRSs that are already projected are supported (the pipeline always goes via WGS84), so a file in EPSG:32643 gives the same answer as the same shape in EPSG:4326.
- Axis order is forced to x=lon, y=lat (`always_xy=True`) to avoid the classic EPSG:4326 lat/lon swap.

## Design decisions

- **Synchronous processing.** Files are capped at 25 MB, and parsing plus measuring typically takes well under a second, so a single `POST` returns the finished result. The route is a plain `def`, so FastAPI runs it in a worker thread and the event loop isn't blocked. For very large files, the `PROCESSING` status already exists to support an async job model; the work in `service.ingest_file` could move to a queue unchanged.
- **UTM zone per feature.** UTM is accurate to better than ~0.1% inside a zone, widely understood, and needs no lookup tables. Choosing the zone per feature (not per file) keeps files spanning several zones accurate. *Alternatives considered:* a per-feature Lambert azimuthal equal-area projection (exact area, worse for length), and geodesic calculation with `pyproj.Geod` (most accurate, but the brief asks for a projected CRS). The tests use `Geod` as an independent check on the UTM results.
- **Features stored in the database.** The original upload is not kept. Features (GeoJSON geometry, properties, measurement) are stored, so `GET` endpoints are cheap and need no re-parsing.
- **GDAL via pyogrio for both formats.** One reader handles Shapefile and KML, and unlike a hand-rolled KML parser it copes with real-world files. Chosen over `fiona`/`geopandas` for a lighter dependency set and wheels that bundle GDAL.
- **Failure is explicit.** Unsupported geometries, empty geometries and per-feature errors are reported in the response per feature, and one bad feature never fails the file. Unusable files are stored as `FAILED` with a reason.
- **Untrusted input.** Zip entries are checked for path traversal, entry count and total uncompressed size; uploads are size-limited while streaming; files are processed in a temporary directory.

## Learning

- CRS bugs are silent: a degree-based area looks plausible but is meaningless, so the tests compare against an independent geodesic calculation instead of hard-coded numbers.
- Axis order differs between EPSG definitions and GIS practice; forcing `always_xy` removes a whole class of errors.
- Zip uploads need defensive handling (zip slip, decompression bombs) just like any file upload.
- GDAL's KML driver adds many bookkeeping columns that must be filtered to return useful properties.

## Future scope

- Asynchronous processing with a job queue and progress tracking for very large files.
- Pagination (and optional geometry omission) on the measurements endpoint for files with many features.
- More formats: GeoJSON, GeoPackage, KMZ.
- Geodesic measurements as an option, and a smarter projection choice for features spanning several UTM zones.
- Perimeter for polygons, and summary totals per file or layer.
- Authentication, per-user files, and PostgreSQL/PostGIS for spatial queries.
