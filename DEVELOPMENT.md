# Development setup

## Sibling repositories

The model weights live in a repo that should be cloned **alongside** this
one (i.e. as a sibling in the same parent directory):

```
parent/
├── py3r_analysis_gui/      ← this repo
└── BohacekLabPoseModels/   ← model weights (git-lfs)
```

```bash
# From the parent directory:
git clone https://github.com/ETHZ-INS/BohacekLabPoseModels.git
cd BohacekLabPoseModels && git lfs pull && cd ..
```

The pinned commit used for each release is recorded in
[`versions.yaml`](versions.yaml).  During development you work
against your local clone directly; the build workflow clones the pinned
commit into a clean CI workspace.

## Python environments

Two separate environments are needed:

| Environment | Purpose | Key dependencies |
|---|---|---|
| `py3r_gui` | GUI + analysis | PySide6, py3r_behaviour, py3r_analysis_gui |
| `tracking_env` | Tracking | PyTorch, ultralytics |

Install this package into the `py3r_gui` environment. `py3r_behaviour` is an
optional `analysis` extra (needed to *run* a pipeline, but not to launch the GUI
or run the tests), so install it with the extra during development:

```bash
# From repo root, with py3r_gui environment active:
pip install -e ".[analysis,dev]"
# py3r_behaviour resolves from PyPI; to work against your sibling clone instead:
#   pip install -e ../py3r_behaviour
```

Create the tracking environment by running the GUI and using
Settings → Reinstall tracking environment.

The GUI calls `tracking_env/Scripts/python.exe` (Windows) or
`tracking_env/bin/python` (Unix) as a subprocess to run
`app/trackers/track.py` — the two environments never share a process.

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
