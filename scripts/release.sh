#!/bin/bash
# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
# Cut a release of the Mac app and publish it where Sparkle looks for it.
#
#   scripts/release.sh               # build, notarize, publish
#   DRY_RUN=1 scripts/release.sh     # build, zip and write the appcast into dist/, then stop:
#                                    # nothing is sent to Apple or GitHub, nothing is tagged
#
# Reads VERSION, builds dist/agent-frolic.app signed with a Developer ID from the packages in
# requirements.lock (a new venv, hash-checked: scripts/locked_venv.sh), notarizes and
# staples it, zips it, writes a Sparkle appcast, tags v<VERSION>, and creates the GitHub
# release with the zip and appcast.xml attached. Two things are signed with the
# agent-frolic EdDSA key: the zip, and the appcast itself, which says which zip is the
# update and what its notes are. The app asks for both (SURequireSignedFeed and
# SUVerifyUpdateBeforeExtraction in macos/Info.plist), so whoever could change what GitHub
# serves could still not offer a copy an update of their choosing, or notes of their own.
# The notes in release-notes/<VERSION>.md go into the appcast, where Sparkle's update
# window shows them, and onto the GitHub release.
# Installed apps poll https://github.com/djp3/agent-frolic/releases/latest/download/appcast.xml,
# so creating the release is what ships the update.
#
# The repository is public: the notes, like everything else in it, use made-up names.
#
# One-time setup:
#   - a "Developer ID Application" certificate in the login keychain
#   - notarization credentials: a notarytool keychain profile named agent-frolic
#       xcrun notarytool store-credentials agent-frolic --apple-id <id> --team-id <team> --password <app-specific>
#     (NOTARY_PROFILE=<name> uses a profile that already exists under another name), or
#     NOTARY_KEY_ID / NOTARY_ISSUER_ID / NOTARY_KEY_FILE (an App Store Connect API key)
#   - the Sparkle key, in the login keychain under the account agent-frolic. Its public half
#     is SUPublicEDKey in macos/Info.plist:
#       "$(scripts/fetch_sparkle_tools.sh)/bin/generate_keys" --account agent-frolic -p     # prints that public key
#     Apps already installed accept updates signed with this key only, so keep a copy of it
#     somewhere safe:  generate_keys --account agent-frolic -x <file>  exports it.
#     SPARKLE_PRIVATE_KEY_FILE=<file> signs with an exported key instead of the keychain's.
#   - gh logged in with push access to the repo
#
# Each release:
#   echo 0.2.0 > VERSION
#   $EDITOR release-notes/0.2.0.md      # what is new, written for people who use the app;
#                                       # Sparkle's window is small: short paragraphs and - lists
#   git add VERSION release-notes && git commit -m "Release 0.2.0"
#   scripts/release.sh
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(tr -d '[:space:]' < VERSION)
TAG="v$VERSION"
REPO=${GITHUB_REPO:-djp3/agent-frolic}
NOTARY_PROFILE=${NOTARY_PROFILE:-agent-frolic}
SPARKLE_ACCOUNT=${SPARKLE_ACCOUNT:-agent-frolic}
DRY_RUN=${DRY_RUN:-0}
APP=dist/agent-frolic.app
ZIP=dist/agent-frolic-$VERSION.zip
NOTES=release-notes/$VERSION.md

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { echo "release: $*" >&2; exit 1; }

step "preflight $TAG"
# Sign by certificate hash rather than by name: a keychain can hold two certificates with
# the same name (a renewed one beside the old), and codesign rejects an ambiguous name.
IDENTITY=${CODESIGN_IDENTITY:-$(security find-identity -v -p codesigning | awk '/Developer ID Application/ {print $2; exit}')}
[ -n "$IDENTITY" ] || die "no 'Developer ID Application' identity in the keychain (or set CODESIGN_IDENTITY)"
IDENTITY_NAME=$(security find-identity -v -p codesigning | awk -F'"' -v h="$IDENTITY" '$0 ~ h {print $2; exit}')
[ -s "$NOTES" ] || die "no release notes in $NOTES; write what is new in $VERSION first"
NOTES_SUFFIX=""
if [ "$DRY_RUN" = 1 ]; then
    echo "dry run: nothing will be notarized, tagged or published"
else
    DIRTY=$(git status --porcelain)       # includes untracked files, which PyInstaller would bundle
    if [ -n "$DIRTY" ]; then
        [ "${ALLOW_DIRTY:-0}" = 1 ] || die "working tree is not clean (ALLOW_DIRTY=1 overrides):
$DIRTY"
        echo "WARNING: building from a dirty tree; the release notes will say so" >&2
        NOTES_SUFFIX="

> Built from a dirty working tree (ALLOW_DIRTY=1); the tag may not reproduce this build."
    fi
    git rev-parse -q --verify "refs/tags/$TAG" >/dev/null && die "tag $TAG already exists locally; bump VERSION, or delete it if the previous attempt did not publish"
    git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null 2>&1 && die "tag $TAG already exists on origin; bump VERSION first"
    gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1 && die "release $TAG already exists on GitHub"
    if [ -n "${NOTARY_KEY_FILE:-}" ]; then
        NOTARY_ARGS=(--key "$NOTARY_KEY_FILE" --key-id "${NOTARY_KEY_ID:?NOTARY_KEY_ID}" --issuer "${NOTARY_ISSUER_ID:?NOTARY_ISSUER_ID}")
    else
        xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" >/dev/null 2>&1 \
            || die "no notarytool keychain profile '$NOTARY_PROFILE'; create it with: xcrun notarytool store-credentials $NOTARY_PROFILE ...
(or name one that exists: NOTARY_PROFILE=<name> scripts/release.sh)"
        NOTARY_ARGS=(--keychain-profile "$NOTARY_PROFILE")
    fi
fi
TOOLS=$(scripts/fetch_sparkle_tools.sh)
# The key this release will be signed with must be the one whose public half the app carries
# (SUPublicEDKey): installed copies accept no other. Looked at here, where it takes a moment,
# and not left to generate_appcast, which comes after the build and the notarization and
# only leaves the signature out. A dry run looks too: it is how a key is tried.
PUBLIC_KEY=$(/usr/libexec/PlistBuddy -c "Print :SUPublicEDKey" macos/Info.plist 2>/dev/null) \
    || die "macos/Info.plist has no SUPublicEDKey"
if [ -n "${SPARKLE_PRIVATE_KEY_FILE:-}" ]; then
    [ -r "$SPARKLE_PRIVATE_KEY_FILE" ] || die "cannot read SPARKLE_PRIVATE_KEY_FILE ($SPARKLE_PRIVATE_KEY_FILE)"
    SPARKLE_KEY_ARGS=(--ed-key-file "$SPARKLE_PRIVATE_KEY_FILE")
    KEY_FROM="the key in $SPARKLE_PRIVATE_KEY_FILE"
    HELD_KEY=$(scripts/sparkle_public_key.sh "$SPARKLE_PRIVATE_KEY_FILE") \
        || die "$SPARKLE_PRIVATE_KEY_FILE does not hold a Sparkle key (as generate_keys -x exports one)"
else
    SPARKLE_KEY_ARGS=(--account "$SPARKLE_ACCOUNT")
    KEY_FROM="the key in the login keychain under the account '$SPARKLE_ACCOUNT'"
    HELD_KEY=$("$TOOLS/bin/generate_keys" --account "$SPARKLE_ACCOUNT" -p 2>/dev/null) \
        || die "no Sparkle key in the login keychain under the account '$SPARKLE_ACCOUNT'
(SPARKLE_ACCOUNT=<name> names another, SPARKLE_PRIVATE_KEY_FILE=<file> signs with an exported key)"
fi
[ "$HELD_KEY" = "$PUBLIC_KEY" ] || die "$KEY_FROM is not the one the app trusts:
its public half is $HELD_KEY, and SUPublicEDKey in macos/Info.plist is $PUBLIC_KEY.
Installed copies would refuse an update signed with it."
echo "identity: ${IDENTITY_NAME:-$IDENTITY} ($IDENTITY)"
echo "sparkle tools: $TOOLS"
echo "sparkle key: $KEY_FROM, the one the app trusts"

step "locked packages"
# The app is built from a new venv holding exactly requirements.lock, every file checked
# against its hash, never from .venv: whatever is installed there by hand would ship.
PYTHON=$(scripts/locked_venv.sh build/release-venv)
echo "built from requirements.lock: $PYTHON"

step "build"
# A release always rebuilds the Python side: SKIP_PYTHON=1 left over in the shell must not
# ship an older build of it. A dry run may reuse it.
[ "$DRY_RUN" = 1 ] || export SKIP_PYTHON=0
PYTHON="$PYTHON" CODESIGN_IDENTITY="$IDENTITY" scripts/build_app.sh

if [ "$DRY_RUN" = 1 ]; then
    step "notarize: skipped (dry run)"
else
    step "notarize"
    rm -f dist/notarize.zip
    ditto -c -k --keepParent "$APP" dist/notarize.zip
    xcrun notarytool submit dist/notarize.zip "${NOTARY_ARGS[@]}" --wait
    xcrun stapler staple "$APP"
    rm -f dist/notarize.zip
    spctl --assess --type execute --verbose=2 "$APP"
fi

step "archive + appcast"
rm -rf dist/appcast "$ZIP"
mkdir -p dist/appcast
ditto -c -k --keepParent "$APP" "$ZIP"
cp "$ZIP" dist/appcast/
# generate_appcast pairs notes with the archive whose name they share, and embeds them, so
# nothing else needs hosting. It signs the zip always, and the appcast as a whole, notes and
# all, because the app in the zip says it requires that (SURequireSignedFeed): the feed's
# signature is the comment at its end.
cp "$NOTES" "dist/appcast/$(basename "$ZIP" .zip).md"
"$TOOLS/bin/generate_appcast" "${SPARKLE_KEY_ARGS[@]}" \
    --download-url-prefix "https://github.com/$REPO/releases/download/$TAG/" \
    --link "https://github.com/$REPO/releases" \
    --embed-release-notes \
    -o dist/appcast/appcast.xml dist/appcast
grep -q "sparkle:edSignature" dist/appcast/appcast.xml || die "appcast.xml carries no EdDSA signature for the zip"
grep -q '<description sparkle:format="markdown">' dist/appcast/appcast.xml || die "appcast.xml carries no release notes"
# The appcast's own signature: without it, every copy from this release on would refuse the
# feed and stay where it is. Present, and good: Sparkle's tool checks it as the app will.
grep -q "<!-- sparkle-signatures:" dist/appcast/appcast.xml \
    || die "appcast.xml is not signed as a whole: is SURequireSignedFeed still set in macos/Info.plist?"
"$TOOLS/bin/sign_update" --verify "${SPARKLE_KEY_ARGS[@]}" dist/appcast/appcast.xml >/dev/null \
    || die "the signature on appcast.xml does not check out"
{ cat "$NOTES"; printf '%s\n' "$NOTES_SUFFIX"; } > dist/release-notes.md

if [ "$DRY_RUN" = 1 ]; then
    echo
    echo "dry run finished; nothing was published. What a release would attach:"
    echo "  $ZIP"
    echo "  dist/appcast/appcast.xml"
    exit 0
fi

step "publish $TAG"
git push origin HEAD                      # the release must be reproducible from the repo
# The tag is created last and removed again if publishing fails, so a re-run starts clean.
git tag -a "$TAG" -m "agent-frolic $VERSION"
if ! { git push origin "$TAG" || { sleep 5; git push origin "$TAG"; }; }; then   # GitHub occasionally rejects a fresh tag once
    git tag -d "$TAG" >/dev/null
    die "pushing tag $TAG failed; nothing was published, re-run to try again"
fi
# --generate-notes adds GitHub's "Full Changelog" link after the notes.
if ! gh release create "$TAG" --repo "$REPO" --title "agent-frolic $VERSION" --generate-notes \
        --notes-file dist/release-notes.md "$ZIP" dist/appcast/appcast.xml; then
    git push --delete origin "$TAG" >/dev/null 2>&1 || true
    git tag -d "$TAG" >/dev/null
    die "creating the GitHub release failed; the tag was removed again, re-run to try again"
fi
echo
echo "released: https://github.com/$REPO/releases/tag/$TAG"
