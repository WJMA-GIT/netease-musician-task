"""不启动浏览器，验证多账号注册与持续播放控制。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import runner
from app.browser import registry


def main() -> None:
    killed: list[int] = []
    original_kill = registry._kill_tree
    try:
        registry._kill_tree = killed.append
        registry.register(1001, 1, "listen")
        registry.register(1002, 2, "listen")
        assert {item["account_id"] for item in registry.active_infos()} == {1, 2}
        assert registry.force_stop(1)
        assert killed == [1001]
        assert [item["account_id"] for item in registry.active_infos()] == [2]
        registry.force_stop()
    finally:
        registry._kill_tree = original_kill

    original_accounts = runner.repo.list_accounts
    original_jobs = runner._build_local_listen_jobs
    original_loop = runner._continuous_local_listen_loop
    try:
        runner.repo.list_accounts = lambda: [
            {"id": 1, "enabled": 1},
            {"id": 2, "enabled": 1},
            {"id": 3, "enabled": 0},
        ]
        runner._build_local_listen_jobs = lambda account_id, limit: [{"item_id": "1"}]
        runner._continuous_local_listen_loop = lambda account_id, event: event.wait(5)
        assert runner.start_continuous_local_listen_all() == [1, 2]
        assert runner.continuous_local_listen_account_ids() == [1, 2]
        assert runner.stop_continuous_local_listen() == [1, 2]
        for thread, _ in runner._continuous_listen.values():
            thread.join(1)
    finally:
        runner.repo.list_accounts = original_accounts
        runner._build_local_listen_jobs = original_jobs
        runner._continuous_local_listen_loop = original_loop
        runner._continuous_listen.clear()

    original_batch = runner.run_local_listen_batch
    original_emit = runner._emit_run
    original_status = runner.bus.status
    calls: list[int] = []

    class StopEvent:
        stopped = False

        def is_set(self) -> bool:
            return self.stopped

        def wait(self, _seconds: float) -> None:
            pass

    stop_event = StopEvent()

    def fail_then_stop(account_id: int, *_args, **_kwargs) -> dict:
        calls.append(account_id)
        if len(calls) == 3:
            stop_event.stopped = True
        return {"ok": False, "message": "test error"}

    try:
        runner.run_local_listen_batch = fail_then_stop
        runner._emit_run = lambda *_args, **_kwargs: None
        runner.bus.status = lambda *_args, **_kwargs: None
        runner._continuous_local_listen_loop(1, stop_event)
        assert calls == [1, 1, 1]
    finally:
        runner.run_local_listen_batch = original_batch
        runner._emit_run = original_emit
        runner.bus.status = original_status

    print("parallel listen checks passed")


if __name__ == "__main__":
    main()
