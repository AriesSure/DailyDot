"""Run only the fixed disposable SQLite migration verification flow."""

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.migration_validation import verify_migrations


def main():
    if len(sys.argv) != 1:
        print("migration_validation_arguments_forbidden")
        return 2
    try:
        verify_migrations()
    except Exception:
        print("migration_validation_failed")
        return 1
    print("migration_validation_ok: empty -> 9a097b8e080b; repeated upgrade verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
