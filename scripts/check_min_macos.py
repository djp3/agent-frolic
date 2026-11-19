#!/usr/bin/env python3
# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""Refuse an app bundle in which any binary needs a newer macOS than the bundle declares, or
whose own executable was built for another macOS than the bundle declares.

    scripts/check_min_macos.py dist/agent-frolic.app

Info.plist's LSMinimumSystemVersion is what Finder checks before it opens the app, and what
the update feed tells Sparkle. Every Mach-O file also records the oldest macOS it runs on
(its minos), chosen by whoever built it. Homebrew builds its Python for the macOS it is
built on, so after a macOS upgrade the next build would quietly need the new macOS while
still declaring the old one. build_app.sh runs this before signing, so it stops the build.

The minimum is said in two places, which must agree: LSMinimumSystemVersion in macos/Info.plist
and the platform in macos/Package.swift, which the Swift executable records as its own. A
binary that needs less than the declared minimum is fine (most that are not Homebrew's do);
the app's own executable needing less, or more, is one of the two places left behind.

The rules are agent-farm's check's, which the two apps keep alike. What each file needs is
read from the file itself: a Mach-O file says which processor each of its slices is for and
which macOS the slice needs, in a header of fixed shape.
"""
from __future__ import annotations

import plistlib
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

THIN = {b"\xcf\xfa\xed\xfe": ("<", 32), b"\xce\xfa\xed\xfe": ("<", 28),     # 64- and 32-bit, as on disk: the
        b"\xfe\xed\xfa\xcf": (">", 32), b"\xfe\xed\xfa\xce": (">", 28)}     # byte order, the header's length
UNIVERSAL = {b"\xca\xfe\xba\xbe": (">iiIII", 20), b"\xca\xfe\xba\xbf": (">iiQQII", 32),   # each slice's entry;
             b"\xbe\xba\xfe\xca": None, b"\xbf\xba\xfe\xca": None}          # 64-bit offsets (lipo -fat64)
MACHO_MAGIC = set(THIN) | set(UNIVERSAL)
ARM64 = 0x0100000C                       # the processor every slice that runs on this app's Macs is for
LC_VERSION_MIN_MACOSX, LC_BUILD_VERSION, MACOS = 0x24, 0x32, 1


def version(v: str) -> tuple[int, ...]:
    """'27.0' -> (27, 0, 0), so '27' and '27.0' compare equal."""
    parts = [int(p) for p in v.split(".")]
    return tuple(parts + [0] * (3 - len(parts)))


def dotted(v: int) -> str:
    """A version as a Mach-O file packs it (xxxx.yy.zz): 0x001B0000 -> '27.0'."""
    patch = v & 0xFF
    return f"{v >> 16}.{(v >> 8) & 0xFF}" + (f".{patch}" if patch else "")


class Unreadable(Exception):
    """A file that starts like a Mach-O one and cannot be read as one: (path, why)."""


def _need(data: bytes, size: int, what: str) -> bytes:
    if len(data) < size:
        raise ValueError(f"it ends inside {what}")
    return data


def _slice(fh, start: int) -> list[str] | None:
    """The macOS minimums one slice records, starting at `start` in the file: LC_BUILD_VERSION's
    minos where its platform is macOS (not the Mac Catalyst record of a library built for
    both), or the older LC_VERSION_MIN_MACOSX's version. None for a slice that is not for
    Apple silicon: an Intel one never runs on this app's Macs, so what it needs does not count."""
    fh.seek(start)
    head = fh.read(32)
    if head[:4] not in THIN:
        raise ValueError("a slice of it does not start like a Mach-O file")
    order, length = THIN[head[:4]]
    cputype, _, _, ncmds, sizeofcmds = struct.unpack(order + "IIIII", _need(head, 24, "its header")[4:24])
    if cputype != ARM64:
        return None
    fh.seek(start + length)
    commands = _need(fh.read(sizeofcmds), sizeofcmds, "its load commands")
    found, at = [], 0
    for _ in range(ncmds):
        cmd, size = struct.unpack_from(order + "II", _need(commands[at:], 8, "a load command"))
        if size < 8 or at + size > len(commands):
            raise ValueError("a load command in it has an impossible size")
        if cmd == LC_BUILD_VERSION:
            platform, minos = struct.unpack_from(order + "II", _need(commands[at + 8:at + size], 8, "a load command"))
            if platform == MACOS:
                found.append(dotted(minos))
        elif cmd == LC_VERSION_MIN_MACOSX:
            (oldest,) = struct.unpack_from(order + "I", _need(commands[at + 8:at + size], 4, "a load command"))
            found.append(dotted(oldest))
        at += size
    return found


def minimums(f: Path) -> tuple[bool, list[str]]:
    """(whether the file has a slice that runs on Apple silicon, the macOS minimums those
    slices record). A universal file is every one of its slices."""
    try:
        with open(f, "rb") as fh:
            head = fh.read(8)
            if head[:4] in THIN:
                found = [_slice(fh, 0)]
            else:
                entry = UNIVERSAL.get(head[:4])
                if entry is None:
                    raise ValueError("its first bytes are a universal file's, in the byte order none is written in")
                (count,) = struct.unpack(">I", _need(head, 8, "its header")[4:8])
                if count > 32:                                  # a Java class file starts 0xcafebabe too, and
                    raise ValueError(f"it says it holds {count} slices")    # this is its version number
                layout, width = entry
                table = _need(fh.read(count * width), count * width, "its table of slices")
                starts = [struct.unpack_from(layout, table, i * width)[2] for i in range(count)]
                found = [_slice(fh, start) for start in starts]
    except (ValueError, struct.error, OSError) as e:
        raise Unreadable(f, str(e)) from None
    here = [slice_ for slice_ in found if slice_ is not None]
    return bool(here), [v for slice_ in here for v in slice_]


def binaries(app: Path):
    for f in sorted(app.rglob("*")):
        if f.is_file() and not f.is_symlink():
            with open(f, "rb") as fh:
                if fh.read(4) in MACHO_MAGIC:
                    yield f


@dataclass
class Found:
    """What the binaries in a bundle need, beside what the bundle declares."""
    declared: str                                             # LSMinimumSystemVersion
    newest: str = "0"                                         # the most any of them needs
    checked: int = 0                                          # the ones with a slice that runs on Apple silicon
    elsewhere: int = 0                                        # the ones for other processors alone: never run here
    too_new: list[tuple[Path, str]] = field(default_factory=list)    # the ones that need more than declared, and what
    silent: list[Path] = field(default_factory=list)          # the ones that run here and record no minimum at all


def check(app: Path) -> Found:
    declared = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())["LSMinimumSystemVersion"]
    found = Found(declared)
    for f in binaries(app):
        runs_here, needs = minimums(f)
        if not runs_here:
            found.elsewhere += 1
            continue
        found.checked += 1
        if not needs:                           # nothing to judge it by: that is not the same as needing nothing
            found.silent.append(f.relative_to(app))
            continue
        need = max(needs, key=version)          # a universal binary needs what its neediest slice needs
        if version(need) > version(found.newest):
            found.newest = need
        if version(need) > version(declared):
            found.too_new.append((f.relative_to(app), need))
    return found


def executable_minimum(app: Path) -> tuple[Path, str | None] | None:
    """The app's own executable and the minimum it records, which is the platform in
    macos/Package.swift; None when Info.plist names no executable."""
    name = plistlib.loads((app / "Contents" / "Info.plist").read_bytes()).get("CFBundleExecutable")
    if not name:
        return None
    exe = app / "Contents" / "MacOS" / name
    _, found = minimums(exe)
    return exe.relative_to(app), (max(found, key=version) if found else None)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: scripts/check_min_macos.py <app bundle>, e.g. dist/agent-frolic.app", file=sys.stderr)
        return 2
    app = Path(argv[1])
    try:
        found = check(app)
        declared, newest, too_new, n = found.declared, found.newest, found.too_new, found.checked
        own = executable_minimum(app)
    except Unreadable as e:
        path, why = e.args
        print(f"{path} starts like a Mach-O binary (so does a Java class file) and can't be read "
              f"as one: {why}. The check can't tell what macOS it needs; find out what it is "
              "before shipping it.", file=sys.stderr)
        return 1
    failed = False
    if own and (own[1] is None or version(own[1]) != version(declared)):
        # The two places that state the minimum must agree; check() alone only notices
        # Package.swift being the higher one.
        print(f"Info.plist declares macOS {declared} (LSMinimumSystemVersion), but the app's own "
              f"executable ({own[0]}) records macOS {own[1] or '(none)'}. Set the platform in "
              f"macos/Package.swift to {declared} too.", file=sys.stderr)
        failed = True
    if too_new:
        print(f"Info.plist declares macOS {declared} (LSMinimumSystemVersion), but {len(too_new)} "
              f"of {n} binaries need a newer macOS:", file=sys.stderr)
        for path, v in too_new[:10]:
            print(f"  {path}: macOS {v}", file=sys.stderr)
        if len(too_new) > 10:
            print(f"  ... and {len(too_new) - 10} more", file=sys.stderr)
        print("Build them for an older macOS, or raise the minimum: LSMinimumSystemVersion in "
              "macos/Info.plist, the platform in macos/Package.swift, and the README.", file=sys.stderr)
        failed = True
    if found.silent:
        print(f"{len(found.silent)} of {n} binaries run on Apple silicon and record no minimum macOS at "
              "all, so the check can't tell what they need:", file=sys.stderr)
        for path in found.silent:
            print(f"  {path}", file=sys.stderr)
        print("Find out what they are before shipping them.", file=sys.stderr)
        failed = True
    if failed:
        return 1
    others = f" ({found.elsewhere} more are for other processors alone, and never run here)" if found.elsewhere else ""
    print(f"{n} binaries checked{others}: none needs a macOS newer than {declared}, the declared "
          f"minimum (the newest any needs is {newest}), and the app's executable records the same minimum")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
