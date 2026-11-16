"""Where DeviceHands keeps its configuration and data on this computer.

Each operating system has its own convention, and ``platformdirs`` knows
them, so the paths come from it rather than from hand-written ``~/.config``
strings (which are wrong on Windows and unusual on macOS):

                 config_dir()                       data_dir()
    Linux        $XDG_CONFIG_HOME/devicehands       $XDG_DATA_HOME/devicehands
                 (~/.config/devicehands)            (~/.local/share/devicehands)
    macOS        ~/Library/Application Support/devicehands  (both)
    Windows      %LOCALAPPDATA%\\devicehands        (both)

Under data_dir():
    workspace/   default workspace (logs, sessions, bench state); see
                 infra.workspace.workspace_root for what overrides it
    tools/       adb / fastboot / mksh that ``dhands setup deps --install``
                 downloads (infra.host_deps)

The names are deliberately our own. The program DeviceHands grew out of
keeps its files elsewhere; DeviceHands neither reads nor writes them, so the
two can be installed side by side.

Nothing here creates a directory. Callers create what they write into.
"""

from __future__ import annotations

from pathlib import Path

import platformdirs

APP_NAME = "devicehands"


def config_dir() -> Path:
    """Directory of the global ``config.toml``."""
    return Path(platformdirs.user_config_dir(APP_NAME, appauthor=False))


def data_dir() -> Path:
    """Per-user data directory (holds ``workspace/`` and ``tools/``)."""
    return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))


def default_workspace_dir() -> Path:
    """Workspace used when neither DHANDS_WORKSPACE nor config.toml names one."""
    return data_dir() / "workspace"


def tools_dir() -> Path:
    """Where ``dhands setup deps --install`` puts downloaded host tools."""
    return data_dir() / "tools"
