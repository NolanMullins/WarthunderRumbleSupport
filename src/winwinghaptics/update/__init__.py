"""Auto-update: check GitHub Releases for a newer version and apply it.

  version.py   the app version + semver-ish parse / compare
  checker.py   query the GitHub Releases API for the latest version (UpdateInfo)
  installer.py download + swap-and-relaunch the frozen --onedir build (Windows only)

The check is portable and unit-testable (HTTP is injectable); the installer is Windows/frozen-only
and degrades to "open the releases page" elsewhere. The GUI shows a banner + an Updates card driven
by UpdateChecker, and triggers the installer on demand.
"""
from .version import __version__, parse_version, is_newer        # noqa: F401
from .checker import UpdateChecker, UpdateInfo, GITHUB_OWNER, GITHUB_REPO   # noqa: F401
