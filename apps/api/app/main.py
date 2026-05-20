from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.modules.artifacts.router import router as artifacts_router
from app.modules.datasets.router import router as datasets_router
from app.modules.files.router import router as files_router

app = FastAPI(title="SceneOps Drive API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(datasets_router, prefix="/api/v1")
app.include_router(artifacts_router, prefix="/api/v1")
app.include_router(files_router, prefix="/api/v1")


@app.get("/health")
def health_check():
    return {"status": "ok"}
