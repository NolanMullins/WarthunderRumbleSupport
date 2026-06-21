"""Tests for the auto-update system (version compare, GitHub checker, Windows installer helpers).

Network and OS side effects are injected/mocked: the checker takes a fake fetch_json, and the
installer's download/stage/script generation are exercised with local data so nothing hits the
network or touches a real install.
"""
import os
import sys
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from winwinghaptics import __version__                              # noqa: E402
from winwinghaptics.update import version as ver                    # noqa: E402
from winwinghaptics.update.checker import UpdateChecker             # noqa: E402
from winwinghaptics.update.installer import WindowsUpdater          # noqa: E402


# --------------------------------------------------------------------------- version compare
def test_parse_version_strips_v_and_prerelease():
    assert ver.parse_version("v1.2.3") == ((1, 2, 3), False)
    assert ver.parse_version("1.2.0-rc1") == ((1, 2, 0), True)
    assert ver.parse_version("") == ((0,), False)


def test_is_newer_basic():
    assert ver.is_newer("0.2.0", "0.1.0") is True
    assert ver.is_newer("v0.1.1", "0.1.0") is True
    assert ver.is_newer("0.1.0", "0.1.0") is False
    assert ver.is_newer("0.0.9", "0.1.0") is False


def test_is_newer_handles_uneven_lengths():
    assert ver.is_newer("1.0", "1.0.0") is False
    assert ver.is_newer("1.0.1", "1.0") is True


def test_is_newer_prerelease_precedence():
    # final release beats a pre-release of the same number; a pre-release does not beat the final
    assert ver.is_newer("1.2.0", "1.2.0-rc1") is True
    assert ver.is_newer("1.2.0-rc1", "1.2.0") is False


def test_is_newer_defaults_to_app_version():
    assert ver.is_newer("0.0.0") is False              # nothing is older than 0.0.0... current>=0.1.0
    assert ver.is_newer("999.0.0") is True


# --------------------------------------------------------------------------- checker
def _release(tag, prerelease=False, draft=False, assets=None, body="notes"):
    return {
        "tag_name": tag, "name": tag, "prerelease": prerelease, "draft": draft,
        "body": body, "html_url": f"https://example/releases/{tag}",
        "assets": assets or [],
    }


def _asset(name, url="https://example/dl/x.zip"):
    return {"name": name, "browser_download_url": url}


def test_checker_reports_available_for_newer():
    fetch = lambda url: [_release("v9.9.9", assets=[_asset("WinwingHaptics-9.9.9.zip")])]
    info = UpdateChecker(current_version="0.1.0", fetch_json=fetch).check()
    assert info.available is True
    assert info.version == "9.9.9"
    assert info.asset_name == "WinwingHaptics-9.9.9.zip"
    assert info.asset_url.endswith(".zip")


def test_checker_not_available_for_same_version():
    fetch = lambda url: [_release("v0.1.0")]
    info = UpdateChecker(current_version="0.1.0", fetch_json=fetch).check()
    assert info.available is False
    assert info.version == "0.1.0"


def test_checker_skips_prerelease_by_default():
    fetch = lambda url: [_release("v2.0.0-rc1", prerelease=True), _release("v1.0.0")]
    info = UpdateChecker(current_version="0.1.0", fetch_json=fetch).check()
    assert info.version == "1.0.0"          # the pre-release was skipped


def test_checker_includes_prerelease_when_asked():
    fetch = lambda url: [_release("v2.0.0-rc1", prerelease=True), _release("v1.0.0")]
    info = UpdateChecker(current_version="0.1.0", include_prereleases=True,
                         fetch_json=fetch).check()
    assert info.version == "2.0.0-rc1"


def test_checker_skips_drafts():
    fetch = lambda url: [_release("v3.0.0", draft=True), _release("v1.5.0")]
    info = UpdateChecker(current_version="0.1.0", fetch_json=fetch).check()
    assert info.version == "1.5.0"


def test_checker_picks_zip_asset():
    assets = [_asset("notes.txt", "https://e/notes.txt"),
              _asset("WinwingHaptics-1.0.0-win64.zip", "https://e/build.zip")]
    fetch = lambda url: [_release("v1.0.0", assets=assets)]
    info = UpdateChecker(current_version="0.1.0", fetch_json=fetch).check()
    assert info.asset_name.endswith(".zip")
    assert info.asset_url == "https://e/build.zip"


def test_checker_none_on_fetch_error():
    def boom(url):
        raise OSError("no network")
    assert UpdateChecker(fetch_json=boom).check() is None


def test_checker_none_when_no_releases():
    assert UpdateChecker(fetch_json=lambda url: []).check() is None


# --------------------------------------------------------------------------- installer
def test_updater_unsupported_when_not_frozen():
    # tests run from source -> not frozen -> swap path must be disabled
    assert WindowsUpdater(exe_path="C:/app/WinwingHaptics.exe").is_supported() is False


def test_update_noop_when_unsupported(tmp_path):
    up = WindowsUpdater(exe_path=str(tmp_path / "WinwingHaptics.exe"))
    called = {"exit": False}
    info = type("I", (), {"asset_url": "https://e/x.zip", "asset_name": "x.zip"})()
    ok = up.update(info, _exit=lambda: called.__setitem__("exit", True))
    assert ok is False and called["exit"] is False


def test_helper_script_contains_key_fields(tmp_path):
    up = WindowsUpdater(app_dir=str(tmp_path / "app"),
                        exe_path=str(tmp_path / "app" / "WinwingHaptics.exe"))
    script = up._helper_script(str(tmp_path / "staged" / "WinwingHaptics"),
                               str(tmp_path / "staged"))
    assert str(os.getpid()) in script
    assert "robocopy" in script
    assert "WinwingHaptics.exe" in script
    assert "goto waitloop" in script


def test_helper_script_preserves_user_data():
    # the swap must NOT use /MIR (which purges user config/calibration/recordings next to the exe),
    # and must exclude the settings files from being overwritten
    up = WindowsUpdater(app_dir="C:/app", exe_path="C:/app/WinwingHaptics.exe")
    script = up._helper_script("C:/staged/WinwingHaptics", "C:/staged")
    assert "/MIR" not in script
    assert "/E " in script
    assert "winwing_haptics.json" in script
    assert "hud_calib.json" in script


def test_build_root_none_when_no_exe(tmp_path):
    # extracted contents without the app exe -> no build root (caller must abort, not guess)
    staging = tmp_path / "staged"
    (staging / "random").mkdir(parents=True)
    (staging / "random" / "notes.txt").write_text("hi")
    up = WindowsUpdater(app_dir=str(tmp_path / "app"),
                        exe_path=str(tmp_path / "app" / "WinwingHaptics.exe"))
    assert up._build_root(str(staging)) is None


def test_stage_extracts_and_finds_nested_build_root(tmp_path):
    # build a zip with the exe nested under a top folder (as PyInstaller --onedir zips often are)
    build = tmp_path / "WinwingHaptics"
    build.mkdir()
    (build / "WinwingHaptics.exe").write_bytes(b"MZ")
    (build / "data.bin").write_bytes(b"x")
    zip_path = tmp_path / "rel.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.write(build / "WinwingHaptics.exe", "WinwingHaptics/WinwingHaptics.exe")
        zf.write(build / "data.bin", "WinwingHaptics/data.bin")
    up = WindowsUpdater(app_dir=str(tmp_path / "installed"),
                        exe_path=str(tmp_path / "installed" / "WinwingHaptics.exe"))
    root = up.stage(str(zip_path), str(tmp_path / "staged"))
    assert os.path.exists(os.path.join(root, "WinwingHaptics.exe"))


def test_download_writes_stream(tmp_path):
    class FakeResp:
        def __init__(self, data):
            self._d = data
            self.headers = {"Content-Length": str(len(data))}
            self._sent = False

        def read(self, n=-1):
            if self._sent:
                return b""
            self._sent = True
            return self._d

        def close(self):
            pass

    seen = {}
    up = WindowsUpdater(exe_path=str(tmp_path / "WinwingHaptics.exe"),
                        opener=lambda url, timeout=30.0: FakeResp(b"PAYLOAD"))
    dest = tmp_path / "out.zip"
    up.download("https://e/x.zip", str(dest),
                on_progress=lambda r, t: seen.__setitem__("p", (r, t)))
    assert dest.read_bytes() == b"PAYLOAD"
    assert seen["p"] == (7, 7)
