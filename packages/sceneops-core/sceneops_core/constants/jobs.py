INGEST_DATASET_STEPS = [
    "load_dataset_metadata",
    "build_dataset_manifest",
    "build_scene_manifests",
    "build_sample_manifests",
    "save_manifests",
]

VALIDATE_DATASET_STEPS = [
    "load_dataset_manifest",
    "validate_scene_index",
    "validate_samples",
    "update_dataset_version_status",
]

PREDICT_MOCK_DETECTION_STEPS = [
    "load_dataset_manifest",
    "load_sample_manifests" "generate_mock_predictions",
    "save_inference_run",
    "save_prediction_artifacts",
]

EVALUATE_DETECTION_STEPS = [
    "load_inference_run",
    "load_gt_annotations",
    "load_predictions",
    "match_boxes",
    "compute_metrics",
    "save_evaluation_run",
]
