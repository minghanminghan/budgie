import sqlite3
from datetime import date
from pathlib import Path


def backup_db(db_path: Path, backup_dir: Path, today: date, keep: int = 7) -> Path:
    """Online SQLite backup to newsletter-YYYY-MM-DD.sqlite; keep the newest `keep` files."""
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"newsletter-{today.isoformat()}.sqlite"
    tmp = dest.with_suffix(".sqlite.tmp")
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    tmp.replace(dest)
    for old in sorted(backup_dir.glob("newsletter-*.sqlite"))[:-keep]:
        old.unlink()
    return dest
