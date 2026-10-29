Fauxphonic 0.2.2 - Windows preview

Install by double-clicking Fauxphonic-0.2.2-Setup.exe. Choose your installation
folder and whether you want a desktop shortcut. No administrator account,
Python installation, GPU setup, or download during installation is required.
Requires 64-bit Intel/AMD Windows 10 (1809+) or Windows 11, with Windows
Media Foundation available for audio preview. Windows N editions need the
Microsoft Media Feature Pack installed. This preview does not install Windows
components or codec packs.

This installer is unsigned, so Windows may display an unknown-publisher or
SmartScreen warning. Check that the file came from the person you trust.

GET STARTED
1. Open Fauxphonic from the desktop or Start menu.
2. Import an audio file. Choose Master only for a simple first test.
3. Use -19 LUFS for mono or -16 LUFS for stereo as starting loudness targets.
4. Choose Export audio and a NEW WAV, FLAC, MP3, M4A/AAC, or Opus filename.
5. Check the verification result and use Play export to review it.
Compressed exports are decoded to a temporary WAV locally for audition; the
prepared playback is reused when you pause/resume and cleaned up on a source
change or app exit.

WHAT CHANGED
Playback no longer races loading and stops after a fraction of a second.
Speech mastering now retries measured gain/peak correction before giving up.
Noise cleanup measures a steady noise sample instead of assuming one fixed
noise floor. Natural/Strong use different reduction amounts. If no suitable
sample is found, cleanup says it was bypassed; clean audio may need no cleanup.
The leveler defaults do more to balance quiet/loud speech. De-essing adapts to
recording level. Changed settings invalidate old A/B results, and unfocused
mouse-wheel scrolling no longer changes processing controls.

The audio file is the WAV/MP3/etc. The adjacent JSON is its measurement report.
Failed verification withholds the audio and writes a clearly named failed
diagnostic report; it does not occupy the normal output/report names. Silent
or very short clips show an explicit measurement exception, not a normal pass.

For conservative noise reduction and phrase leveling, assign a mono voice track
the Speech role and choose Natural speech. Strong cleanup applies more noise
reduction and should be previewed. Stereo, music and ambience bypass mono-only
noise reduction; the processing report explains which stages actually ran.
Originals remain unchanged. Do not trim speaker files independently to align them.

Build A/B preview for a selected region up to 30 seconds, then switch between the
aligned original and processed versions. The first uncached preview renders
the full program before extracting that region, stopping at the file's end.
Short clips are supported. Loudness matching changes
comparison playback only. Advanced controls include gain curves, EQ, bounded
de-essing, music ducking, and opt-in conservative bleed attenuation. Unknown
speech overlap is preserved; these controls do not identify speakers.

Optional stems keep the entire project timeline and are pre-master. Stems
remain lossless WAV when the main delivery file is MP3, AAC or Opus. They are
not individually normalized and need not sum to the mastered delivery file.

Neural denoising, dereverberation and automatic microphone synchronization are
unavailable. Processing is CPU-only. Human listening approval, Auphonic
equivalence and compatibility testing on a 16 GB GPU are not claimed.

UNINSTALL
Use Windows Settings > Apps > Installed apps > Fauxphonic > Uninstall.
Recordings, exports, saved projects, and your application data are preserved.
Close Fauxphonic before installing an update or uninstalling it. Setup and
uninstall will refuse to proceed while Fauxphonic or its audio worker is open.
The withdrawn 0.1.0 preview must be uninstalled before installing 0.2.2; its
installer cannot safely update that release while older files may be running.

Included third-party license notices are installed with the application.
