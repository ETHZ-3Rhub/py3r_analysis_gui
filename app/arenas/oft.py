from app.pipelines import oft_pipeline as pipeline
from app.trackers import py3r_pose

NAME = "Open Field Test"
TRACKER = py3r_pose
TRACKER_ARGS = {"instances": ["oft", "mouse_top"], "tracker_type": "fixed-instances"}
PIPELINE = pipeline
