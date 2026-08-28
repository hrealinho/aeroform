from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.db.session import Base, engine
from app.api.routes import router
from app.domain import models as _models  # noqa: F401  (import registers the ORM tables)

Base.metadata.create_all(engine)
# Single source of truth for the version, so /health and the OpenAPI schema agree.
app = FastAPI(title=f"{settings.app_name} API", version=settings.app_version)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(router)
