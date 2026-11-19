# Brand assets

- `natty-logo-v5-black-gold.png` — source logo (4096 px, transparent). Edit this, then
  regenerate the files below.
- `logo-128.png` — the glyph on a cream rounded tile, for dark backgrounds (the black
  glyph would disappear on them). Not shown in the top bar for now (wordmark instead).
- `favicon-32.png`, `apple-touch-icon.png` (180 px), `icon-192.png`, `icon-512.png` — same
  tile, used as browser and home-screen icons (the last two are in the app manifest).
- `icon-maskable-512.png` — the same tile with the glyph shrunk to fit inside the circle
  Android crops home-screen icons to (made from `icon-512.png`).

The app only shows these when the files exist (`_brand_assets()` in `app.py`).
