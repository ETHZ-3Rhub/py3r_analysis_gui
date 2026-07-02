import tomllib
from pathlib import Path


def on_config(config, **kwargs):
    version = tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]
    config["site_name"] = f"py3r Analysis v{version}"
    return config
