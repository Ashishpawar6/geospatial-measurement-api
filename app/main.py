from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.database import Base, engine
from app.routers import router


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Geospatial File Measurement API", version="1.0.0", lifespan=lifespan)
app.include_router(router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
