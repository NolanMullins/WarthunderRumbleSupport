"""Query the GitHub Releases API for the latest version.

Stdlib-only (urllib + json) so the app keeps its lean dependency set. The HTTP fetch is injectable
(`fetch_json`) so the logic is unit-testable without the network, and any error -> check() returns
None (the caller simply shows nothing / "up to date"). Pre-releases are ignored by default.
"""
import json
import urllib.request
from collections import namedtuple

from .version import __version__, is_newer

GITHUB_OWNER = "NolanMullins"
GITHUB_REPO = "WarthunderRumbleSupport"
_API = "https://api.github.com/repos/{owner}/{repo}/releases"
_RELEASES_PAGE = "https://github.com/{owner}/{repo}/releases"

# available  : True if `version` is newer than the running app
# version    : release tag without a leading 'v' (e.g. "0.2.0")
# name       : release title
# notes      : release body / changelog (may be "")
# html_url   : the release page (fallback "download" target / "view notes")
# asset_url  : browser_download_url of the update asset (the zipped --onedir build), or None
# asset_name : that asset's filename, or None
UpdateInfo = namedtuple(
    "UpdateInfo", "available version name notes html_url asset_url asset_name")


def _default_fetch_json(url, timeout=6.0):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "WinwingHaptics-Updater",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def releases_page(owner=GITHUB_OWNER, repo=GITHUB_REPO):
    return _RELEASES_PAGE.format(owner=owner, repo=repo)


class UpdateChecker:
    """Checks GitHub Releases for a newer build than the running app.

    asset_pattern: substring/suffix used to pick the update asset among a release's assets
    (defaults to ".zip" -- the zipped --onedir build). fetch_json is injectable for tests."""
    def __init__(self, owner=GITHUB_OWNER, repo=GITHUB_REPO, current_version=None,
                 asset_pattern=".zip", include_prereleases=False, fetch_json=None):
        self.owner = owner
        self.repo = repo
        self.current = current_version or __version__
        self.asset_pattern = asset_pattern.lower()
        self.include_prereleases = include_prereleases
        self._fetch = fetch_json or _default_fetch_json

    def _pick_asset(self, release):
        assets = release.get("assets") or []
        for a in assets:
            name = (a.get("name") or "").lower()
            if self.asset_pattern in name and a.get("browser_download_url"):
                return a.get("name"), a.get("browser_download_url")
        return None, None

    def _latest_release(self):
        """Return the newest published (non-draft) release dict, honoring include_prereleases.

        Uses the list endpoint (not /releases/latest) so pre-releases can be considered when asked;
        GitHub returns releases newest-first."""
        url = _API.format(owner=self.owner, repo=self.repo)
        data = self._fetch(url)
        if not isinstance(data, list):
            return None
        for rel in data:
            if rel.get("draft"):
                continue
            if rel.get("prerelease") and not self.include_prereleases:
                continue
            return rel
        return None

    def check(self):
        """Return an UpdateInfo, or None on any error / when nothing is published.

        UpdateInfo.available reflects whether the latest release is newer than the running version,
        so callers can also surface "you're up to date" using available=False."""
        try:
            rel = self._latest_release()
        except Exception:
            return None
        if not rel:
            return None
        tag = (rel.get("tag_name") or rel.get("name") or "").strip()
        if not tag:
            return None
        version = tag[1:] if tag[:1].lower() == "v" else tag
        asset_name, asset_url = self._pick_asset(rel)
        return UpdateInfo(
            available=is_newer(tag, self.current),
            version=version,
            name=rel.get("name") or tag,
            notes=rel.get("body") or "",
            html_url=rel.get("html_url") or releases_page(self.owner, self.repo),
            asset_url=asset_url,
            asset_name=asset_name,
        )
