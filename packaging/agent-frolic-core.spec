# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Python side of agent-frolic ("agent-frolic-core").
#
# Built by scripts/build_app.sh into dist/python/agent-frolic-core/ (onedir). The Swift
# app copies that folder into agent-frolic.app/Contents/Resources and runs
# agent-frolic-core inside its SwiftTerm view. Nothing is signed here: build_app.sh
# signs the whole bundle inside-out afterwards (scripts/sign_app.sh).
import os
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas, binaries, hiddenimports = [(os.path.join(ROOT, "VERSION"), ".")], [], []
# textual and rich ship data files and import modules lazily; ngrok is a native library
# that tunnel.py imports only when webhooks are turned on.
for pkg in ("textual", "rich", "ngrok"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
# keychain.py names the Keychain backend itself, inside a function.
hiddenimports += ["keyring.backends.macOS", "keyring.backends.macOS.api"]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "unittest", "pydoc", "doctest", "test", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="agent-frolic-core",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="agent-frolic-core")
