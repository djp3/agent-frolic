#!/bin/bash
# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
# Sign an assembled agent-frolic.app inside-out with the hardened runtime.
#   scripts/sign_app.sh dist/agent-frolic.app "Developer ID Application: Name (TEAMID)"
#   scripts/sign_app.sh dist/agent-frolic.app -          # ad-hoc, for local builds
set -euo pipefail
APP=${1:?usage: sign_app.sh <app> [identity]}
IDENTITY=${2:--}
cd "$(dirname "$0")/.."
PY_ENT=packaging/python.entitlements

# Developer ID builds get the hardened runtime (notarization requires it); Sparkle.framework
# and the Python tree are signed by the same team, so library validation passes. Ad-hoc
# signatures carry no team, so an ad-hoc hardened-runtime app could not load its own
# frameworks: those builds are signed plainly instead.
opts=(--force)
[ "$IDENTITY" != "-" ] && opts+=(--options runtime --timestamp)
sign() { codesign "${opts[@]}" --sign "$IDENTITY" "$@" 2> >(grep -v "replacing existing signature" >&2); }

CORE="$APP/Contents/Resources/agent-frolic-core"
FW="$APP/Contents/Frameworks/Sparkle.framework"

# 1. Every Mach-O in the Python tree: extension modules, dylibs, the interpreter library.
find "$CORE/_internal" -type f \( -name "*.so" -o -name "*.dylib" -o ! -name "*.*" \) | sort | while read -r f; do
    if file -b "$f" | grep -q "Mach-O"; then sign "$f"; fi
done
# The nested Python.framework is a bundle of its own (sign after its binary, before the app).
if [ -d "$CORE/_internal/Python.framework/Versions/Current" ]; then
    sign "$CORE/_internal/Python.framework"
fi
# 2. The entry executable, with the entitlements PyInstaller needs under the hardened runtime.
sign --entitlements "$PY_ENT" "$CORE/agent-frolic-core"

# 3. Sparkle, as its own instructions for signing it by hand have it (the "Code Signing" part
# of sparkle-project.org/documentation/sandboxing): each helper by itself, never --deep, and
# then the framework. Only the downloader keeps the entitlements it ships with, should it
# have any. The others are signed plainly: kept on Autoupdate, the one it ships with would
# put Sparkle's own application identifier under this app's signature.
sign "$FW/Versions/B/XPCServices/Installer.xpc"
sign --preserve-metadata=entitlements "$FW/Versions/B/XPCServices/Downloader.xpc"
sign "$FW/Versions/B/Autoupdate"
sign "$FW/Versions/B/Updater.app"
sign "$FW"

# 4. The app itself.
sign "$APP"
codesign --verify --deep --strict "$APP"
echo "signed $APP with: $IDENTITY"
