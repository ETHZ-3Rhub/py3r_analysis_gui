# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for py3r Analysis GUI.
# Commit this file — do not rely on auto-generation.
#
# Build with:  pyinstaller py3r_analysis_gui.spec --clean

from PyInstaller.utils.hooks import collect_all, collect_submodules

# py3r is a namespace package (no __init__.py) - collect_all ensures its
# submodules, data, and binaries are all bundled and that py3r.behaviour's
# __init__.py exports (e.g. TrackingCollection) resolve correctly when frozen.
py3r_datas, py3r_binaries, py3r_hiddenimports = collect_all("py3r.behaviour")

# py3r_behaviour lazily imports these plotting deps from inside its plotting
# methods - [viz] extras (umap, pycirclize) and core deps (seaborn,
# statannotations). collect_all("py3r.behaviour") above can't discover lazy
# imports, so each must be collected separately here.
umap_datas, umap_binaries, umap_hiddenimports = collect_all("umap")
pycirclize_datas, pycirclize_binaries, pycirclize_hiddenimports = collect_all("pycirclize")
seaborn_datas, seaborn_binaries, seaborn_hiddenimports = collect_all("seaborn")
statannotations_datas, statannotations_binaries, statannotations_hiddenimports = collect_all("statannotations")

# Pull in all arena and pipeline modules so the auto-discovery works at runtime
hidden_imports = (
    collect_submodules("app.arenas")
    + collect_submodules("app.pipelines")
    + py3r_hiddenimports
    + umap_hiddenimports
    + pycirclize_hiddenimports
    + seaborn_hiddenimports
    + statannotations_hiddenimports
    + ["py3r"]
    # Add other heavyweight deps that PyInstaller may miss:
    + ["pyarrow", "sklearn", "shapely", "cv2"]
)

a = Analysis(
    ["app/main.py"],
    pathex=["."],
    binaries=[
        # uv — used by app.tracking_env_setup to build tracking_env/ on the
        # target machine, which won't have uv installed.
        ("vendor/uv.exe", "vendor"),
        *py3r_binaries,
        *umap_binaries,
        *pycirclize_binaries,
        *seaborn_binaries,
        *statannotations_binaries,
    ],
    datas=[
        # Bundle any data files from py3r_behaviour (e.g. bundled test CSVs)
        *py3r_datas,
        *umap_datas,
        *pycirclize_datas,
        *seaborn_datas,
        *statannotations_datas,
        ("assets/icon.ico", "assets"),
        ("assets/icon.png", "assets"),
        # track.py is run as a script in tracking_env's interpreter, not
        # imported — PyInstaller won't pick it up automatically.
        ("app/trackers/track.py", "app/trackers"),
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude heavy deps we don't use in the GUI
        "pytest", "nbmake", "nbformat", "mkdocs",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="py3r_analysis",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,      # no terminal window — set True temporarily to debug crashes
    disable_windowed_traceback=False,
    icon="assets/icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="py3r_analysis",
)
