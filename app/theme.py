"""App theme — colour tokens, persistence via QSettings."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QSettings


@dataclass(frozen=True)
class Theme:
    name: str
    bg: str  # window / panel background (the "faceplate")
    panel: str  # raised panel frame background
    display: str  # background for list / input / log "screen" widgets
    accent: str
    accent_hover: str
    text: str  # text inside dark display areas
    panel_text: str  # text / labels that sit on the panel background
    muted: str  # secondary text inside display areas
    sep: str  # separator lines (on faceplate)
    error: str
    warn: str
    success: str
    title: str  # section label colour
    selection_bg: str  # semi-transparent accent for list selection


def _flat(
    bg: str,
    panel: str,
    accent: str,
    accent_hover: str,
    text: str,
    muted: str,
    sep: str,
    error: str,
    warn: str,
    success: str,
    title: str,
    selection_bg: str,
) -> dict:
    """Helper: single-tone theme where display == bg and panel_text == text."""
    return dict(
        bg=bg,
        panel=panel,
        display=bg,
        accent=accent,
        accent_hover=accent_hover,
        text=text,
        panel_text=text,
        muted=muted,
        sep=sep,
        error=error,
        warn=warn,
        success=success,
        title=title,
        selection_bg=selection_bg,
    )


_SEMANTIC = dict(error="#f38ba8", warn="#fab387", success="#a6e3a1")

MOCHA = Theme(
    name="Catppuccin Mocha",
    **_flat(
        bg="#1e1e2e",
        panel="#2a2a3e",
        accent="#7c6af7",
        accent_hover="#9580ff",
        text="#cdd6f4",
        muted="#6c7086",
        sep="#3a3a4e",
        title="#cdd6f4",
        selection_bg="rgba(124, 106, 247, 70)",
        **_SEMANTIC,
    ),
)

# Lighter hot pink on same dark base
FLAMINGO = Theme(
    name="Flamingo",
    **_flat(
        bg="#1e1e2e",
        panel="#2a2a3e",
        accent="#ff5dac",
        accent_hover="#ff80c4",
        text="#cdd6f4",
        muted="#6c7086",
        sep="#3a3a4e",
        title="#ff5dac",
        selection_bg="rgba(255, 93, 172, 70)",
        **_SEMANTIC,
    ),
)

# Very light pastel pink
BUBBLEGUM = Theme(
    name="Bubblegum",
    **_flat(
        bg="#1e1e2e",
        panel="#2a2a3e",
        accent="#ff8cc8",
        accent_hover="#ffaad8",
        text="#cdd6f4",
        muted="#6c7086",
        sep="#3a3a4e",
        title="#ff8cc8",
        selection_bg="rgba(255, 140, 200, 70)",
        **_SEMANTIC,
    ),
)

# Warm brownish-plum dark, medium rose accent
DARKROOM = Theme(
    name="Darkroom",
    **_flat(
        bg="#201820",
        panel="#2c2030",
        accent="#e05490",
        accent_hover="#f075a8",
        text="#d8c8d8",
        muted="#7a6080",
        sep="#3c3048",
        title="#e05490",
        selection_bg="rgba(224, 84, 144, 70)",
        **_SEMANTIC,
    ),
)

# Cool blue-shifted dark grey, vivid magenta
BLUEPRINT = Theme(
    name="Blueprint",
    **_flat(
        bg="#1e2030",
        panel="#282a3e",
        accent="#e040ab",
        accent_hover="#f060c8",
        text="#cdd6f4",
        muted="#6c72a0",
        sep="#38405a",
        title="#e040ab",
        selection_bg="rgba(224, 64, 171, 70)",
        **_SEMANTIC,
    ),
)

# Blueprint with much lighter, neutral grey panels
OVERCAST = Theme(
    name="Overcast",
    **_flat(
        bg="#535570",
        panel="#62648a",
        accent="#e040ab",
        accent_hover="#f060c8",
        text="#e8eafc",
        muted="#a0a4c8",
        sep="#72759a",
        title="#e040ab",
        selection_bg="rgba(224, 64, 171, 70)",
        **_SEMANTIC,
    ),
)

# Gold faceplate + Blueprint LCD screens
THREE_R_SPECIAL = Theme(
    name="3R Special",
    bg="#d4cc8e",  # light silvery-champagne gold — the faceplate
    panel="#ddd6a0",  # slightly lighter/shinier — the raised panel face
    display="#1e2030",  # Blueprint dark — the LCD display windows
    accent="#e040ab",  # hot magenta — gloriously wrong on gold
    accent_hover="#f060c8",
    text="#cdd6f4",  # light text inside the dark LCD areas
    panel_text="#181408",  # near-black warm — active labels engraved on gold
    muted="#8c8040",  # darker gold — inactive/engraved tone on faceplate,
    # also doubles as subtle golden text in dark displays
    sep="#b8b270",  # slightly darker gold for faceplate separators
    title="#e040ab",  # magenta section titles on gold
    selection_bg="rgba(224, 64, 171, 70)",
    **_SEMANTIC,
)

# 3R Special but silver — brushed aluminium faceplate, same Blueprint LCD + magenta accent
HIFI = Theme(
    name="Hifi",
    bg="#d4d4da",  # cool brushed-aluminium silver — the faceplate
    panel="#dcdce2",  # slightly lighter silver — the raised panel face
    display="#1e2030",  # Blueprint dark — the LCD display windows
    accent="#e040ab",  # hot magenta
    accent_hover="#f060c8",
    text="#cdd6f4",  # light text inside dark LCD areas
    panel_text="#18181e",  # near-black with cool tint — active labels on silver
    muted="#878898",  # darker silver — inactive engraved tone on faceplate
    sep="#b0b0be",  # medium silver separator
    title="#e040ab",  # magenta section titles on silver
    selection_bg="rgba(224, 64, 171, 70)",
    **_SEMANTIC,
)

_ALL: dict[str, Theme] = {
    t.name: t
    for t in (MOCHA, FLAMINGO, BUBBLEGUM, DARKROOM, BLUEPRINT, OVERCAST, THREE_R_SPECIAL, HIFI)
}

# Mutable current theme — updated by update_theme(), read by get_theme()
_current: Theme | None = None


def get_theme() -> Theme:
    global _current
    if _current is None:
        _current = load_theme()
    return _current


def update_theme(theme: Theme) -> None:
    global _current
    _current = theme
    save_theme(theme)


def all_themes() -> list[Theme]:
    return list(_ALL.values())


def load_theme() -> Theme:
    s = QSettings("py3r", "analysis_gui")
    name = s.value("theme/name", MOCHA.name)
    return _ALL.get(name, MOCHA)


def save_theme(theme: Theme) -> None:
    s = QSettings("py3r", "analysis_gui")
    s.setValue("theme/name", theme.name)
