from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.modules.artifacts.router import router as artifacts_router
from app.modules.datasets.router import router as datasets_router
from app.modules.files.router import router as files_router
from app.modules.runs.router import router as runs_router
from app.modules.evaluations.router import router as evaluations_router
from app.modules.jobs.router import router as jobs_router
from app.modules.pipelines.router import router as pipelines_router

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
app.include_router(evaluations_router, prefix="/api/v1")
app.include_router(runs_router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")
app.include_router(pipelines_router, prefix="/api/v1")


@app.get("/health")
def health_check():
    return {"status": "ok"}
