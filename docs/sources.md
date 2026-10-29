# Sources actually consulted

Checked 2026-09-25. Upstream benchmarks are not used as local measurements.

- FastEnhancer [official README](https://github.com/aask1357/fastenhancer/blob/f85223bd546b27f39dc0744e0310dcd246f750a4/README.md), [wav2wav reference](https://github.com/aask1357/fastenhancer/blob/f85223bd546b27f39dc0744e0310dcd246f750a4/scripts/test_onnx.py), [spec2spec reference](https://github.com/aask1357/fastenhancer/blob/f85223bd546b27f39dc0744e0310dcd246f750a4/scripts/test_onnx_spec.py), [license](https://github.com/aask1357/fastenhancer/blob/f85223bd546b27f39dc0744e0310dcd246f750a4/LICENSE), [48 kHz release](https://github.com/aask1357/fastenhancer/releases/tag/onnx-48khz-v1), [ONNX delay documentation](https://aask1357.github.io/fastenhancer/onnx/).
- [FFmpeg loudnorm documentation](https://ffmpeg.org/ffmpeg-filters.html#loudnorm) and locally executed 8.1.1 build/version/configuration. [Exact Gyan release](https://github.com/GyanD/codexffmpeg/releases/tag/8.1.1). Initial gyan.dev package URL returned 404; replaced with the verified GitHub release URL and published digest.
- [Qt PyInstaller deployment](https://doc.qt.io/qtforpython-6/deployment/deployment-pyinstaller.html). Local frozen output remains authoritative for packaging tests.
- NVIDIA skill catalog checked through its live JSON after its CLI was blocked by offline npm cache. No strong catalog skill was needed for a basic read-only nvidia-smi diagnostic; no additional skill was installed.

The supplied specification's other linked sources have not all been revalidated. They remain candidate research until their integration starts.
