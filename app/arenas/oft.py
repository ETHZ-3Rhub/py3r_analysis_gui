from app.pipelines import oft_pipeline
from app.trackers import yolo_tracker as tracker

NAME = "Open Field Test"
VERSION = "0.1.0"
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
            "batch": 128,
        },
    ],
}
PIPELINE = oft_pipeline.run
PIPELINE_INPUTS = {
    "group_csv_files": "group_csv_files",
    "output_dir": "output_dir",
    "comparisons": "comparisons",
    "group_video_files": "group_video_files",
}
OPTIONS = []
