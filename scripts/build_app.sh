#!/bin/bash
# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
# Build dist/agent-frolic.app from a checkout:
#   1. PyInstaller onedir of main.py            -> dist/python/agent-frolic-core
#   2. SwiftPM release build of the host app     -> macos/.build/release/AgentFrolic
#   3. assemble the bundle (binary, Sparkle.framework, the Python tree, icon, Info.plist),
#      and check that nothing in it needs a newer macOS than Info.plist says
#   4. sign it: with the Developer ID in the keychain, or ad-hoc with CODESIGN_IDENTITY=-
#   5. ask the signed copy to check itself (it reads no secret and reaches no network)
# Needs: .venv with requirements.txt + requirements-build.txt (or PYTHON=<the python of
# another venv>), Xcode command line tools.
# The version comes from VERSION and is used for both CFBundleShortVersionString and
# CFBundleVersion (Sparkle compares the latter; dotted numbers compare naturally).
#
# This builds the app; scripts/release.sh notarizes it and publishes it as an update.
# HTTPS certificates are judged by macOS (truststore), not by the certificate file of the
# Python it was built with, which another Mac may not have; the self-check proves it.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(tr -d '[:space:]' < VERSION)
PY=${PYTHON:-./.venv/bin/python3}         # release.sh passes one made from the lock (locked_venv.sh)
APP=dist/agent-frolic.app
# Sign with the first Developer ID Application certificate when the keychain has one (by
# hash, since a renewed certificate can sit beside the old one under the same name), else
# ad-hoc. A Keychain item belongs to the signature of the program that made it, so builds
# signed the same way every time read the app's secrets without asking again; an ad-hoc
# build is a new program to the Keychain each time it is rebuilt.
IDENTITY=${CODESIGN_IDENTITY:-$(security find-identity -v -p codesigning 2>/dev/null | awk '/Developer ID Application/ {print $2; exit}')}
IDENTITY=${IDENTITY:--}

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

if [ "${SKIP_PYTHON:-0}" = 1 ] && [ -x dist/python/agent-frolic-core/agent-frolic-core ]; then
    step "python core: reusing dist/python/agent-frolic-core (SKIP_PYTHON=1)"
else
    step "python core: PyInstaller onedir ($($PY --version))"
    $PY -m PyInstaller --noconfirm --clean --log-level WARN \
        --distpath dist/python --workpath build/pyinstaller packaging/agent-frolic-core.spec
fi

step "host app: swift build"
BIN=macos/.build/release/AgentFrolic
rm -f "$BIN"                                   # so an existing binary can only come from this build
( cd macos && swift build -c release --product AgentFrolic 2>&1 | grep -E "error|warning: unre|Build complete"
  [ "${PIPESTATUS[0]}" -eq 0 ] )              # keep the compiler's verdict, not grep's
[ -x "$BIN" ] || { echo "swift build did not produce $BIN" >&2; exit 1; }
# Sparkle comes as a binary framework that SwiftPM downloads next to the build.
SPARKLE_FW=$(find macos/.build/artifacts -type d -name Sparkle.framework -path "*macos-*" | head -1)
[ -d "$SPARKLE_FW" ] || { echo "Sparkle.framework not found under macos/.build/artifacts" >&2; exit 1; }

step "icon"
# Made afresh by every build, which takes a second. Git ignores the .icns, so one left by an
# earlier build or made by hand could otherwise go into a release that the tagged commit
# cannot make again, and release.sh's check for uncommitted changes cannot see it. Going by
# which file is newer is not enough: a copied or restored file keeps an old date.
ICNS=macos/Resources/AppIcon.icns
ICON=macos/Resources/icon.png              # the drawing at 1024 px
ICON_SMALL=macos/Resources/icon-small.png  # the barn alone, for the 16 and 32 px sizes
scripts/render_icon.sh --check             # the two pictures are the drawings as they stand, or stop
scripts/make_icon.sh "$ICON" "$ICON_SMALL" "$ICNS"

step "assemble $APP ($VERSION)"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Frameworks" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/AgentFrolic"
# SwiftPM records an absolute rpath into .build for the Sparkle artifact; the app only
# needs the @executable_path/../Frameworks one set in Package.swift.
otool -l "$APP/Contents/MacOS/AgentFrolic" | awk '/LC_RPATH/{f=1} f&&/ path /{print $2; f=0}' | while read -r rp; do
    case "$rp" in /*) install_name_tool -delete_rpath "$rp" "$APP/Contents/MacOS/AgentFrolic" 2>/dev/null || true ;; esac
done
cp -R "$SPARKLE_FW" "$APP/Contents/Frameworks/"
cp -R dist/python/agent-frolic-core "$APP/Contents/Resources/agent-frolic-core"
cp "$ICNS" "$APP/Contents/Resources/AppIcon.icns"
# The license the code is under, and the notice that says so and where the source is, go in
# every copy of the app: the license asks for both.
cp LICENSE NOTICE "$APP/Contents/Resources/"
# The artwork's credits, with the full text of each license it is shared under, go in every
# copy of the app: the licenses say so. The build stops if the folder in the app is not whole.
cp -R credits "$APP/Contents/Resources/credits"
scripts/check_credits.sh "$APP/Contents/Resources/credits"
sed "s/__VERSION__/$VERSION/g" macos/Info.plist > "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"

step "macOS minimum"
# Nothing inside may need a newer macOS than Info.plist promises (Homebrew's libraries follow
# the macOS they were built on), or the app would install there and fail to start.
scripts/check_macos_minimum.sh "$APP"

step "sign"
scripts/sign_app.sh "$APP" "$IDENTITY"

step "self-check of the signed copy"
# Loads ngrok's library, the Keychain backend and macOS's certificate trust (truststore,
# through the Security framework) under the signature just made, then draws
# the setup screen with an empty stand-in for the Keychain: no secret read, no network.
AGENT_FROLIC_VERSION="$VERSION" "$APP/Contents/Resources/agent-frolic-core/agent-frolic-core" --selfcheck

echo
echo "built $APP  version $VERSION  ($(du -sh "$APP" | cut -f1))"
