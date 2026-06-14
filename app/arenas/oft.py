from app.pipelines import oft_pipeline
from app.trackers import yolo_tracker as tracker

NAME = "Open Field Test"
ARENA_IMAGE = "oft_arena.png"
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
    "manifest": "manifest",
    "output_dir": "output_dir",
    "comparisons": "comparisons",
    "video_paths": "video_paths",
}
OPTIONS = [
    {"name": "numbins", "type": int, "default": None, "label": "Time bins", "min": 2, "max": 20},
    {
        "name": "n_clusters",
        "type": int,
        "default": 10,
        "label": "Behaviour clusters (k)",
        "min": 5,
        "max": 50,
    },
]
