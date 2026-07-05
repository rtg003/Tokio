from __future__ import annotations

import gzip
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "deploy" / "backup_sqlite.sh"


@pytest.mark.skipif(shutil.which("sqlite3") is None, reason="sqlite3 CLI ausente")
def test_backup_script_creates_verified_local_and_offsite_copy(tmp_path: Path) -> None:
    db_path = tmp_path / "tokio.db"
    backup_dir = tmp_path / "backups"
    remote_dir = tmp_path / "offsite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE strategies (id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO strategies (id) VALUES ('ct_48295497')")
        conn.commit()

    env = {
        "APP_DIR": str(tmp_path),
        "DB_PATH": str(db_path),
        "BACKUP_DIR": str(backup_dir),
        "BACKUP_REMOTE": f"file://{remote_dir}",
        "LOCAL_RETENTION_DAYS": "7",
        "REMOTE_RETENTION_DAYS": "30",
    }
    subprocess.run(["bash", str(SCRIPT)], check=True, env=env, cwd=REPO_ROOT)

    local = sorted(backup_dir.glob("tokio-*.db.gz"))
    remote = sorted(remote_dir.glob("tokio-*.db.gz"))
    assert len(local) == 1
    assert [p.name for p in remote] == [local[0].name]

    restored = tmp_path / "restored.db"
    with gzip.open(local[0], "rb") as src:
        restored.write_bytes(src.read())
    with sqlite3.connect(restored) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT id FROM strategies").fetchone()[0] == "ct_48295497"

    subprocess.run(["bash", str(SCRIPT), "--verify", str(local[0])], check=True, env=env, cwd=REPO_ROOT)
