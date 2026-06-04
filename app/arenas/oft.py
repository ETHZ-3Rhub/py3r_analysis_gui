from app.pipelines import oft_pipeline as pipeline
from app.trackers import yolo_tracker as tracker

NAME = "Open Field Test"
TRACKER = tracker
TRACKER_ARGS = {
    "models": [
        {
            "model": "environment/environment_main",
            "instances": [{"type": "oft", "max": 1}],
            "stride": (30, "ffill"),
            "batch": 16,
        },
        {
            "model": "mouse/mouse_top_main",
            "instances": [{"type": "mouse_top", "max": 1}],
            "batch": 256,
        },
    ],
}
PIPELINE = pipeline
