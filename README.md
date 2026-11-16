# Analys3R

> **This project is still in development and has no stable release yet.** Pre-release builds are available for testing but are not recommended for general use.

[Download the app](https://ethz-3rhub.github.io/py3r_analysis_gui/) · [Documentation](https://ethz-3rhub.github.io/py3r_analysis_gui/docs/)

## Licence

The source code in this repository is licensed under the [MIT Licence](LICENSE), with one exception: `app/trackers/track.py` imports Ultralytics and so, in combination with it, must be used under the AGPL-3.0. It runs as a separate process; the rest of the app does not import Ultralytics.

Distributed release builds include bundled model weights (in `models/`) licensed under [AGPL-3.0](https://www.gnu.org/licenses/agpl-3.0.html). If you use the tracking functionality, the app will also automatically download and install [Ultralytics YOLO](https://github.com/ultralytics/ultralytics) and `ultralytics-thop`, which are likewise licensed under AGPL-3.0.

The macOS build also bundles FFmpeg with the x264 and x265 codecs (via the opencv-python wheel), which are licensed under [GPL-3.0](https://www.gnu.org/licenses/gpl-3.0.html). The user interface uses Qt through PySide6 ([LGPL-3.0](https://www.gnu.org/licenses/lgpl-3.0.html), dynamically linked). Licence texts for everything bundled, plus source information for the GPL components, are in the `THIRD_PARTY_LICENSES/` folder of each release build.
