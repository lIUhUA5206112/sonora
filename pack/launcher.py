"""拾音 Sonora 启动器（PyInstaller 入口）。"""

import os
import sys
from pathlib import Path


def main() -> None:
    base = Path(sys.executable).resolve().parent
    os.chdir(base)
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))
    from app.server import run

    run()


if __name__ == "__main__":
    main()
