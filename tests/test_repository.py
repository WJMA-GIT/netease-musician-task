import sqlite3
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from app import repository
from app.api import tasks


class ClearLogsTest(unittest.TestCase):
    def test_clear_logs_only_removes_task_logs(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            "CREATE TABLE task_logs (id INTEGER PRIMARY KEY, message TEXT);"
            "CREATE TABLE local_listen_runs (id INTEGER PRIMARY KEY);"
            "INSERT INTO task_logs(message) VALUES ('a'), ('b');"
            "INSERT INTO local_listen_runs DEFAULT VALUES;"
        )

        @contextmanager
        def fake_db():
            yield conn
            conn.commit()

        with patch.object(repository, "db", fake_db):
            self.assertEqual(repository.clear_logs(), 2)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM task_logs").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM local_listen_runs").fetchone()[0], 1)


class ActiveStateTest(unittest.TestCase):
    @patch("app.browser.registry.active_infos", return_value=[])
    @patch("app.runner.continuous_local_listen_account_ids", return_value=[2, 5])
    def test_active_exposes_continuous_accounts(self, _continuous, _active):
        state = tasks.active()
        self.assertEqual(state["continuous"], [2, 5])
        self.assertEqual([item["account_id"] for item in state["actives"]], [2, 5])


if __name__ == "__main__":
    unittest.main()
