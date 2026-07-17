import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / "data" / "ead.db"

if not DB_PATH.exists():
    subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "data" / "generate_dataset.py")],
        check=True,
    )
