# Bundled fonts

## Anton (Anton.ttf)
- **Licence:** SIL Open Font License, Version 1.1
- **Source:** https://fonts.google.com/specimen/Anton
- **Commercial use:** permitted, including embedding in video
- **Redistribution:** permitted under the OFL

Anton is the default caption face. It is downloaded automatically on first run
if absent (see `engine/video/fonts.py`) and used via ffmpeg's `fontsdir`, so
caption rendering is identical on every machine.

## Oswald (Oswald.ttf, optional)
- **Licence:** SIL Open Font License, Version 1.1
- **Source:** https://fonts.google.com/specimen/Oswald

## Montserrat (MontserratBlack.ttf, optional)
- **Licence:** SIL Open Font License, Version 1.1
- **Source:** https://fonts.google.com/specimen/Montserrat
- **Note:** this is the *variable* font file. libass has limited variable-font
  support, so it is used for thumbnail sub-text rather than captions.

## System fallbacks
If no OFL font can be downloaded, the engine copies a system font (Arial Black,
Impact, Segoe UI Black, DejaVu Sans Bold or Liberation Sans Bold) into this
directory so `fontsdir` stays self-contained.

**These system fonts are NOT redistributable.** They are copied for local
rendering only and are excluded from version control. Do not ship them.

## Noto Sans, Indic scripts (NotoSans{Devanagari,Tamil,Telugu,Bengali,Gujarati}.ttf)
- **Licence:** SIL Open Font License, Version 1.1
- **Source:** https://github.com/google/fonts/tree/main/ofl
- **Commercial use:** permitted, including embedding in video
- **Redistribution:** permitted under the OFL

These are BUNDLED rather than downloaded on demand, unlike the Latin display
faces. Two reasons:

1. The display faces contain no Indic glyphs at all, so a Hindi caption
   rendered as a row of tofu boxes - not a degraded caption, an unreadable one.
   That surfaced the moment non-English scripts started generating correctly.
2. The deployed box has no Indic fonts installed (only DejaVu), so the
   fallback is tofu rather than a different-looking face. A download that fails
   mid-job would ship a video full of empty rectangles, and committing 2.9 MB
   is a cheap way to make that impossible.

They are the variable font files. libass renders them at their default
instance, which is Regular rather than Black - less punchy than Anton and
completely legible, which is the right trade for a subtitle.
