#!/bin/bash
# Copyright (C) 2026 Don Patterson
# SPDX-License-Identifier: GPL-3.0-or-later
# Check that a credits folder is whole: its README, and the full text of each license the
# README names. The licenses of the artwork say their text must go with every copy, so
# scripts/build_app.sh runs this on the folder inside the app and stops if it fails.
#     usage: scripts/check_credits.sh <credits folder>
set -euo pipefail

dir=${1:?usage: scripts/check_credits.sh <credits folder>}
fail() { echo "credits: $*" >&2; exit 1; }

[ -f "$dir/README.txt" ] || fail "$dir/README.txt is missing"

# The Apache License 2.0 exactly as apache.org publishes it (LICENSE-2.0.txt).
APACHE=cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30
[ -f "$dir/Apache-2.0.txt" ] || fail "$dir/Apache-2.0.txt is missing"
[ "$(shasum -a 256 "$dir/Apache-2.0.txt" | cut -d' ' -f1)" = "$APACHE" ] \
    || fail "$dir/Apache-2.0.txt is not the text of the Apache License 2.0"

# The SIL Open Font License 1.1 as the Noto Emoji repository ships it, copyright line and all.
[ -f "$dir/OFL-1.1.txt" ] || fail "$dir/OFL-1.1.txt is missing"
grep -q "SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007" "$dir/OFL-1.1.txt" \
    || fail "$dir/OFL-1.1.txt is not the text of the SIL Open Font License 1.1"
grep -q "Copyright 2013 Google LLC" "$dir/OFL-1.1.txt" \
    || fail "$dir/OFL-1.1.txt has lost its copyright line"
grep -q "OTHER DEALINGS IN THE FONT SOFTWARE" "$dir/OFL-1.1.txt" \
    || fail "$dir/OFL-1.1.txt is cut short"

for name in Apache-2.0.txt OFL-1.1.txt; do
    grep -q "$name" "$dir/README.txt" || fail "$dir/README.txt does not name $name"
done
echo "credits: $dir is whole"
