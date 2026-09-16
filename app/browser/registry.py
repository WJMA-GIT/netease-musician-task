"""
活跃浏览器进程注册表：按 PID 记录并发运行的浏览器任务（PID + 账号）。

Playwright 同步对象线程绑定，无法跨线程 close()，因此手动停止靠杀进程树实现。
"""

from __future__ import annotations

import os
import signal
import threading
from typing import Optional

from app.event_bus import bus
from app.account_identity import account_label
from app.logging_conf import logger


class _Active:
    __slots__ = ("pid", "account_id", "label", "thread_id")

    def __init__(self, pid: int, account_id: Optional[int], label: str):
        self.pid = pid
        self.account_id = account_id
        self.label = label
        self.thread_id = threading.get_ident()


_lock = threading.Lock()
_active: dict[int, _Active] = {}


def _kill_tree(pid: int) -> None:
    """杀掉进程及其所有子进程（浏览器会 fork 多个渲染进程）。"""
    try:
        import psutil

        parent = psutil.Process(pid)
        procs = parent.children(recursive=True) + [parent]
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass
        psutil.wait_procs(procs, timeout=5)
        return
    except Exception as e:  # noqa: BLE001
        logger.warning(f"psutil 结束进程树失败，改用系统命令兜底：{e}")

    # 兜底：平台原生命令
    try:
        if os.name == "nt":
            os.system(f"taskkill /F /T /PID {pid} >NUL 2>&1")
        else:
            os.kill(pid, signal.SIGKILL)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"兜底结束进程 {pid} 失败：{e}")


def register(pid: int, account_id: Optional[int], label: str = "") -> None:
    with _lock:
        _active[pid] = _Active(pid, account_id, label)
    logger.info(f"登记活跃浏览器 pid={pid} 账号={account_label(account_id)} {label}")


def unregister(pid: int) -> None:
    with _lock:
        _active.pop(pid, None)


def active_account_id() -> Optional[int]:
    """兼容旧调用：返回任一正在运行浏览器的账号 id。"""
    with _lock:
        cur = next(iter(_active.values()), None)
        return cur.account_id if cur is not None else None


def active_info() -> Optional[dict]:
    """兼容旧调用：返回任一活动任务。"""
    with _lock:
        cur = next(iter(_active.values()), None)
        return None if cur is None else {"account_id": cur.account_id, "label": cur.label, "pid": cur.pid}


def active_infos() -> list[dict]:
    with _lock:
        return [
            {"account_id": cur.account_id, "label": cur.label, "pid": cur.pid}
            for cur in _active.values()
        ]


def is_current_task(account_id: Optional[int]) -> bool:
    """当前调用线程是否仍是注册中的浏览器任务；强停或被抢占后立即为 False。"""
    thread_id = threading.get_ident()
    with _lock:
        return any(
            cur.thread_id == thread_id and cur.account_id == account_id
            for cur in _active.values()
        )


def force_stop(account_id: Optional[int] = None, *, label: Optional[str] = None) -> bool:
    """
    强制结束匹配账号的全部活跃浏览器；account_id 为空时停止全部。
    """
    with _lock:
        targets = [
            cur for cur in _active.values()
            if (account_id is None or cur.account_id == account_id)
            and (label is None or cur.label == label)
        ]
        for cur in targets:
            _active.pop(cur.pid, None)
    if not targets:
        return False

    for cur in targets:
        tip = f"账号 {account_label(cur.account_id)}" if cur.account_id is not None else "任务"
        msg = f"手动强制停止（{tip}·{cur.label}）"
        logger.warning(msg)
        bus.log(cur.account_id, msg, level="warn")
        _kill_tree(cur.pid)
        bus.log(cur.account_id, "已强制停止浏览器进程", level="warn")
        bus.status(cur.account_id, "stopped", "已强制停止")
    return True
