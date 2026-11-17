Credits: the artwork in agent-frolic, and where it came from
=============================================================

This folder goes with the source and with the app (inside it, at
Contents/Resources/credits). It lists every piece of artwork agent-frolic ships, where
each came from, and the license it is shared under. The full text of each license named
here is in this folder.

This is about the artwork only. The code of agent-frolic is under the GNU General Public
License, version 3 or later: see NOTICE and LICENSE, at the top of the source and, in the
app, in Contents/Resources.

  Apache-2.0.txt   the Apache License, Version 2.0
  OFL-1.1.txt      the SIL Open Font License, Version 1.1, as the Noto Emoji repository
                   ships it (its LICENSE file, with its copyright line)


Before adding artwork
---------------------

Anything added here has to be something agent-frolic may pass on, and it needs an entry
below: where it came from, whose it is, its license, and that license's text in this
folder. Apple's own emoji artwork is not licensed for redistribution, so it must not be
used in the icon or anywhere else in the app.


The rooster
-----------

  File:        macos/Resources/rooster.png
  What it is:  U+1F413 ROOSTER from Noto Emoji's 3D set. The file is unmodified.
  Source:      https://github.com/googlefonts/noto-emoji
               the file 3D/png/512/emoji_u1f413.png, as added by commit
               d6a792cb12e3eb7224f4fbf13173a0dded455651 ("Add 3D PNGs", 17 September 2026)
  SHA-256:     5d385c2cee31f456084beda23834ceae76b847b7feb8aed32d086a7820600572
               (rooster.png, the same bytes as the file at that commit)
  Copyright:   Copyright 2013 Google LLC
  License:     In Noto Emoji's own words, as read on 17 November 2026. Its README says:

                 "Emoji fonts (under the fonts subdirectory) are under the SIL Open
                 Font License, version 1.1. Tools and most image resources are under
                 the Apache license, version 2.0."

               It does not say which of its images are the exceptions, and says nothing
               of the 3D set in particular; the 3D folder has no license file of its
               own. The README's link for "Apache license, version 2.0" leads to the
               repository's LICENSE file, and that file is the text of the SIL Open
               Font License, Version 1.1 (it is OFL-1.1.txt here).

               So the rooster is under the Apache License, Version 2.0, as the README
               says, or under the SIL Open Font License, Version 1.1, as the license
               file says. The text of both is in this folder. What follows from that
               for the icon is under "The app icon" below.


The app icon
------------

  Files:       macos/Resources/icon.svg         the drawing
               macos/Resources/icon.png         the drawing at 1024 px
               macos/Resources/icon-small.svg   the barn alone, for the 16 and 32 px sizes
               macos/Resources/icon-small.png   that drawing at 1024 px
               AppIcon.icns                     made from the two .png files by every
                                                build; in the app
               macos/Resources/icon.rendered.sha256
                                                not artwork: the checksums of the files
                                                above as they were when the .png files
                                                were last rendered, which the build checks
  How made:    scripts/render_icon.sh renders the two drawings to the two .png files;
               scripts/make_icon.sh scales those into AppIcon.icns.
  What it is:  The rooster above, scaled, given a soft shadow, and stood in front of a
               barn, a timber frame and a weathervane on a dark rounded square. All but
               the rooster is drawn for agent-frolic and is not part of Noto Emoji. The
               two smallest sizes show the barn alone, with no rooster.
  Copyright:   The rooster: Copyright 2013 Google LLC. The rest of the drawing:
               Copyright (C) 2026 Don Patterson.
  License:     The icon as a whole, in every form listed above and with the parts drawn
               for agent-frolic included, is shared under the SIL Open Font License,
               Version 1.1 (OFL-1.1.txt).

               Why that license. It is not settled which license covers the rooster:
               Noto Emoji's README names the Apache License for its images, and the
               license file it ships is the Open Font License. The icon is made by
               adding to the rooster, so it has to meet whichever applies:
                 - The Open Font License says a work made by adding to something under
                   it must be shared entirely under it. Sharing the whole icon under it
                   does that.
                 - The Apache License lets a work that includes something under it carry
                   other terms, so long as the Apache License's own text and the notices
                   go with it. They do: Apache-2.0.txt and the copyright line above.
               So the icon is shared under the stricter of the two, with the text of
               both beside it.
