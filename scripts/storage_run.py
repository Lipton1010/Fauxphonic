"""Own and finish build payloads without claiming runtime release acceptance."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from local_audio_studio import storage

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("operation", choices=["begin-build", "complete-build"])
parser.add_argument("path")
parser.add_argument("results", nargs="*")
args = parser.parse_args()
if args.operation == "begin-build":
    # The launching PowerShell owns the build, rather than this short helper.
    import json
    import psutil
    folder = storage.begin(args.path, "build")
    owner = json.loads((folder / storage.MARKER).read_text())
    parent = psutil.Process().parent()
    owner.update(pid=parent.pid, process_created=parent.create_time())
    storage._write(folder / storage.MARKER, owner)
    print(folder)
else:
    storage.complete(args.path, args.results)
