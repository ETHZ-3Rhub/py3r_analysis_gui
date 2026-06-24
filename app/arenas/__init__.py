"""Bundled pipeline configs.

A pipeline is a TOML config (see ``app/pipeline_config.py``). The bundled ones
live in ``app/arenas/configs/*.toml``; their analysis code lives in
``app/pipelines/<entry>.py``. Discovery is ``pipeline_config.discover()`` — this
package is just the home for the bundled config files.
"""
