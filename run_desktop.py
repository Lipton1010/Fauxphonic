from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent / "src"))
from local_audio_studio.gui import main

if __name__ == "__main__":
    main()
