# Third-party components

The application bundle includes Python and its PSF license; PySide6, shiboken6,
and Qt under the applicable LGPL terms; NumPy; SoundFile and libsndfile; psutil;
ONNX Runtime; the Silero VAD model; FFmpeg; and the PyInstaller bootloader.
`dependency-inventory.json` lists the exact Python distributions and every
native executable, DLL, Python extension, and model file in this build, with
SHA-256 and license/source attribution. The matching license texts are in
`licenses/`.
Python 3.12.10's OpenSSL 3.0.16 runtime DLLs are licensed under Apache-2.0;
the matching license text is included as `licenses/OpenSSL-Apache-2.0.txt`.

FFmpeg and ffprobe 8.1.1 are project-built under LGPL-2.1-or-later. FFmpeg
statically links LAME 4.0 (LGPL-2.0-or-later) and Opus 1.5.2 (BSD-3-Clause) for
MP3 and Opus delivery encoding. The matching FFmpeg and LAME source archives,
LAME license texts, configure flags, build recipes, pinned hashes, and
reproducibility record are in `legal/sources/FFmpeg/`; Opus source is in
`legal/sources/libsndfile/`. The original Gyan GPL build is not included.
Processing uses these executables through a separate worker process.

Qt 6.11.2 and PySide6 6.11.2 source archives are in `legal/sources/Qt/` to
correspond to the Qt libraries shipped here. Preview uses the Windows Media
Foundation backend; Qt's alternate FFmpeg multimedia plugin is not included.
The Qt LGPL and GPL license texts are in `licenses/`.

SoundFile uses a project-built libsndfile 1.2.2 DLL under LGPL-2.1-or-later.
FLAC 1.4.3, Ogg 1.3.5, Vorbis 1.3.7, and Opus 1.5.2 are statically linked
under their BSD-3-Clause licenses. MPEG support is disabled. Matching source
archives, hashes, build recipe, and toolchain distribution are in
`legal/sources/libsndfile/`.

The Silero VAD ONNX model is MIT-licensed; its license and pinned model
manifest are included. No FastEnhancer weights or other research models ship.

The build does not prevent replacement or debugging of dynamically linked Qt
libraries. `dependency-inventory.json` records the files actually shipped,
rather than all packages installed in the development environment.
