"""App version + semver-ish comparison.

The version lives on the package (winwinghaptics.__version__); this module re-exports it and adds a
tolerant parser/compare used by the updater. Tags may be prefixed with `v` and may carry a
pre-release suffix (e.g. `1.2.0-rc1`); the numeric release is compared, and a pre-release of the
SAME numeric version is treated as OLDER than the final release (standard semver precedence, kept
deliberately simple).
"""
import re

from .. import __version__   # single source of truth

_NUM = re.compile(r"\d+")


def parse_version(v):
    """Parse a version string to ((major, minor, patch, ...), is_prerelease).

    Tolerant: strips a leading 'v', reads the leading dot-separated integer run, and flags a
    pre-release if a '-' suffix follows the numbers (e.g. '1.2.0-beta'). Unparseable -> ((0,), False)."""
    if not v:
        return (0,), False
    s = str(v).strip()
    if s[:1].lower() == "v":
        s = s[1:]
    # numeric core is everything up to the first '-' / '+' / whitespace
    core = re.split(r"[-+\s]", s, 1)
    nums = tuple(int(n) for n in _NUM.findall(core[0])) or (0,)
    is_pre = len(core) > 1 and bool(core[1])
    return nums, is_pre


def _pad(a, b):
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)), b + (0,) * (n - len(b))


def is_newer(candidate, current=None):
    """True if `candidate` is a strictly newer version than `current` (defaults to the app version).

    Compares the numeric release tuples; if equal, a NON-pre-release candidate beats a pre-release
    current (final > rc of the same number), but a pre-release candidate never beats an equal final.
    """
    if current is None:
        current = __version__
    cnums, cpre = parse_version(candidate)
    unums, upre = parse_version(current)
    cnums, unums = _pad(cnums, unums)
    if cnums != unums:
        return cnums > unums
    # same numeric version: final (not pre) is newer than a pre-release
    return (not cpre) and upre
