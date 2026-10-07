from typing import Any

from pydantic import BaseModel

from app.models import FileStatus


class FileInfo(BaseModel):
    id: str
    filename: str
    feature_count: int
    crs: str | None
    status: FileStatus
    error: str | None = None


class MeasurementOut(BaseModel):
    status: str
    type: str | None = None
    value: float | None = None
    unit: str | None = None
    projected_crs: str | None = None
    note: str | None = None


class FeatureOut(BaseModel):
    index: int
    layer: str | None
    geometry_type: str | None
    geometry: dict[str, Any] | None
    crs: str
    properties: dict[str, Any]
    measurement: MeasurementOut


class MeasurementsOut(BaseModel):
    id: str
    filename: str
    status: FileStatus
    feature_count: int
    features: list[FeatureOut]
