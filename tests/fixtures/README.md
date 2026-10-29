The VAD regression audio is new synthetic test speech, generated locally with
Microsoft David Desktop using the committed `generate_leveler_tts.ps1` script.
The script uses Windows SAPI through Windows PowerShell 5.1 in STA mode.
`provenance.json` records the exact text and voice. No audit or user recording
is included.

To recreate it, create an empty scratch directory and run:

```powershell
powershell.exe -NoProfile -STA -File tests/fixtures/generate_leveler_tts.ps1 -Destination <scratch>
tools/ffmpeg.exe -hide_banner -nostdin -y -i <scratch>/speech.wav -map 0:a:0 -ac 1 -ar 48000 -c:a pcm_s16le tests/fixtures/silero-leveling-tts.wav
```

The checked-in 48 kHz WAV hash is recorded in `provenance.json`.
