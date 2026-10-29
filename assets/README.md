# Fauxphonic app icon

The original vector mark is a bold teal F with a brass audio pulse forming its middle arm, on a deep navy rounded tile. Created for Fauxphonic on 2026-10-29. It uses geometric paths with no fonts, embedded images or external artwork.

- `fauxphonic-icon.svg`: editable vector source (256-unit square).
- `fauxphonic-icon.png`: 1024 x 1024 RGBA export used by the native masthead and as the app-icon fallback.
- `fauxphonic.ico`: Windows icon with 16, 24, 32, 48, 64, 128 and 256 pixel entries, embedded in GUI, worker and installer executables.

Palette: deep navy `#102A38`, signal teal `#51D8C4`, warm brass `#E9BB70`. Transparent padding and rounded tile corners allow the same icon on light and dark surfaces. Keep the mark and tile together and preserve their proportions.

From the project root, regenerate both raster resources from the SVG with the pinned Qt installation:

```powershell
.venv/Scripts/python.exe scripts/make_windows_icon.py
.venv/Scripts/python.exe scripts/render_icon_contact_sheet.py
```

The contact sheet at `evidence/brand-icons.png` displays the actual embedded ICO entries at native sizes on light and dark backgrounds. The build script regenerates the PNG and ICO before packaging. No image-generation model is required to reproduce these vector-derived resources.
