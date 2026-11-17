# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The credits folder: the artwork agent-frolic ships, where it came from, and the full text
of every license it is shared under. The licenses say their text goes with every copy."""
import hashlib
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CREDITS = ROOT / "credits"


def check(folder: Path) -> subprocess.CompletedProcess:
    return subprocess.run([str(ROOT / "scripts" / "check_credits.sh"), str(folder)],
                          capture_output=True, text=True, timeout=30)


def test_the_credits_folder_is_whole():
    done = check(CREDITS)
    assert done.returncode == 0, done.stderr
    assert "is whole" in done.stdout


def test_a_credits_folder_that_lacks_a_license_text_is_not_whole(tmp_path):
    """The build runs the same check on the folder inside the app, and stops on this."""
    for gone in ("Apache-2.0.txt", "OFL-1.1.txt", "README.txt"):
        folder = tmp_path / gone
        shutil.copytree(CREDITS, folder)
        (folder / gone).unlink()
        done = check(folder)
        assert done.returncode == 1 and f"{gone} is missing" in done.stderr, gone
    folder = tmp_path / "linked"                                 # a link to a license is not the license
    shutil.copytree(CREDITS, folder)
    (folder / "Apache-2.0.txt").write_text("https://www.apache.org/licenses/LICENSE-2.0\n")
    done = check(folder)
    assert done.returncode == 1 and "is not the text of the Apache License 2.0" in done.stderr


def test_the_readme_lists_every_piece_of_artwork_and_every_license_text():
    readme = (CREDITS / "README.txt").read_text()
    tracked = subprocess.run(["git", "ls-files", "macos/Resources"], cwd=ROOT, capture_output=True, text=True,
                             timeout=30).stdout.split()
    assert tracked                                               # the artwork the repository carries
    for path in tracked:
        assert path in readme, path
    for text in CREDITS.glob("*.txt"):
        assert text.name == "README.txt" or text.name in readme, text.name


def test_the_rooster_is_the_file_the_credits_say_it_is():
    """Pinned to the commit it came from, in the source's own words, with its checksum: so
    the claim can be checked again later, and a swapped image does not go unnoticed."""
    readme = (CREDITS / "README.txt").read_text()
    rooster = hashlib.sha256((ROOT / "macos" / "Resources" / "rooster.png").read_bytes()).hexdigest()
    assert rooster in readme
    assert "d6a792cb12e3eb7224f4fbf13173a0dded455651" in readme          # a commit, not a branch that moves
    said = " ".join(readme.split())
    assert '"Emoji fonts (under the fonts subdirectory) are under the SIL Open Font License, version 1.1.' in said
    assert 'Tools and most image resources are under the Apache license, version 2.0."' in said


def test_where_the_artwork_came_from_and_how_it_is_rendered_are_each_said_in_one_place():
    """Said in three places, they had already begun to differ. The credits say where the
    artwork came from; scripts/render_icon.sh says how a drawing becomes its picture; the
    drawings and the other scripts point to those two."""
    resources = ROOT / "macos" / "Resources"
    others = [resources / "icon.svg", resources / "icon-small.svg", ROOT / "scripts" / "make_icon.sh",
              ROOT / "scripts" / "build_app.sh"]
    for path in others:
        text = path.read_text()
        assert "Noto" not in text and "Google" not in text, path.name      # the source: the credits' to say
        assert "rsvg-convert" not in text, path.name                         # the rendering: render_icon.sh's
    for path in (resources / "icon.svg", resources / "icon-small.svg", ROOT / "scripts" / "make_icon.sh"):
        text = path.read_text()
        assert "credits/README.txt" in text and "scripts/render_icon.sh" in text, path.name
    assert "rsvg-convert -w 1024 icon.svg -o icon.png" in (ROOT / "scripts" / "render_icon.sh").read_text()
    readme = (CREDITS / "README.txt").read_text()
    assert "scripts/render_icon.sh" in readme and "Apple's own emoji artwork is not licensed" in readme


def test_the_icon_says_which_license_it_is_shared_under():
    """The icon is the rooster and a drawing of our own: without a statement, nobody who
    gets a copy knows what they may do with it."""
    icon = " ".join((CREDITS / "README.txt").read_text().split("The app icon\n------------")[1].split())
    assert "The icon as a whole" in icon and "is shared under the SIL Open Font License, Version 1.1" in icon
    assert "OFL-1.1.txt" in icon and "Apache-2.0.txt" in icon           # and where to read both texts
    assert "Copyright 2013 Google LLC" in icon


def test_the_build_puts_the_credits_in_the_app_and_checks_them_there():
    build = (ROOT / "scripts" / "build_app.sh").read_text()
    assert 'cp -R credits "$APP/Contents/Resources/credits"' in build
    assert 'scripts/check_credits.sh "$APP/Contents/Resources/credits"' in build
