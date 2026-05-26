# Development setup

## Sibling repositories

The tracking stack lives in two repos that should be cloned **alongside** this
one (i.e. as siblings in the same parent directory):

```
parent/
├── py3r_analysis_gui/      ← this repo
├── Py3R-Pose/              ← pose estimation library
└── BohacekLabPoseModels/   ← model weights (git-lfs)
```

```bash
# From the parent directory:
git clone https://github.com/ETHZ-INS/Py3R-Pose.git
git clone https://github.com/ETHZ-INS/BohacekLabPoseModels.git
cd BohacekLabPoseModels && git lfs pull && cd ..
```

The pinned commits used for each release are recorded in
[`versions.yaml`](versions.yaml).  During development you work
against your local clones directly; the build workflow clones the pinned
commits into a clean CI workspace.

## Python environments

Two separate environments are needed:

| Environment | Purpose | Key dependencies |
|---|---|---|
| `py3r_gui` | GUI + analysis | PyQt6, py3r_behaviour, py3r_analysis_gui |
| `py3r_pose` (or `unifiedpointtracking`) | Tracking | py3r_pose[yolo], PyTorch, ultralytics |

Install the tracking library into its environment:
```bash
pip install -e ../Py3R-Pose[yolo]
```

The GUI calls the tracking environment's `py3r_pose` executable as a
subprocess — the two environments never share a process.

## Running the GUI

```bash
# From repo root, with py3r_gui environment active:
python -m app
```

## Code style

[ruff](https://docs.astral.sh/ruff/) is enforced via pre-commit:
```bash
pip install pre-commit
pre-commit install
```
