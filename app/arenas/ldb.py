from app.pipelines import ldb_pipeline
from app.trackers import yolo_tracker as tracker

# Tracker model not yet built — keep this arena out of the GUI's dropdown
# until the model is ready and this flag is flipped to True.
READY = False

NAME = "Light-Dark Box"
ARENA_IMAGE = "ldb_arena.png"
TRACKER = tracker
TRACKER_ARGS = {
    "models": [
        {
            "model": "environment/environment_main",
            "instances": [{"type": "ldb", "max": 1}],
            "stride": (30, "ffill"),
            "batch": 32,
        },
        {
            "model": "mouse/mouse_top_main",
            "instances": [{"type": "mouse_top", "max": 1}],
            "batch": 32,
        },
    ],
}
PIPELINE = ldb_pipeline.run
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
        "default": 25,
        "label": "Behaviour clusters (k)",
        "min": 5,
        "max": 50,
    },
]
