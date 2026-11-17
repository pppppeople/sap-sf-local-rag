"""Read-only entrypoint imports, using the invoking interpreter (Python 3.9+)."""

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULES = (
    "setup", "ask", "sap_asset", "kb_hook", "build_index", "knowledge", "lib.sf_tokenize",
)
IMPORT_CODE = r'''
import importlib
import os
import sys
from pathlib import Path
from unittest.mock import patch

root = Path.cwd()
fake_home = root / ".import-smoke-home"
internal_docs = root / "docs"
original_exists = Path.exists
sys.dont_write_bytecode = True

def read_only(event, args):
    if event == "open":
        path, mode, flags = args
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            raise RuntimeError("import attempted a file write")
        if isinstance(path, (str, bytes)) and fake_home in Path(os.fsdecode(path)).parents:
            raise PermissionError("smoke test does not read user configuration")
    if event in {"os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.link", "os.symlink"}:
        raise RuntimeError("import attempted a filesystem mutation")

def exists(path):
    return False if path == internal_docs or path == fake_home or fake_home in path.parents else original_exists(path)

sys.addaudithook(read_only)
with patch.object(Path, "home", return_value=fake_home), \
     patch.object(Path, "exists", exists), \
     patch.dict(os.environ, {"SAP_SF_NOTES_DIR": str(fake_home / "notes")}):
    try:
        importlib.import_module(sys.argv[1])
    except ModuleNotFoundError as error:
        print("MISSING_DEPENDENCY " + str(error.name))
        sys.exit(2)
    except TypeError as error:
        print("TYPE_ERROR " + str(error))
        sys.exit(1)
    except Exception as error:
        print(type(error).__name__ + " " + str(error))
        sys.exit(1)
print("PASS")
'''


def main() -> int:
    print("Python " + sys.version.split()[0], flush=True)
    failed = False
    for module in MODULES:
        result = subprocess.run(
            [sys.executable, "-B", "-c", IMPORT_CODE, module],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        detail = result.stdout.strip() or result.stderr.strip()
        print(module + ": " + detail, flush=True)
        failed = failed or result.returncode != 0
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
