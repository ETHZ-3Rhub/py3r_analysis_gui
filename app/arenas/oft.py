from app.pipelines import oft_pipeline as pipeline
from app.trackers import yolo_tracker

NAME = "Open Field Test"
TRACKER = yolo_tracker
TRACKER_ARGS = {
    "models": [
        {
            "model": "environment/environment_main",
            "instances": [{"type": "oft", "max": 1}],
            "stride": (30, "ffill"),
            "batch": 4,
        },
        {
            "model": "mouse/mouse_top_main",
            "instances": [{"type": "mouse_top", "max": 1}],
            "batch": 96,
        },
    ],
}
PIPELINE = pipeline
