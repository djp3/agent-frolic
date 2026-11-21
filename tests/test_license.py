# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The code's license: the GNU General Public License, version 3 or later. Its text, the
notice that says so, and the two lines at the top of each source file."""
import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OWNER = "Copyright (C) 2026 Don Patterson"
SPDX = "SPDX-License-Identifier: GPL-3.0-or-later"


def test_the_license_is_the_text_of_the_gnu_gpl_version_3():
    """As the Free Software Foundation publishes it (gpl-3.0.txt), to the byte."""
    text = (ROOT / "LICENSE").read_bytes()
    assert hashlib.sha256(text).hexdigest() == "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986"
    assert b"GNU GENERAL PUBLIC LICENSE" in text and b"Version 3, 29 June 2007" in text


def test_the_notice_says_whose_it_is_which_license_and_where_the_source_is():
    notice = " ".join((ROOT / "NOTICE").read_text().split())
    assert OWNER in notice
    assert "either version 3 of the License, or (at your option) any later version" in notice
    assert "WITHOUT ANY WARRANTY" in notice
    assert "https://github.com/djp3/agent-frolic" in notice           # where the source is
    assert "credits/README.txt" in notice                             # the artwork has licenses of its own


def test_every_source_file_says_whose_it_is_and_its_license():
    """Python, Swift and shell, in the first lines of each; the mockups are left as they are.
    A shebang, and Swift's tools-version line, stay first."""
    tracked = subprocess.run(["git", "ls-files", "*.py", "*.sh", "*.swift"], cwd=ROOT, capture_output=True,
                             text=True, timeout=30).stdout.split()
    sources = [name for name in tracked if not name.startswith("mockup/")]
    assert len(sources) >= 44
    for name in sources:
        lines = (ROOT / name).read_text().splitlines()
        mark = "//" if name.endswith(".swift") else "#"
        top = lines[:3]
        assert f"{mark} {OWNER}" in top and f"{mark} {SPDX}" in top, name
        if name.endswith(".sh"):
            assert lines[0].startswith("#!"), name
    assert (ROOT / "macos" / "Package.swift").read_text().startswith("// swift-tools-version")


def test_the_app_carries_the_licenses_of_the_packages_its_host_links():
    """Sparkle's and SwiftTerm's ask that their text go with every copy. Taken from the
    packages the copy was built from, and a build without one stops."""
    build = (ROOT / "scripts" / "build_app.sh").read_text()
    assert "for package in Sparkle SwiftTerm; do" in build
    assert 'cp "macos/.build/checkouts/$package/LICENSE" "$APP/Contents/Resources/licenses/$package.txt"' in build
    assert '[ -s "macos/.build/checkouts/$package/LICENSE" ]' in build and "exit 1" in build
    package = (ROOT / "macos" / "Package.swift").read_text()
    linked = {line.split('name: "')[1].split('"')[0] for line in package.splitlines() if ".product(name:" in line}
    assert linked == {"Sparkle", "SwiftTerm"}                           # one more linked is one more license
    notice = " ".join((ROOT / "NOTICE").read_text().split())
    assert "The texts of Sparkle's and SwiftTerm's are in the app, in Contents/Resources/licenses." in notice


def test_the_app_carries_the_license_and_says_so():
    build = (ROOT / "scripts" / "build_app.sh").read_text()
    assert 'cp LICENSE NOTICE "$APP/Contents/Resources/"' in build      # in every copy, under no condition
    plist = (ROOT / "macos" / "Info.plist").read_text()
    assert "NSHumanReadableCopyright" in plist                          # what the About panel shows
    assert "2026 Don Patterson" in plist and "GNU General Public License, version 3 or later" in plist
    credits = " ".join((ROOT / "credits" / "README.txt").read_text().split())
    assert "The code of agent-frolic is under the GNU General Public License, version 3 or later" in credits
