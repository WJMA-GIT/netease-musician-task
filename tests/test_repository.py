import sqlite3
import threading
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from app import repository
from app import runner
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


class CookieStateTest(unittest.TestCase):
    @patch.object(runner.bus, "status")
    @patch.object(runner.repo, "add_log")
    def test_known_expired_cookie_stops_before_browser(self, add_log, status):
        self.assertFalse(runner._cookie_ready(7, {"cookie_status": "expired"}))
        add_log.assert_called_once()
        status.assert_called_once_with(7, "login_fail", "任务已终止：Cookie 已失效，请先重新登录")

    @patch.object(runner, "_notify_manual_result")
    @patch.object(runner, "_run_blocking")
    @patch.object(runner.bus, "status")
    @patch.object(runner.repo, "add_log")
    @patch.object(runner.repo, "get_account", return_value={"id": 7, "cookie_status": "expired"})
    def test_manual_task_does_not_start_browser_for_expired_cookie(
        self, _account, _add_log, _status, run_blocking, _notify
    ):
        runner.run_selected(7, ["checkin"])
        run_blocking.assert_not_called()

    @patch.object(runner, "_emit_run")
    @patch.object(runner.bus, "status")
    @patch.object(runner, "run_local_listen_batch", return_value={"ok": False, "auth_valid": False})
    def test_continuous_playback_stops_on_expired_cookie(self, run_batch, _status, emit):
        runner._continuous_local_listen_loop(9, threading.Event())
        run_batch.assert_called_once()
        emit.assert_called_once_with(9, "Cookie 已失效，持续播放已终止，请重新登录")


if __name__ == "__main__":
    unittest.main()
