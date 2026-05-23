from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class DeploySmokeTests(unittest.TestCase):
    def test_alembic_upgrade_head_with_sqlite(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temp_dir:
            db_file = Path(temp_dir) / "smoke.db"
            env = dict(os.environ)
            env["DATABASE_URL"] = f"sqlite:///{db_file.as_posix()}"

            upgrade = subprocess.run(
                [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
                cwd=str(root),
                env=env,
                text=True,
                capture_output=True,
            )
            if upgrade.returncode != 0:
                self.fail(f"alembic upgrade head failed: {upgrade.stderr}")

            current = subprocess.run(
                [sys.executable, "-m", "alembic", "-c", "alembic.ini", "current"],
                cwd=str(root),
                env=env,
                text=True,
                capture_output=True,
            )
            if current.returncode != 0:
                self.fail(f"alembic current failed: {current.stderr}")
            self.assertIn("20260523_000002", current.stdout)


if __name__ == "__main__":
    unittest.main()
