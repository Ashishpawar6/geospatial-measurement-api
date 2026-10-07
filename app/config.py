import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/geo.db")
MAX_UPLOAD_MB = float(os.getenv("MAX_UPLOAD_MB", "25"))
MAX_UNZIPPED_MB = float(os.getenv("MAX_UNZIPPED_MB", "200"))
MAX_ZIP_ENTRIES = int(os.getenv("MAX_ZIP_ENTRIES", "2000"))
