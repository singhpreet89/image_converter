# Split out of convert.py: the resume-manifest (schema, read/write, cleanup) is
# self-contained enough to reason about and test on its own, and keeping it here
# keeps convert.py focused on the actual conversion pipeline.

import sqlite3
from pathlib import Path


class Manifest:
    """Tracks which (source, target format, output dir) combinations have
    already been converted in this session, so a killed/crashed run can be
    resumed instead of reconverting everything.

    This is intentionally NOT a permanent record: callers are expected to
    delete the backing file once a run completes normally (see
    `delete_file`). In read-only mode (used for --dry-run) the manifest is
    opened without creating or modifying anything on disk.
    """

    def __init__(self, db_path: Path, read_only: bool = False):
        self.db_path = db_path
        self.read_only = read_only

        if read_only:
            self.conn = (
                sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) if db_path.exists() else None
            )
            return

        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS converted (
                src_path      TEXT NOT NULL,
                target_format TEXT NOT NULL,
                output_dir    TEXT NOT NULL,
                size          INTEGER NOT NULL,
                mtime         REAL NOT NULL,
                dst_path      TEXT NOT NULL,
                converted_at  REAL NOT NULL,
                PRIMARY KEY (src_path, target_format, output_dir)
            )
            """
        )
        self.conn.commit()

    def is_converted(self, src_path: str, target_format: str, output_dir: str, size: int, mtime: float) -> bool:
        if self.conn is None:
            return False
        try:
            row = self.conn.execute(
                "SELECT size, mtime FROM converted WHERE src_path = ? AND target_format = ? AND output_dir = ?",
                (src_path, target_format, output_dir),
            ).fetchone()
        except sqlite3.OperationalError:
            return False
        return row is not None and row[0] == size and row[1] == mtime

    def record(
        self,
        src_path: str,
        target_format: str,
        output_dir: str,
        size: int,
        mtime: float,
        dst_path: str,
        converted_at: float,
    ) -> None:
        # Commits immediately rather than batching: the whole point of this
        # table is crash resilience, and an image conversion takes far longer
        # than a single-row SQLite commit, so there's no meaningful throughput
        # cost to making every recorded success durable right away.
        assert not self.read_only, "attempted to write to a read-only manifest"
        assert self.conn is not None
        self.conn.execute(
            """
            INSERT OR REPLACE INTO converted
                (src_path, target_format, output_dir, size, mtime, dst_path, converted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (src_path, target_format, output_dir, size, mtime, dst_path, converted_at),
        )
        self.conn.commit()

    def close(self) -> None:
        if self.conn is None:
            return
        if not self.read_only:
            self.conn.commit()
        self.conn.close()
        self.conn = None

    def delete_file(self) -> None:
        self.close()
        if self.read_only:
            return
        try:
            self.db_path.unlink()
        except FileNotFoundError:
            pass
