import logging
import tempfile
from pathlib import Path

from shapely.geometry import mapping
from sqlalchemy.orm import Session

from app.measure import measure
from app.models import Feature, FileStatus, UploadedFile
from app.reader import ReaderError, crs_label, read_geospatial_file

logger = logging.getLogger(__name__)

KINDS = {".kml": "kml", ".zip": "shapefile"}


def ingest_file(db: Session, filename: str, kind: str, data: bytes) -> UploadedFile:
    """Parse, measure and persist an upload. Never raises: failures are recorded on the file."""
    record = UploadedFile(filename=filename, status=FileStatus.PROCESSING)
    db.add(record)
    db.commit()

    try:
        with tempfile.TemporaryDirectory(prefix="geo-upload-") as tmp:
            workdir = Path(tmp)
            path = workdir / ("upload.kml" if kind == "kml" else "upload.zip")
            path.write_bytes(data)
            parsed = read_geospatial_file(path, kind, workdir)

        rows = []
        for p in parsed:
            m = measure(p.geometry, p.crs)
            rows.append(
                Feature(
                    file_id=record.id,
                    index=p.index,
                    layer=p.layer,
                    geometry_type=p.geometry.geom_type if p.geometry is not None else None,
                    geometry=mapping(p.geometry) if p.geometry is not None else None,
                    crs=crs_label(p.crs),
                    properties=p.properties,
                    measurement_status=m.status,
                    measurement_type=m.type,
                    measurement_value=m.value,
                    measurement_unit=m.unit,
                    projected_crs=m.projected_crs,
                    measurement_note=m.note,
                )
            )
        db.add_all(rows)

        crs_values = {r.crs for r in rows}
        record.crs = crs_values.pop() if len(crs_values) == 1 else "MIXED"
        record.feature_count = len(rows)
        record.status = FileStatus.COMPLETED
    except ReaderError as exc:
        db.rollback()
        record.status, record.error = FileStatus.FAILED, str(exc)
    except Exception:
        logger.exception("Unexpected error processing %s", filename)
        db.rollback()
        record.status, record.error = FileStatus.FAILED, "Unexpected error while processing the file"

    db.add(record)
    db.commit()
    return record
