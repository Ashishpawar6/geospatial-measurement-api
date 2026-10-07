from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app import config
from app.database import get_db
from app.models import FileStatus, UploadedFile
from app.schemas import FeatureOut, FileInfo, MeasurementOut, MeasurementsOut
from app.service import KINDS, ingest_file

router = APIRouter(prefix="/api/files", tags=["files"])

CHUNK = 1024 * 1024


def _get_file_or_404(db: Session, file_id: str) -> UploadedFile:
    record = db.get(UploadedFile, file_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return record


def _info(record: UploadedFile) -> FileInfo:
    return FileInfo(
        id=record.id,
        filename=record.filename,
        feature_count=record.feature_count,
        crs=record.crs,
        status=record.status,
        error=record.error,
    )


@router.post("/", response_model=FileInfo, status_code=status.HTTP_201_CREATED)
def upload_file(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Upload a .kml or a .zip containing a Shapefile. Processed synchronously."""
    filename = Path(file.filename or "").name
    kind = KINDS.get(Path(filename).suffix.lower())
    if kind is None:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only .kml and .zip (containing a Shapefile) are supported"
        )

    limit = int(config.MAX_UPLOAD_MB * 1024 * 1024)
    chunks, size = [], 0
    while chunk := file.file.read(CHUNK):
        size += len(chunk)
        if size > limit:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE, f"File exceeds the {config.MAX_UPLOAD_MB:g} MB limit"
            )
        chunks.append(chunk)
    if size == 0:
        raise HTTPException(422, "Uploaded file is empty")

    record = ingest_file(db, filename, kind, b"".join(chunks))
    if record.status == FileStatus.FAILED:
        raise HTTPException(422, {"message": record.error, "file_id": record.id})
    return _info(record)


@router.get("/{file_id}/", response_model=FileInfo)
def get_file(file_id: str, db: Session = Depends(get_db)):
    return _info(_get_file_or_404(db, file_id))


@router.get("/{file_id}/measurements/", response_model=MeasurementsOut)
def get_measurements(file_id: str, db: Session = Depends(get_db)):
    record = _get_file_or_404(db, file_id)
    if record.status != FileStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT, f"File is not available (status: {record.status.value})")
    return MeasurementsOut(
        id=record.id,
        filename=record.filename,
        status=record.status,
        feature_count=record.feature_count,
        features=[
            FeatureOut(
                index=f.index,
                layer=f.layer,
                geometry_type=f.geometry_type,
                geometry=f.geometry,
                crs=f.crs,
                properties=f.properties,
                measurement=MeasurementOut(
                    status=f.measurement_status,
                    type=f.measurement_type,
                    value=f.measurement_value,
                    unit=f.measurement_unit,
                    projected_crs=f.projected_crs,
                    note=f.measurement_note,
                ),
            )
            for f in record.features
        ],
    )
