# One orchestration step of a PipelineRun (pipeline queue): it only observes and
# submits Jobs, never executes one.
PIPELINE_ADVANCE_TASK = "sceneops.tasks.advance_pipeline"
# The execution of one Job through JobRunner (job queue).
JOB_RUN_TASK = "sceneops.tasks.run_job"

PIPELINE_QUEUE = "sceneops.pipeline_runs"
JOB_QUEUE = "sceneops.jobs"
