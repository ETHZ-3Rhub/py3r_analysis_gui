import re
import tomllib
from pathlib import Path


def _app_version() -> str:
    return tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"]


def _py3r_version() -> str:
    text = Path("RELEASE_REFS.md").read_text()
    m = re.search(r"^py3r_behaviour:\s*(\S+)", text, re.MULTILINE)
    return m.group(1) if m else "unknown"


def on_config(config, **kwargs):
    config["site_name"] = f"py3r Analysis v{_app_version()}"
    return config


def on_page_content(html, page, config, files, **kwargs):
    version = _py3r_version()
    url = f"https://ethz-3rhub.github.io/py3r_behaviour/{version}/"
    link = f'<a href="{url}">{version}</a>'
    return html.replace("{py3r_version}", link)
