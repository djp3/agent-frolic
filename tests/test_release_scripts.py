# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""How a release is built, signed and published as an update (scripts/release.sh,
build_app.sh, sign_app.sh and macos/Info.plist): the things that, got wrong, leave
installed copies unable to update, or find a mistake only after the notarization."""
import base64
import json
import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RELEASE = (ROOT / "scripts" / "release.sh").read_text()
BUILD = (ROOT / "scripts" / "build_app.sh").read_text()
PUBLIC_KEY = ROOT / "scripts" / "sparkle_public_key.sh"
# RFC 8032, section 7.1, test 1: an Ed25519 seed and the public key that belongs to it.
SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
ITS_PUBLIC = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")

needs_swift = pytest.mark.skipif(shutil.which("swift") is None, reason="needs the Swift toolchain, as a build does")


def info_plist() -> dict:
    return plistlib.loads((ROOT / "macos" / "Info.plist").read_text().replace("__VERSION__", "1.2.3").encode())


def public_half(tmp_path, held: bytes | str):
    key = tmp_path / "exported.key"
    key.write_text(held if isinstance(held, str) else base64.b64encode(held).decode() + "\n")
    return subprocess.run(["/bin/bash", str(PUBLIC_KEY), str(key)], capture_output=True, text=True, timeout=120)


# ---- the key a release is signed with ---------------------------------------------------

def test_the_build_keeps_to_the_versions_that_are_pinned():
    """Or it would move to a newer Sparkle than the tools were chosen for, and repin it."""
    assert "swift build -c release --product AgentFrolic --only-use-versions-from-resolved-file" in BUILD


# What a release does with the key and the appcast is shown by running it: test_release.py,
# test_sparkle_key.py and test_check_appcast.py. Here: what the app itself asks for.

def test_the_app_requires_a_signed_appcast_and_a_checked_update_and_has_both_or_neither():
    """Sparkle refuses to start with the first and not the second: no update would ever come."""
    plist = info_plist()
    assert plist["SURequireSignedFeed"] is True and plist["SUVerifyUpdateBeforeExtraction"] is True
    assert plist["SUPublicEDKey"] and plist["SUFeedURL"].startswith("https://")


def test_only_a_release_carries_the_feed_a_copy_built_by_hand_does_not_update_itself():
    """With the feed in it, a copy built to try a change would offer the published release.
    Not told by how it is signed: on a Mac with a Developer ID in its keychain, a copy built
    by hand is signed with it as a release is."""
    assert 'if [ "${WITH_UPDATES:-0}" != 1 ]; then' in BUILD
    taken_out = BUILD[BUILD.index('if [ "${WITH_UPDATES:-0}" != 1 ]; then'):]
    taken_out = taken_out[:taken_out.index("\nfi\n")]
    for key in ("SUFeedURL", "SUEnableAutomaticChecks", "SUScheduledCheckInterval"):
        assert f'-c "Delete :{key}"' in taken_out
        assert key in info_plist()                                     # and a release has them
    assert 'WITH_UPDATES=1 PYTHON="$PYTHON" CODESIGN_IDENTITY="$IDENTITY" scripts/build_app.sh' in RELEASE
    delegate = (ROOT / "macos" / "Sources" / "AgentFrolic" / "AppDelegate.swift").read_text()
    assert 'guard !isDevelopmentRun, Bundle.main.object(forInfoDictionaryKey: "SUFeedURL") != nil' in delegate


def test_sparkles_helpers_are_signed_as_sparkle_says_to_sign_them():
    """Each by itself and then the framework: only the downloader keeps its entitlements."""
    sign = (ROOT / "scripts" / "sign_app.sh").read_text()
    steps = [line.strip() for line in sign.splitlines() if line.startswith("sign ") and "$FW" in line]
    assert steps == ['sign "$FW/Versions/B/XPCServices/Installer.xpc"',
                     'sign --preserve-metadata=entitlements "$FW/Versions/B/XPCServices/Downloader.xpc"',
                     'sign "$FW/Versions/B/Autoupdate"', 'sign "$FW/Versions/B/Updater.app"', 'sign "$FW"']
    assert "--deep" not in "".join(line for line in sign.splitlines() if line.lstrip().startswith("sign "))


def test_the_version_of_sparkle_cannot_drift_between_the_manifest_the_pin_and_the_build():
    """The manifest gives the least version, the pin the one in use, and the build keeps to
    the pin. A manifest that asked for more than the pin would make every build fail."""
    package = (ROOT / "macos" / "Package.swift").read_text()
    least = package.split('sparkle-project/Sparkle.git", from: "')[1].split('"')[0]
    resolved = json.loads((ROOT / "macos" / "Package.resolved").read_text())
    pinned = next(pin["state"]["version"] for pin in resolved["pins"] if pin["identity"] == "sparkle")
    assert tuple(map(int, least.split("."))) <= tuple(map(int, pinned.split("."))), (least, pinned)
    tools = (ROOT / "scripts" / "sparkle_tools.sh").read_text()
    assert "swift package --only-use-versions-from-resolved-file resolve" in tools
    assert RELEASE.count("TOOLS=$(scripts/sparkle_tools.sh)") == 2      # before the build, and again after it


@needs_swift
def test_an_exported_keys_public_half_is_the_one_ed25519_gives_its_seed(tmp_path):
    got = public_half(tmp_path, SEED)
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == base64.b64encode(ITS_PUBLIC).decode()
    assert base64.b64encode(SEED).decode() not in got.stdout + got.stderr                      # only the public half


@needs_swift
def test_a_key_in_sparkles_older_form_carries_its_public_half_at_its_end(tmp_path):
    got = public_half(tmp_path, bytes(64) + ITS_PUBLIC)
    assert got.returncode == 0 and got.stdout.strip() == base64.b64encode(ITS_PUBLIC).decode()


@needs_swift
@pytest.mark.parametrize("held, said", [("not a key at all\n", "the file is not base64 text"),
                                        (bytes(40), "the key is 40 bytes"), ("", "the key is 0 bytes")])
def test_what_is_not_a_sparkle_key_is_refused_and_says_why(tmp_path, held, said):
    got = public_half(tmp_path, held)
    assert got.returncode == 1 and got.stdout == "" and said in got.stderr


def test_a_key_file_that_is_not_there_is_refused(tmp_path):
    got = subprocess.run(["/bin/bash", str(PUBLIC_KEY), str(tmp_path / "nowhere.key")], capture_output=True, text=True)
    assert got.returncode == 1 and got.stdout == "" and "cannot read" in got.stderr


def test_the_app_is_left_one_place_to_look_for_its_frameworks_or_the_build_stops():
    """Its own Contents/Frameworks, which Package.swift sets. Whatever else the toolchain
    recorded is taken out, and anything but that one left stops the build."""
    assert "SwiftPM records an absolute rpath" not in BUILD            # it said so, and none is recorded
    rule = BUILD[BUILD.index("# Where the app looks for Sparkle.framework"):BUILD.index('cp -R "$SPARKLE_FW"')]
    assert 'NEEDED="@executable_path/../Frameworks"' in rule
    assert '[ "$rp" = "$NEEDED" ] || install_name_tool -delete_rpath "$rp" "$EXE"' in rule
    assert '[ "$(rpaths)" = "$NEEDED" ] || {' in rule and "exit 1" in rule
    package = (ROOT / "macos" / "Package.swift").read_text()
    assert '"-rpath", "-Xlinker", "@executable_path/../Frameworks"' in package


def test_closing_the_boards_window_ends_the_app_whatever_else_is_open():
    """With Sparkle's update window open, "after the last window closed" never comes."""
    delegate = (ROOT / "macos" / "Sources" / "AgentFrolic" / "AppDelegate.swift").read_text()
    closing = delegate[delegate.index("func windowWillClose(_ notification: Notification)"):]
    closing = closing[:closing.index("\n    }\n")]
    assert "(notification.object as? NSWindow) === window" in closing and "NSApp.terminate(nil)" in closing
    assert "w.isReleasedWhenClosed = false" in delegate and "w.delegate = self" in delegate
    assert "if coreRunning { terminal.terminate() }" in delegate       # and ending the app ends the board


def test_no_pipe_in_the_scripts_can_end_one_of_them_unheard():
    """Under pipefail a pipeline fails when its reader leaves early and the writer is cut off:
    `file … | grep -q`, `find … | head -1`, an awk that exits at its first match. In a test
    that reads as "no"; in an assignment, under `set -e`, it ends the script with nothing said."""
    sign = (ROOT / "scripts" / "sign_app.sh").read_text()
    assert 'kind=$(file -b "$f")' in sign and 'case "$kind" in *Mach-O*) sign "$f" ;; esac' in sign
    for script in sorted((ROOT / "scripts").glob("*.sh")):
        code = "\n".join(line for line in script.read_text().splitlines() if not line.lstrip().startswith("#"))
        if "pipefail" not in code:
            continue
        assert "| grep -q" not in code, script.name
        assert "| head -1)" not in code, script.name                    # in an assignment
        for line in code.splitlines():
            if "| awk" in line and "$(" in line:
                assert "exit}" not in line.split("| awk", 1)[1], f"{script.name}: {line.strip()[:80]}"
