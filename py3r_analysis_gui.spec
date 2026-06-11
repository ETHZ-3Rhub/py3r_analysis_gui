# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for py3r Analysis GUI.
# Commit this file — do not rely on auto-generation.
#
# Build with:  pyinstaller py3r_analysis_gui.spec --clean

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# Pull in all arena and pipeline modules so the auto-discovery works at runtime
hidden_imports = (
    collect_submodules("app.arenas")
    + collect_submodules("app.pipelines")
    + collect_submodules("py3r.behaviour")
    # Add other heavyweight deps that PyInstaller may miss:
    + ["pyarrow", "sklearn", "shapely", "cv2"]
)

a = Analysis(
    ["app/main.py"],
    pathex=["."],
    binaries=[],
    datas=[
        # Bundle any data files from py3r_behaviour (e.g. bundled test CSVs)
        *collect_data_files("py3r.behaviour"),
        ("assets/icon.ico", "assets"),
        ("assets/icon.png", "assets"),
    ],
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude heavy deps we don't use in the GUI
        "pytest", "nbmake", "nbformat", "mkdocs",
        "umap", "pycirclize",
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
