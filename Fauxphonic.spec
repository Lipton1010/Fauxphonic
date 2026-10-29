from PyInstaller.utils.hooks import copy_metadata
from PyInstaller.building.utils import format_binaries_and_datas
from pathlib import Path
from importlib.metadata import packages_distributions
import os
import sys

data = [('tools/ffmpeg.exe', 'tools'), ('tools/ffprobe.exe', 'tools'),
        ('tools/manifest.json', 'tools'), ('tools/LICENSE', 'tools')]
sndfile = Path('.cache/sndfile-controlled/package/libsndfile_x64.dll')
sndfile_manifest = Path('tools/libsndfile_manifest.json')
if not sndfile.is_file() or not sndfile_manifest.is_file():
    raise RuntimeError('Pinned project-built libsndfile is missing; run scripts/build_sndfile.ps1.')
import hashlib
expected_sndfile = __import__('json').loads(sndfile_manifest.read_text(encoding='utf-8'))['dll_sha256']
if hashlib.sha256(sndfile.read_bytes()).hexdigest() != expected_sndfile:
    raise RuntimeError('Pinned project-built libsndfile hash mismatch.')
gui = Analysis(['run_desktop.py'], pathex=['src'], datas=data, excludes=[
    'onnxruntime', 'pytest', 'torch', 'PySide6.QtPdf', 'PySide6.QtQml',
    'PySide6.QtQuick', 'PySide6.QtVirtualKeyboard',
])
worker_data = data + [
    ('models/silero_vad.onnx', 'models'),
    ('models/silero-vad-manifest.json', 'models'),
]
worker = Analysis(['run_worker.py'], pathex=['src'], datas=worker_data,
                  excludes=['PySide6', 'pytest', 'torch'])
distributions_by_module = packages_distributions()
for analysis in (gui, worker):
    modules = {str(entry[0]).split('.')[0] for entry in analysis.pure}
    packages = {distribution for module in modules
                for distribution in distributions_by_module.get(module, ())}
    for package in sorted(packages):
        analysis.datas.extend((destination, source, 'DATA')
                              for destination, source in format_binaries_and_datas(copy_metadata(package)))
for analysis in (gui, worker):
    def is_old_libsndfile(entry):
        return (Path(entry[0]).name.lower() == 'libsndfile_x64.dll'
                and '_soundfile_data' in (str(entry[0]) + str(entry[1])).lower())
    analysis.binaries = [entry for entry in analysis.binaries if not is_old_libsndfile(entry)]
    analysis.datas = [entry for entry in analysis.datas if not is_old_libsndfile(entry)]
    analysis.datas.append(('_soundfile_data/libsndfile_x64.dll', str(sndfile), 'DATA'))
# Preview explicitly uses the Windows Media Foundation backend. Remove Qt's
# alternate FFmpeg backend and unrelated PDF, QML/Quick, and keyboard modules.
unused_qt = ('/qt6pdf.dll', '/qt6qml', '/qt6quick', '/qt6virtualkeyboard.dll',
             '/plugins/imageformats/qpdf.dll', '/plugins/imageformats/qgif.dll',
             '/plugins/imageformats/qicns.dll', '/plugins/imageformats/qtga.dll',
             '/plugins/imageformats/qtiff.dll', '/plugins/imageformats/qwbmp.dll',
             '/plugins/imageformats/qwebp.dll', '/plugins/multimedia/ffmpegmediaplugin.dll',
             '/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll',
             '/plugins/tls/qopensslbackend.dll',
             '/plugins/qml', '/qml/', '/avcodec-61.dll', '/avformat-61.dll',
             '/avutil-59.dll', '/swresample-5.dll', '/swscale-8.dll')
gui.binaries = [entry for entry in gui.binaries if not any(
    part in str(entry[1]).lower().replace('\\', '/') for part in unused_qt
)]
gui.datas = [entry for entry in gui.datas if not any(
    part in str(entry[0]).lower().replace('\\', '/') for part in unused_qt
)]
for analysis in (gui, worker):
    def is_unneeded_openssl(entry):
        filenames = {Path(str(value)).name.lower() for value in entry[:2]}
        return bool(filenames & {'libcrypto-3-x64.dll', 'libssl-3-x64.dll'})
    def is_windows_api_set(entry):
        filenames = {Path(str(value)).name.lower() for value in entry[:2]}
        return any(name.startswith(('api-ms-win-', 'ext-ms-win-')) for name in filenames)
    analysis.binaries = [entry for entry in analysis.binaries
                         if not is_unneeded_openssl(entry) and not is_windows_api_set(entry)]
    analysis.datas = [entry for entry in analysis.datas
                      if not is_unneeded_openssl(entry) and not is_windows_api_set(entry)]
allowed = [Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(),
           Path(os.environ['SystemRoot']).resolve(), Path('tools').resolve()]
def is_allowed_source(source):
    path = Path(source).resolve()
    return any(path.is_relative_to(base) for base in allowed)

# PyInstaller may find redistributable CRT DLLs beside unrelated host packages.
# Use the approved interpreter's copies, and rely on the Windows 10 system UCRT.
approved_names = {Path(source).name.lower()
                  for analysis in (gui, worker)
                  for _, source, _ in analysis.binaries
                  if is_allowed_source(source)}
def is_ambient_duplicate(source):
    if is_allowed_source(source):
        return False
    name = Path(source).name.lower()
    if name == 'ucrtbase.dll':
        return True
    if name in approved_names and name.startswith(('msvcp140', 'vcruntime140')):
        return True
    # These ICU libraries were pulled only by Qt's unused PDF image plugin.
    return name.startswith('icu') and 'poppler' in str(source).lower()

for analysis in (gui, worker):
    analysis.binaries = [entry for entry in analysis.binaries
                         if not is_ambient_duplicate(entry[1])]
    # NumPy's vendored OpenBLAS DLL loads the VC runtime dynamically. PyInstaller
    # nests Qt copies below package folders, which Windows' loader will not find.
    runtime_sources = [*Path(sys.base_prefix).glob('VCRUNTIME140*.dll'),
                       *Path(sys.prefix, 'Lib/site-packages/PySide6').glob('MSVCP140*.dll')]
    bundled_names = {Path(entry[0]).name.lower() for entry in analysis.binaries}
    analysis.binaries.extend((Path(source).name, str(source), 'BINARY')
                             for source in runtime_sources
                             if Path(source).name.lower() not in bundled_names)
unrelated = []
for analysis in (gui, worker):
    for name, source, kind in analysis.binaries:
        if not any(Path(source).resolve().is_relative_to(base) for base in allowed):
            unrelated.append(str(source))
if unrelated:
    raise RuntimeError('Unrelated binaries in build:\n' + '\n'.join(sorted(set(unrelated))))
gui_pyz = PYZ(gui.pure)
worker_pyz = PYZ(worker.pure)
gui_exe = EXE(gui_pyz, gui.scripts, [], exclude_binaries=True, name='Fauxphonic', console=False,
              icon='assets/fauxphonic.ico')
worker_exe = EXE(worker_pyz, worker.scripts, [], exclude_binaries=True, name='Fauxphonic-worker', console=True,
                 icon='assets/fauxphonic.ico')
collection = COLLECT(gui_exe, worker_exe, gui.binaries, worker.binaries, gui.datas, worker.datas,
                     strip=False, upx=False, name='Fauxphonic')
