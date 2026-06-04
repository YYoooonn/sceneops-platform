from fastapi import APIRouter

from app.platform.artifacts.router import router as artifacts_router
from app.platform.executions.router import router as executions_router
from app.platform.jobs.router import router as jobs_router
from app.platform.pipelines.router import router as pipelines_router

from app.domains.datasets.router import router as datasets_router
from app.domains.evaluations.router import router as evaluations_router
from app.domains.inference.router import router as inference_router
from app.domains.labels.router import router as labels_router
from app.domains.models.router import router as models_router
from app.domains.scenarios.router import router as scenarios_router
from app.domains.scenes.router import router as scenes_router

from app.views.leaderboards.router import router as leaderboards_router
from app.views.operations.router import router as operations_router

api_router = APIRouter()

# platform
api_router.include_router(jobs_router, prefix="/jobs", tags=["jobs"])
api_router.include_router(pipelines_router, prefix="/pipelines", tags=["pipelines"])
api_router.include_router(executions_router, prefix="/executions", tags=["executions"])
api_router.include_router(artifacts_router, prefix="/artifacts", tags=["artifacts"])

# domains — core resources
api_router.include_router(datasets_router, prefix="/datasets", tags=["datasets"])
api_router.include_router(scenes_router, prefix="/scenes", tags=["scenes"])
api_router.include_router(scenarios_router, prefix="/scenario-sets", tags=["scenarios"])
api_router.include_router(models_router, prefix="/models", tags=["models"])

# domains — ML workflow results
api_router.include_router(inference_router, prefix="/inference", tags=["inference"])
api_router.include_router(
    evaluations_router, prefix="/evaluations", tags=["evaluations"]
)
api_router.include_router(labels_router, prefix="/labels", tags=["labels"])

# views
api_router.include_router(operations_router, prefix="/operations", tags=["operations"])
api_router.include_router(
    leaderboards_router, prefix="/leaderboards", tags=["leaderboards"]
)
