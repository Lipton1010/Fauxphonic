# Fauxphonic visual identity

The app icon is an original teal F with a brass audio pulse forming its middle arm, on a deep navy rounded tile. Its bold silhouette and open spacing keep the letter readable at taskbar sizes. The editable SVG, PNG and Windows ICO share the same geometry and palette; asset details and regeneration commands are in `assets/README.md`.

The interface uses a dark petrol console surround and waveform surface, cream control panels, teal signal accents, brass details and a coral primary action. Segoe UI keeps native Windows controls readable. No Bricolage Grotesque is used. Waveform bars come from measured audio; decorative grid lines are not simulated meters.

## Comparison with the user's other apps

References were inspected read-only; those projects were not modified.

| App | Inspected reference | Distinguishing treatment |
|---|---|---|
| Storybird | `D:/Audiobook_Pipeline/app/static/index.html` | Cream paper/cards, navy text, blue primary actions, yellow accents, Georgia title accents and persistent left navigation. |
| Video upscaler | Separate native Windows application | Light gray Windows form, dark gray text and dark teal start button. |
| Fauxphonic | `src/local_audio_studio/gui.py` | Native audio-console grouping, dark petrol masthead/signal view, cream control facings, coral action, brass accents and waveform F branding. |

The apps remain related only as the user's tools; Fauxphonic does not reuse their artwork, navigation shell or color hierarchy.

## Windows resources

`scripts/make_windows_icon.py` renders `assets/fauxphonic-icon.svg` into the 1024 pixel RGBA PNG and 16, 24, 32, 48, 64, 128 and 256 pixel ICO entries using Qt. The same icon is embedded in the GUI, worker and Setup executables and loaded for the running window. Installer desktop/Start-menu shortcuts reference the GUI executable. The stable Windows application identity is `Fauxphonic.LocalAudioStudio`; pinning to the taskbar is left to the user.

`evidence/brand-icons.png` shows actual icon sizes on light and dark backgrounds. Local UI captures and package/resource checks are identified in `PROJECT_STATE.md`. Source assets and future builds use this identity; previously installed applications and historical releases require a rebuilt package to show it. Audio algorithms and their quality/hardware evidence boundaries are unchanged by this branding update.
