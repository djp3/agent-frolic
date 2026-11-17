# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The app icon: scripts/make_icon.sh scales the two drawings (macos/Resources/icon.png, and
icon-small.png for 16 and 32 px) to the sizes an .icns holds, with the tools every Mac has."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MAKE = ROOT / "scripts" / "make_icon.sh"
ICON = ROOT / "macos" / "Resources" / "icon.png"
SMALL = ROOT / "macos" / "Resources" / "icon-small.png"


def make(source: Path, out: Path, small: Path = SMALL) -> subprocess.CompletedProcess:
    return subprocess.run([str(MAKE), str(source), str(small), str(out)], capture_output=True, text=True,
                          timeout=120)


def size(image: Path) -> tuple[int, int]:
    said = subprocess.run(["/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight", str(image)],
                          capture_output=True, text=True, timeout=30).stdout
    return (int(re.search(r"pixelWidth: (\d+)", said).group(1)),
            int(re.search(r"pixelHeight: (\d+)", said).group(1)))


def entries(icns: Path, into: Path) -> dict[str, tuple[int, int]]:
    """What an .icns holds: each entry's name and its size in pixels."""
    subprocess.run(["/usr/bin/iconutil", "-c", "iconset", str(icns), "-o", str(into)], check=True, timeout=60)
    return {image.name: size(image) for image in sorted(into.glob("*.png"))}


def test_the_icon_holds_every_size_macos_asks_for(tmp_path):
    out = tmp_path / "fresh" / "clone" / "AppIcon.icns"            # a folder that is not there yet
    done = make(ICON, out)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == f"wrote {out}"
    assert entries(out, tmp_path / "held.iconset") == {
        "icon_16x16.png": (16, 16), "icon_16x16@2x.png": (32, 32),
        "icon_32x32.png": (32, 32), "icon_32x32@2x.png": (64, 64),
        "icon_128x128.png": (128, 128), "icon_128x128@2x.png": (256, 256),
        "icon_256x256.png": (256, 256), "icon_256x256@2x.png": (512, 512),
        "icon_512x512.png": (512, 512), "icon_512x512@2x.png": (1024, 1024)}


def test_each_size_is_scaled_once(tmp_path):
    """Ten names, seven sizes: 32, 256 and 512 px are each used twice, not made twice."""
    log = tmp_path / "scaled"
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "sips").write_text(                                  # notes each scaling (sips -z), then does it
        f'#!/bin/bash\n[ "$1" = -z ] && echo "$2 $(basename "$4")" >> "{log}"\nexec /usr/bin/sips "$@"\n')
    (tools / "sips").chmod(0o755)
    done = subprocess.run([str(MAKE), str(ICON), str(SMALL), str(tmp_path / "AppIcon.icns")], capture_output=True,
                          text=True, timeout=120, env={**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"})
    assert done.returncode == 0, done.stderr
    assert log.read_text().splitlines() == [                      # the two smallest from the barn alone
        "16 icon-small.png", "32 icon-small.png",
        "64 icon.png", "128 icon.png", "256 icon.png", "512 icon.png", "1024 icon.png"]
    assert len(entries(tmp_path / "AppIcon.icns", tmp_path / "held.iconset")) == 10


def test_it_says_how_it_is_used_when_it_is_not_given_three_names(tmp_path):
    done = subprocess.run([str(MAKE), str(ICON), str(tmp_path / "AppIcon.icns")], capture_output=True, text=True,
                          timeout=30)
    assert done.returncode == 2
    assert "usage: scripts/make_icon.sh <icon.png> <icon-small.png> <out.icns>" in done.stderr


def test_the_two_smallest_sizes_are_the_barn_alone(tmp_path):
    """At 16 and 32 px the whole scene runs together into a smudge: those sizes are made from
    the second drawing, and are not the first one scaled down."""
    out = tmp_path / "AppIcon.icns"
    assert make(ICON, out).returncode == 0
    held = tmp_path / "held.iconset"
    entries(out, held)

    def scaled(source: Path, px: int) -> bytes:
        to = tmp_path / f"{source.stem}-{px}.png"
        subprocess.run(["/usr/bin/sips", "-z", str(px), str(px), str(source), "--out", str(to)], check=True,
                       capture_output=True, timeout=60)
        return to.read_bytes()

    # 64 px is the whole scene, stored as scaled. (The 16 and 32 px entries are stored another
    # way inside an .icns, so their bytes cannot be compared; the scaling test names their source.)
    assert (held / "icon_32x32@2x.png").read_bytes() == scaled(ICON, 64)
    assert (held / "icon_32x32@2x.png").read_bytes() != scaled(SMALL, 64)
    assert scaled(SMALL, 32) != scaled(ICON, 32)                     # and the two drawings do differ


def test_a_picture_that_is_not_there_is_said_to_be_missing(tmp_path):
    """Not answered with how the script is used: it was used rightly."""
    done = make(tmp_path / "icon.png", tmp_path / "AppIcon.icns")
    assert done.returncode == 1 and "usage" not in done.stderr
    assert f"make_icon: there is no {tmp_path / 'icon.png'}" in done.stderr
    assert not (tmp_path / "AppIcon.icns").exists()


def test_only_a_png_is_scaled(tmp_path):
    """The drawing itself (icon.svg) would be scaled without its rooster, and reported as
    done; a file that is a PNG in name only would too."""
    named = tmp_path / "icon.png"
    named.write_text("a PNG in name only")
    for wrong in (ROOT / "macos" / "Resources" / "icon.svg", named):
        done = make(wrong, tmp_path / "AppIcon.icns")
        assert done.returncode == 1 and f"make_icon: {wrong} is not a PNG" in done.stderr, wrong
        assert "wrote" not in done.stdout and not (tmp_path / "AppIcon.icns").exists()
    done = make(ICON, tmp_path / "AppIcon.icns", small=ROOT / "macos" / "Resources" / "icon-small.svg")
    assert done.returncode == 1 and "icon-small.svg is not a PNG" in done.stderr     # the small one is checked too


def test_a_picture_of_the_wrong_size_is_refused_not_stretched(tmp_path):
    """A square of 1024 px or more: a smaller one would be blown up, and one that is not
    square drawn out of shape, each with nothing said."""
    def scaled(name: str, high: int, wide: int) -> Path:
        out = tmp_path / name
        subprocess.run(["/usr/bin/sips", "-z", str(high), str(wide), str(ICON), "--out", str(out)],
                       check=True, capture_output=True, timeout=60)
        return out

    broken = tmp_path / "broken.png"
    broken.write_bytes(b"\x89PNG\r\n\x1a\n" + b"a PNG by its first bytes, and nothing after")
    for wrong, why in ((scaled("small.png", 512, 512), "is 512 px: the icon's largest size is 1024 px"),
                       (scaled("wide.png", 1024, 2048), "is 2048 by 1024 px: the icon is square"),
                       (broken, "cannot be read as a picture")):
        done = make(wrong, tmp_path / "AppIcon.icns")
        assert done.returncode == 1 and f"make_icon: {wrong} {why}" in done.stderr, (wrong, done.stderr)
        assert not (tmp_path / "AppIcon.icns").exists()
    assert make(scaled("large.png", 2048, 2048), tmp_path / "AppIcon.icns").returncode == 0   # larger is fine


def test_the_drawing_writes_each_shape_once():
    """The barn's wall is its fill and the clip for what is drawn on it: written twice, the two
    can come apart and leave slivers."""
    drawing = (ROOT / "macos" / "Resources" / "icon.svg").read_text()
    assert drawing.count("M100 440 V252 L152 152 L256 86 L360 152 L412 252 V440 Z") == 1
    assert drawing.count('href="#wallshape"') == 2               # the fill, and the clip
    assert drawing.count("V440") == 12                           # the wall, and the eleven boards in one path
    assert "839.6800000000001" not in drawing


RENDER = ROOT / "scripts" / "render_icon.sh"


def check_rendered(folder: Path) -> subprocess.CompletedProcess:
    return subprocess.run([str(RENDER), "--check", str(folder)], capture_output=True, text=True, timeout=60)


def test_the_pictures_are_the_drawings_as_they_stand():
    """The pictures are kept in git so a build needs no SVG tools: this is what says a
    drawing was changed and its picture left behind."""
    done = check_rendered(ROOT / "macos" / "Resources")
    assert done.returncode == 0, done.stderr


def test_a_drawing_changed_without_its_picture_rendered_again_is_caught(tmp_path):
    resources = ROOT / "macos" / "Resources"
    for changed in ("icon.svg", "icon-small.svg", "rooster.png", "icon.png"):
        folder = tmp_path / changed
        folder.mkdir()
        for name in ("icon.svg", "icon-small.svg", "rooster.png", "icon.png", "icon-small.png",
                     "icon.rendered.sha256"):
            (folder / name).write_bytes((resources / name).read_bytes())
        assert check_rendered(folder).returncode == 0
        with open(folder / changed, "ab") as f:                   # the smallest change there is
            f.write(b"\n")
        done = check_rendered(folder)
        assert done.returncode == 1, changed
        assert "changed since the pictures were rendered" in done.stderr and changed in done.stderr
        assert "scripts/render_icon.sh" in done.stderr           # and what to do about it


def test_rendering_gives_the_pictures_that_are_kept(tmp_path):
    """Where the renderer is installed: the kept pictures are what it makes of the kept
    drawings, to the byte, and it records what it made them from."""
    if shutil.which("rsvg-convert") is None:
        pytest.skip("rsvg-convert is not installed")
    resources = ROOT / "macos" / "Resources"
    for name in ("icon.svg", "icon-small.svg", "rooster.png"):
        (tmp_path / name).write_bytes((resources / name).read_bytes())
    done = subprocess.run([str(RENDER), str(tmp_path)], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "icon.rendered.sha256").read_text() == (resources / "icon.rendered.sha256").read_text()
    assert check_rendered(tmp_path).returncode == 0


def test_the_build_checks_the_pictures_before_it_makes_the_icon():
    step = (ROOT / "scripts" / "build_app.sh").read_text().split('step "icon"')[1].split("step ")[0]
    assert step.index("scripts/render_icon.sh --check") < step.index("scripts/make_icon.sh")


def test_every_build_makes_the_icon_afresh():
    """Never one left in the folder, which git ignores: by its date it could look newer than
    the picture and still not be made from it."""
    build = (ROOT / "scripts" / "build_app.sh").read_text()
    step = build.split('step "icon"')[1].split("step ")[0]
    assert '\nscripts/make_icon.sh "$ICON" "$ICON_SMALL" "$ICNS"\n' in step    # on a line of its own, under
                                                                                # no condition
    assert "-nt" not in step and "\nif " not in step


def test_the_build_makes_the_icon_with_it():
    build = (ROOT / "scripts" / "build_app.sh").read_text()
    assert "scripts/make_icon.sh" in build and "make_icon.swift" not in build
    assert build.count("macos/Resources/icon.png") == 1          # the picture's path is written once
