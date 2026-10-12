"""The desktop app: a window for clips and settings, and a tray icon that shows what the service
is doing. Closing the window leaves the app in the tray.

It only reads the service's status file and controls the service through systemd, so closing it
never stops a recording. Needs PySide6: install the app with the `gui` extra.
"""

import argparse
import sys

from .. import APP_NAME, __version__


def main():
    parser = argparse.ArgumentParser(prog=f"{APP_NAME}-gui",
                                     description="Tray icon for the WoW recorder service.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--tray", action="store_true",
                        help="start in the tray without opening the window (used at login)")
    parser.add_argument("--autostart", choices=("on", "off"),
                        help="start the tray icon at every login, or stop doing so")
    args = parser.parse_args()

    if args.autostart:
        from . import desktop
        if args.autostart == "on":
            print(f"Added {desktop.install_autostart()}")
        else:
            desktop.remove_autostart()
            print("The tray icon no longer starts at login.")
        return

    try:
        import PySide6  # noqa: F401
    except ImportError:
        sys.exit(f"The desktop app needs PySide6. Reinstall with the gui extra:\n"
                 f"  uv tool install '{APP_NAME}[gui]'   (or: pipx install '{APP_NAME}[gui]')")
    from .. import config, logs
    try:
        cfg = config.load()
        logs.setup("app", cfg.detailed_logging, cfg.webhook_urls)
    except Exception:
        logs.setup("app")
    import logging
    logging.getLogger(APP_NAME).info(f"Desktop app {__version__} on {logs.system_summary()}")
    from .tray import run
    sys.exit(run(start_hidden=args.tray))
