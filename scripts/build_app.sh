#!/bin/bash
# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
# Build dist/agent-frolic.app from a checkout:
#   1. PyInstaller onedir of main.py            -> dist/python/agent-frolic-core
#   2. SwiftPM release build of the host app     -> macos/.build/release/AgentFrolic
#   3. assemble the bundle (binary, Sparkle.framework, the Python tree, icon, Info.plist),
#      and check the macOS minimum (scripts/check_min_macos.py): stop if anything in it needs
#      a newer macOS than Info.plist says, or the Swift executable records another minimum
#   4. sign it: with the Developer ID in the keychain, or ad-hoc with CODESIGN_IDENTITY=-
#   5. ask the signed copy to check itself (it reads no secret and reaches no network)
# Needs: .venv with requirements.txt + requirements-build.txt (or PYTHON=<the python of
# another venv>), Xcode command line tools.
# The version comes from VERSION and is used for both CFBundleShortVersionString and
# CFBundleVersion (Sparkle compares the latter; dotted numbers compare naturally).
#
# This builds the app; scripts/release.sh notarizes it and publishes it as an update.
# Only a release updates itself (WITH_UPDATES=1, which release.sh sets): a copy built here by
# hand is given no feed, so it never asks GitHub and never offers to replace itself.
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
# (The awk reads to the end and prints its first match: one that left at the first match
# would fail the pipeline under pipefail when `security` still had lines to write, and the
# assignment would end the script with nothing said.)
IDENTITY=${CODESIGN_IDENTITY:-$(security find-identity -v -p codesigning 2>/dev/null | awk '/Developer ID Application/ && !n++ {print $2}')}
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
# Only the versions Package.resolved pins: left to itself, swift build moves to a newer
# Sparkle when Package.swift allows one, and writes that into Package.resolved as it goes.
# The tools a release signs with were chosen for the pinned version (sparkle_tools.sh),
# so the framework would no longer be the one they belong to. This way a newer one stops
# the build, and bumping Sparkle stays a change someone makes: the pin and its hashes.
( cd macos && swift build -c release --product AgentFrolic --only-use-versions-from-resolved-file 2>&1 \
      | grep -E "error|warning: unre|Build complete"
  [ "${PIPESTATUS[0]}" -eq 0 ] )              # keep the compiler's verdict, not grep's
[ -x "$BIN" ] || { echo "swift build did not produce $BIN" >&2; exit 1; }
# Sparkle comes as a binary framework that SwiftPM downloads next to the build.
# -print -quit, not `| head -1`: under pipefail a find cut off by head fails the pipeline,
# and the assignment would end the script with nothing said.
SPARKLE_FW=$(find macos/.build/artifacts -type d -name Sparkle.framework -path "*macos-*" -print -quit)
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
# Where the app looks for Sparkle.framework. It needs one place, the one Package.swift sets:
# its own Contents/Frameworks. SwiftPM adds @loader_path, beside the executable, where a
# `swift run` finds the framework and the finished app has nothing; an absolute path into
# the build folder, should a toolchain ever record one, has no place in a copy that goes to
# other Macs. Whatever else is there is taken out, and the build stops unless the one that
# matters is what is left: the app would not start.
EXE="$APP/Contents/MacOS/AgentFrolic"
NEEDED="@executable_path/../Frameworks"
# One path a line, each whole: otool prints "path <path> (offset <n>)", and a path can hold
# spaces (a checkout in "My Checkout"), so not awk's $2.
rpaths() { otool -l "$EXE" | awk '/LC_RPATH/{f=1} f && sub(/^[[:space:]]*path /, "") {sub(/ \(offset [0-9]+\)$/, ""); print; f=0}'; }
FOUND=$(rpaths)             # read whole before any is taken out: the file changes under otool otherwise
while IFS= read -r rp; do
    [ -z "$rp" ] || [ "$rp" = "$NEEDED" ] || install_name_tool -delete_rpath "$rp" "$EXE"
done <<< "$FOUND"
[ "$(rpaths)" = "$NEEDED" ] || { echo "the app's executable must look for its frameworks in $NEEDED alone; it looks in:" >&2; rpaths | sed 's/^/    /' >&2; exit 1; }
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
# Other people's programs inside the app come under licenses of their own. Those of the two
# the Swift host links ask that their text go with every copy: Sparkle's, which has with it
# the notices of what Sparkle itself carries, and SwiftTerm's. They are taken from the very
# packages this copy was built from, so they are the licenses of the versions inside it, and
# go beside the app's own: LICENSE-Sparkle, LICENSE-SwiftTerm. A build that cannot find one
# stops here: the app must not carry either without it.
for package in Sparkle SwiftTerm; do
    [ -s "macos/.build/checkouts/$package/LICENSE" ] \
        || { echo "no license text for $package under macos/.build/checkouts" >&2; exit 1; }
    cp "macos/.build/checkouts/$package/LICENSE" "$APP/Contents/Resources/LICENSE-$package"
done
sed "s/__VERSION__/$VERSION/g" macos/Info.plist > "$APP/Contents/Info.plist"
if [ "${WITH_UPDATES:-0}" != 1 ]; then
    # A copy built to try a change is not to replace itself with the published release, which
    # is what it would offer once a day with the feed in it. Without a feed the app starts
    # no updater at all (AppDelegate.setupUpdater), and Check for Updates stays grey.
    /usr/libexec/PlistBuddy -c "Delete :SUFeedURL" -c "Delete :SUEnableAutomaticChecks" \
        -c "Delete :SUScheduledCheckInterval" "$APP/Contents/Info.plist"
    echo "no update feed in this copy: it is not a release (WITH_UPDATES=1 makes one that updates itself)"
fi
printf 'APPL????' > "$APP/Contents/PkgInfo"

step "macOS minimum"
# Nothing inside may need a newer macOS than Info.plist promises (Homebrew's libraries follow
# the macOS they were built on), or the app would install there and fail to start. And the
# Swift executable must record that same minimum: it is said twice, in Info.plist and in
# Package.swift, and one raised without the other would pass otherwise. The check runs by
# itself: it needs no package, so a build that reuses the Python core (SKIP_PYTHON=1) needs
# no venv for it either.
scripts/check_min_macos.py "$APP"

step "sign"
scripts/sign_app.sh "$APP" "$IDENTITY"

step "self-check of the signed copy"
# Loads ngrok's library, the Keychain backend and macOS's certificate trust (truststore,
# through the Security framework) under the signature just made, then draws
# the setup screen with an empty stand-in for the Keychain: no secret read, no network.
AGENT_FROLIC_VERSION="$VERSION" "$APP/Contents/Resources/agent-frolic-core/agent-frolic-core" --selfcheck

echo
echo "built $APP  version $VERSION  ($(du -sh "$APP" | cut -f1))"
