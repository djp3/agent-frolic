"""Run a mock-up, and start it again whenever its source changes.

    .venv/bin/python mockup/watch.py              # runs mockup/optionx.py
    .venv/bin/python mockup/watch.py other.py     # or another mock-up in this folder
    .venv/bin/python mockup/watch.py main.py      # or the real app, from the project folder

It watches every .py file in the folder of the program it runs. When one of them is saved,
the running program is stopped, the terminal is put back the way it was, and the program
starts again from the top.

- q inside the mock-up quits both.
- If the mock-up stops with an error (a half-finished edit, a bug), the error stays on
  screen, and the next save starts it again. ctrl+c stops the wait.

Standard library only, so it adds nothing to the pinned dependencies.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import termios
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
POLL = 0.25                      # seconds between looks at the files
SETTLE = 0.4                     # a change counts once the files have been still this long

# What Textual writes when it leaves the screen properly. A mock-up that is stopped from
# outside never gets to write it, so the watcher does: paste and mouse reporting off, line
# wrap on, the Kitty keyboard protocol off, back from the alternate screen, cursor shown.
RESET = ("\x1b[?2004l\x1b[?7h\x1b[?2048l\x1b[?1000l\x1b[?1003l\x1b[?1015l\x1b[?1006l"
         "\x1b[<u\x1b[?1049l\x1b[?25h\x1b[?1004l\x1b[0m")


def watched(target: Path) -> list[Path]:
    return sorted(target.parent.glob("*.py"))


def stamps(target: Path) -> dict[Path, int]:
    found: dict[Path, int] = {}
    for path in watched(target):
        try:
            found[path] = path.stat().st_mtime_ns
        except OSError:                          # mid-save: the editor has moved it aside
            pass
    return found


def changed_since(target: Path, seen: dict[Path, int]) -> dict[Path, int] | None:
    """The new stamps once a change has settled, or None when nothing has changed."""
    now = stamps(target)
    if now == seen:
        return None
    while True:                                  # several saves in a row are one change
        time.sleep(SETTLE)
        later = stamps(target)
        if later == now:
            return now
        now = later


def stop(child: subprocess.Popen) -> bool:
    """Stop the mock-up if it is still running. True when it had to be stopped from outside,
    which means it never tidied the terminal itself."""
    if child.poll() is not None:
        return False
    child.terminate()
    try:
        child.wait(2)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
    return True


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "optionx.py"
    target = Path(name) if Path(name).exists() else HERE / name
    target = target.resolve()
    if not target.exists():
        print(f"watch: {name} is not a file here or in {HERE}", file=sys.stderr)
        return 2

    tty = sys.stdin.fileno() if sys.stdin.isatty() else None
    saved = termios.tcgetattr(tty) if tty is not None else None

    def restore(stopped: bool) -> None:
        """Put the terminal back. The escape codes are written only after a mock-up was
        stopped from outside: one that ended by itself has already written them, and
        writing them twice can move the cursor back over its error message."""
        if tty is not None:
            termios.tcsetattr(tty, termios.TCSANOW, saved)
        if stopped and sys.stdout.isatty():
            sys.stdout.write(RESET)
            sys.stdout.flush()

    def leave(signum, frame) -> None:            # the terminal closed, or someone asked us to stop
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, leave)
    signal.signal(signal.SIGHUP, leave)

    seen = stamps(target)
    child: subprocess.Popen | None = None
    try:
        while True:
            child = subprocess.Popen([sys.executable, str(target)])
            restart = False
            while child.poll() is None:
                time.sleep(POLL)
                now = changed_since(target, seen)
                if now is not None:
                    seen, restart = now, True
                    restore(stop(child))
            restore(False)
            if restart:
                continue
            if child.returncode == 0:            # quit from inside the mock-up
                return 0
            print(f"\nwatch: {target.name} stopped with an error (exit {child.returncode}). "
                  "It starts again at the next save; ctrl+c stops.", flush=True)
            while (now := changed_since(target, seen)) is None:
                time.sleep(POLL)
            seen = now
    except KeyboardInterrupt:
        return 130
    finally:
        restore(child is not None and stop(child))


if __name__ == "__main__":
    sys.exit(main())
