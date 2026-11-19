# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
"""The build refuses an app in which a binary needs a newer macOS than Info.plist declares, or
whose own executable was built for another one (scripts/check_min_macos.py, run by
build_app.sh before signing)."""
import importlib.util
import plistlib
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_min_macos", ROOT / "scripts" / "check_min_macos.py")
check_min_macos = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = check_min_macos           # a dataclass looks its own module up by name
_spec.loader.exec_module(check_min_macos)

ARM64, X86_64 = check_min_macos.ARM64, 0x01000007


def packed(v: str) -> int:
    """A version as a Mach-O file packs it: '27.0' -> 0x001B0000."""
    major, minor, patch = ([int(part) for part in v.split(".")] + [0, 0])[:3]
    return major << 16 | minor << 8 | patch


def build_version(minos: str, platform: int = 1) -> bytes:
    return struct.pack("<IIIIII", 0x32, 24, platform, packed(minos), packed("27.2"), 0)      # LC_BUILD_VERSION


def version_min(v: str) -> bytes:
    return struct.pack("<IIII", 0x24, 16, packed(v), packed("27.0"))                         # LC_VERSION_MIN_MACOSX


OTHER = struct.pack("<II", 0x19, 24) + b"__\xff\xfeTEXT" + b"\0" * 8      # a command that is neither, its name not UTF-8


def thin(*commands: bytes, cputype: int = ARM64, subtype: int = 0) -> bytes:
    """A Mach-O file of one slice, down to the end of its load commands: all the check reads."""
    body = b"".join(commands)
    return struct.pack("<IIIIIIII", 0xFEEDFACF, cputype, subtype, 2, len(commands), len(body), 0, 0) + body


def universal(*slices: bytes, wide: bool = False) -> bytes:
    """A universal file of these slices; `wide`, with 64-bit offsets (lipo -fat64)."""
    width = 32 if wide else 20
    table, body, start = b"", b"", 8 + len(slices) * width
    for one in slices:
        cputype, subtype = struct.unpack_from("<II", one, 4)
        where = (cputype, subtype, start + len(body), len(one))
        table += struct.pack(">IIQQII", *where, 0, 0) if wide else struct.pack(">IIIII", *where, 0)
        body += one
    return (b"\xca\xfe\xba\xbf" if wide else b"\xca\xfe\xba\xbe") + struct.pack(">I", len(slices)) + table + body


def written(tmp_path, data: bytes, name: str = "x") -> Path:
    (tmp_path / name).write_bytes(data)
    return tmp_path / name


def test_reads_the_macos_minimum_of_each_apple_silicon_slice_and_nothing_else(tmp_path):
    # not the command between them, the Mac Catalyst record (platform 6), or the Intel slice
    several = universal(thin(build_version("27.0"), OTHER, build_version("28.0", platform=6)),
                        thin(version_min("26.0"), subtype=2),                    # arm64e
                        thin(version_min("29.0"), cputype=X86_64))
    assert check_min_macos.minimums(written(tmp_path, several)) == (True, ["27.0", "26.0"])
    wide = universal(thin(build_version("27.1")), wide=True)
    assert check_min_macos.minimums(written(tmp_path, wide)) == (True, ["27.1"])
    assert check_min_macos.minimums(written(tmp_path, thin(build_version("25.1")))) == (True, ["25.1"])


def test_a_file_for_intel_alone_never_runs_here_whether_it_is_one_slice_or_a_universal_file(tmp_path):
    """A file of one slice does not say in words which processor it is for; its header does."""
    intel = thin(build_version("28.0"), cputype=X86_64)
    assert check_min_macos.minimums(written(tmp_path, intel)) == (False, [])
    assert check_min_macos.minimums(written(tmp_path, universal(intel))) == (False, [])


def test_an_apple_silicon_slice_counts_whatever_kind_of_arm64_it_is(tmp_path):
    for subtype in (0, 2, 3, 0x80000002):            # arm64, arm64e, one with no name yet, arm64e with a flag
        odd = universal(thin(build_version("29.0"), subtype=subtype), thin(build_version("12.0"), cputype=X86_64))
        assert check_min_macos.minimums(written(tmp_path, odd)) == (True, ["29.0"]), subtype


def test_a_file_that_starts_like_a_program_and_is_not_one_is_refused_by_name(tmp_path):
    whole = thin(build_version("29.0"), OTHER)
    not_programs = {
        "cut short": whole[:40],                                              # its load commands are not all there
        "a java class": b"\xca\xfe\xba\xbe\x00\x00\x00\x41" + b"\0" * 24,       # 0xcafebabe, then its version
        "a universal file with a slice past its end": universal(whole)[:30],
        "a universal file, bytes swapped": b"\xbe\xba\xfe\xca" + b"\0" * 28,
        "a command of no size": thin(struct.pack("<II", 0x32, 0)),
        "a command past the end": thin(struct.pack("<II", 0x32, 4096) + b"\0" * 16),
        "a universal file of something else": (b"\xca\xfe\xba\xbe" + struct.pack(">IIIIII", 1, ARM64, 0, 28, 8, 0)
                                               + b"notmacho"),
    }
    for name, data in not_programs.items():
        with pytest.raises(check_min_macos.Unreadable) as refused:
            check_min_macos.minimums(written(tmp_path, data, "odd"))
        assert refused.value.args[0] == tmp_path / "odd" and refused.value.args[1], name


def test_versions_compare_as_numbers_and_are_read_as_a_file_packs_them():
    v = check_min_macos.version
    assert v("27") == v("27.0") == v("27.0.0")
    assert v("10.13") < v("11.0") < v("26.4") < v("27.0") < v("27.0.1")
    assert [check_min_macos.dotted(packed(said)) for said in ("27.0", "10.13", "26.4", "27.0.1")] == [
        "27.0", "10.13", "26.4", "27.0.1"]


def swift_platform(package_swift: str) -> str | None:
    """The macOS version in Package.swift's platforms, in either form: .macOS("27.0") or .macOS(.v27)."""
    m = re.search(r'\.macOS\(\s*(?:"([\d.]+)"|\.v(\d+(?:_\d+)*))\s*\)', package_swift)
    return None if m is None else (m.group(1) or m.group(2).replace("_", "."))


def test_both_forms_of_the_swift_platform_are_read():
    assert swift_platform('platforms: [.macOS("27.0")],') == "27.0"
    assert swift_platform("platforms: [.macOS(.v28)],") == "28"
    assert swift_platform("platforms: [.macOS(.v10_15)],") == "10.15"
    assert swift_platform("platforms: [.iOS(.v17)],") is None


def test_the_declared_minimum_matches_the_swift_package():
    declared = plistlib.loads((ROOT / "macos" / "Info.plist").read_bytes())["LSMinimumSystemVersion"]
    swift = swift_platform((ROOT / "macos" / "Package.swift").read_text())
    assert swift is not None, "no .macOS(...) platform found in macos/Package.swift"
    v = check_min_macos.version
    assert v(declared) == v(swift), f"Info.plist declares {declared}, Package.swift {swift}"


def _fake_bundle(tmp_path, declared, files, executable=None):
    """A bundle of these files (name -> what it holds), declaring this minimum."""
    app = tmp_path / "fake.app"
    (app / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
    info = {"LSMinimumSystemVersion": declared} | ({"CFBundleExecutable": executable} if executable else {})
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    for name, data in files.items():
        (app / "Contents" / "MacOS" / name).write_bytes(data)
    return app


def test_a_universal_binary_is_reported_with_its_neediest_slice(tmp_path):
    uni = universal(thin(build_version("27.1")), thin(build_version("28.0"), subtype=2))
    found = check_min_macos.check(_fake_bundle(tmp_path, "27.0", {"uni": uni}))
    assert found.too_new == [(Path("Contents/MacOS/uni"), "28.0")] and found.newest == "28.0"   # not 27.1, the first


def test_an_intel_file_that_needs_more_is_no_reason_to_stop_a_build(tmp_path):
    intel = thin(build_version("28.0"), cputype=X86_64)
    files = {"intel": intel, "both": universal(intel, thin(build_version("26.0"))), "ours": thin(build_version("26.0"))}
    found = check_min_macos.check(_fake_bundle(tmp_path, "26.0", files))
    assert found.too_new == [] and found.newest == "26.0"
    assert (found.checked, found.elsewhere) == (2, 1)             # the one for Intel alone is not among the checked


def test_a_file_that_runs_here_and_records_no_minimum_stops_the_build(tmp_path, capsys):
    """Nothing to judge it by is not the same as needing nothing. It was passed over, and counted as checked."""
    files = {"AgentFrolic": thin(build_version("27.0")), "silent": thin(OTHER), "intel": thin(OTHER, cputype=X86_64)}
    app = _fake_bundle(tmp_path, "27.0", files, executable="AgentFrolic")
    found = check_min_macos.check(app)
    assert found.silent == [Path("Contents/MacOS/silent")] and (found.checked, found.elsewhere) == (2, 1)
    assert check_min_macos.main(["check", str(app)]) == 1
    err = capsys.readouterr().err
    assert "Contents/MacOS/silent" in err and "record no minimum macOS" in err and "intel" not in err
    (app / "Contents" / "MacOS" / "silent").unlink()              # without it the build goes on, and says
    assert check_min_macos.main(["check", str(app)]) == 0         # what it did not judge
    assert "1 binaries checked (1 more are for other processors alone" in capsys.readouterr().out


def test_the_build_stops_when_info_plist_and_the_swift_executable_disagree(tmp_path, capsys):
    # Info.plist raised to 28.0, Package.swift left at 27.0: no binary needs more than 28.0,
    # so only the executable's own minimum gives it away.
    app = _fake_bundle(tmp_path, "28.0", {"AgentFrolic": thin(build_version("27.0"))}, executable="AgentFrolic")
    assert check_min_macos.main(["check", str(app)]) == 1
    err = capsys.readouterr().err
    assert "Contents/MacOS/AgentFrolic" in err and "records macOS 27.0" in err and "Package.swift" in err


def test_the_build_stops_when_info_plist_names_no_executable(tmp_path, capsys):
    """There is then nothing to set beside the declared minimum: it passed, by not being checked."""
    app = _fake_bundle(tmp_path, "27.0", {"AgentFrolic": thin(build_version("26.0"))})
    assert check_min_macos.main(["check", str(app)]) == 1
    captured = capsys.readouterr()
    assert "names no executable (CFBundleExecutable)" in captured.err and "same minimum" not in captured.out


def test_an_executable_that_is_not_in_the_bundle_is_said_to_be_missing(tmp_path, capsys):
    """Not that it records no minimum, nor that Package.swift is to change: the name is wrong."""
    files = {"AgentFrolic": thin(build_version("27.0")), "Notes": b"not a program at all"}
    for named in ("AgentFrolik", "Notes"):                        # no such file; a file that is no program
        app = _fake_bundle(tmp_path / named, "27.0", files, executable=named)
        assert check_min_macos.main(["check", str(app)]) == 1
        err = capsys.readouterr().err
        assert f"holds no program at Contents/MacOS/{named}" in err and "Package.swift" not in err, named


def test_the_build_goes_on_when_they_agree(tmp_path, capsys):
    app = _fake_bundle(tmp_path, "27", {"AgentFrolic": thin(build_version("27.0"))}, executable="AgentFrolic")
    assert check_min_macos.main(["check", str(app)]) == 0
    assert "the app's executable records the same minimum" in capsys.readouterr().out


def test_the_wrong_arguments_print_how_to_run_it(capsys):
    assert check_min_macos.main(["check"]) == 2
    assert capsys.readouterr().err.startswith("usage: scripts/check_min_macos.py <app bundle>")


def test_every_mach_o_header_is_recognized_and_nothing_else(tmp_path):
    heads = {"thin64": b"\xcf\xfa\xed\xfe", "thin32": b"\xce\xfa\xed\xfe", "fat": b"\xca\xfe\xba\xbe",
             "fat64": b"\xca\xfe\xba\xbf", "fat64-swapped": b"\xbf\xba\xfe\xca", "text": b"#!/b"}
    for name, head in heads.items():
        (tmp_path / name).write_bytes(head + b"\0" * 28)
    assert sorted(f.name for f in check_min_macos.binaries(tmp_path)) == sorted(set(heads) - {"text"})


LS = max(check_min_macos.minimums(Path("/bin/ls"))[1], key=check_min_macos.version)     # what a real program records


def _bundle(tmp_path, declared):
    """A bundle whose executable is a real program, /bin/ls, declaring this minimum."""
    app = tmp_path / "x.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    info = {"LSMinimumSystemVersion": declared, "CFBundleExecutable": "ls"}
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    shutil.copy("/bin/ls", app / "Contents" / "MacOS" / "ls")              # a real Mach-O
    (app / "Contents" / "MacOS" / "notes.txt").write_text("not a binary")
    return app


def test_a_binary_that_needs_a_newer_macos_fails_the_build(tmp_path):
    app = _bundle(tmp_path, "10.0")
    found = check_min_macos.check(app)
    assert found.checked == 1 and found.declared == "10.0"
    assert [str(p) for p, _ in found.too_new] == ["Contents/MacOS/ls"]
    run = subprocess.run([sys.executable, ROOT / "scripts" / "check_min_macos.py", app], capture_output=True, text=True)
    assert run.returncode == 1 and "Contents/MacOS/ls" in run.stderr


def test_a_file_that_cannot_be_read_as_a_program_stops_the_build_with_its_name(tmp_path, capsys):
    app = _bundle(tmp_path, "99.0")
    odd = app / "Contents" / "Resources" / "Hello.class"          # a Java class file starts 0xcafebabe too
    odd.parent.mkdir()
    odd.write_bytes(b"\xca\xfe\xba\xbe\x00\x00\x00\x41" + b"\x00" * 24)
    assert check_min_macos.main(["check", str(app)]) == 1
    err = capsys.readouterr().err
    assert "Hello.class" in err and "Traceback" not in err


def test_a_bundle_within_its_declared_minimum_passes(tmp_path):
    app = _bundle(tmp_path, LS)
    found = check_min_macos.check(app)
    assert found.checked == 1 and found.too_new == [] and check_min_macos.version(found.newest) > (10, 0, 0)
    run = subprocess.run([sys.executable, ROOT / "scripts" / "check_min_macos.py", app], capture_output=True, text=True)
    assert run.returncode == 0 and f"none needs a macOS newer than {LS}" in run.stdout
