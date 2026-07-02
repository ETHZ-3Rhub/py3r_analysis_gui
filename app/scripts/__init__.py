"""Bundled analysis scripts — the code a config's ``[script].entry`` points at.

Each file holds the full analysis logic for one arena type (OFT, EPM, …): load
tracking data, compute features, summarise, export. These modules know about
py3r_behaviour and nothing else — never the GUI or the tracker. A config selects
one via ``entry = "<module>:run"`` (e.g. ``"oft:run"`` → ``app.scripts.oft``).

The entry function's contract (kwargs the runner passes, ``POINTS``, deferred
heavy imports) is documented on each script's ``run`` — see ``oft.py``.
"""
