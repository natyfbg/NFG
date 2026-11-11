# Brand assets

- `natty-logo-v5-black-gold.png` — source logo (4096 px, transparent). Edit this, then
  regenerate the files below.
- `logo-128.png` — the glyph on a cream rounded tile, for dark backgrounds (the black
  glyph would disappear on them). Not shown in the top bar for now (wordmark instead).
- `favicon-32.png`, `apple-touch-icon.png` (180 px), `icon-512.png` (future PWA
  manifest) — same tile, used as browser and home-screen icons.

The app only shows these when the files exist (`_brand_assets()` in `app.py`).
