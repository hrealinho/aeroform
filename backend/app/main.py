from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.db.session import Base, engine
from app.api.routes import router
import app.domain.models  # noqa: F401

Base.metadata.create_all(engine)
app = FastAPI(title="Aeroform API", version="0.5.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(router)
