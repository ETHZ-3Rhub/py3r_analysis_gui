from app.pipelines import oft_pipeline as pipeline
from app.trackers import yolo_tracker

NAME = "Open Field Test"
TRACKER = yolo_tracker
TRACKER_ARGS = {
    "models": [
        ("environment/environment_main", "oft"),
        ("mouse/mouse_top_main", "mouse_top"),
    ],
}
PIPELINE = pipeline
