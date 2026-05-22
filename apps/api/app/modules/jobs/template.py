from app.modules.jobs.schemas import JobStep, JobType


def build_default_steps(job_type: JobType) -> list[JobStep]:
    if job_type == JobType.INGEST_NUSCENES:
        return [
            JobStep(name="LOAD_NUSCENES_METADATA"),
            JobStep(name="BUILD_DATASET_MANIFEST"),
            JobStep(name="BUILD_SCENE_MANIFESTS"),
            JobStep(name="BUILD_SAMPLE_MANIFESTS"),
            JobStep(name="SAVE_MANIFESTS"),
        ]

    if job_type == JobType.PREDICT_MOCK_DETECTION:
        return [
            JobStep(name="LOAD_DATASET_MANIFEST"),
            JobStep(name="LOAD_SAMPLE_MANIFESTS"),
            JobStep(name="GENERATE_MOCK_PREDICTIONS"),
            JobStep(name="SAVE_INFERENCE_RUN"),
            JobStep(name="SAVE_PREDICTION_ARTIFACTS"),
        ]

    if job_type == JobType.EVALUATE_DETECTION:
        return [
            JobStep(name="LOAD_INFERENCE_RUN"),
            JobStep(name="LOAD_GT_ANNOTATIONS"),
            JobStep(name="LOAD_PREDICTIONS"),
            JobStep(name="MATCH_BOXES"),
            JobStep(name="COMPUTE_METRICS"),
            JobStep(name="SAVE_EVALUATION_RUN"),
        ]

    return []
