"""
浏览器 worker：每个浏览器任务跑在独立线程。

不同账号的 profile 可并发运行；同一 profile 用锁串行，避免 Playwright
同时打开同一个持久化目录导致冲突。

用法：
    submit(fn, *args, **kwargs) -> Future
    fn 在独立线程内执行；Playwright 生命周期由 run_with_context 提供。
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import Future
from contextlib import contextmanager
from typing import Any, Callable

from app.config import BROWSER_TIMEOUT_MS, HEADLESS, USER_AGENT
from app.browser.selectors import STEALTH_SCRIPT
from app.logging_conf import logger


class BrowserWorker:
    def __init__(self) -> None:
        self._started = False

    def start(self) -> None:
        # 无常驻线程，保留接口兼容 main.py 的启动调用
        self._started = True
        logger.info("浏览器 worker 已就绪（多账号并发）")

    def submit(self, fn: Callable[..., Any], *args, **kwargs) -> Future:
        fut: Future = Future()

        def _run() -> None:
            try:
                fut.set_result(fn(*args, **kwargs))
            except Exception as e:  # noqa: BLE001
                logger.exception(f"浏览器任务执行异常：{e}")
                fut.set_exception(e)

        threading.Thread(target=_run, name="browser-task", daemon=True).start()
        return fut


# 全局单例
worker = BrowserWorker()

_profile_locks_guard = threading.Lock()
_profile_locks: dict[str, threading.Lock] = {}


def _profile_lock(profile_dir: str) -> threading.Lock:
    key = os.path.abspath(profile_dir)
    with _profile_locks_guard:
        return _profile_locks.setdefault(key, threading.Lock())


@contextmanager
def run_with_context(
    profile_dir: str,
    *,
    headless: bool | None = None,
    account_id: int | None = None,
    label: str = "",
    cancel_event: threading.Event | None = None,
):
    """
    在 worker 线程内打开一个持久化 Playwright context，yield (context, page)。
    退出时自动关闭。必须在 worker 线程内调用（Playwright 同步 API 线程亲和）。
    打开后登记浏览器进程，供手动停止逻辑跨线程强制结束。
    """
    from playwright.sync_api import sync_playwright
    from app.browser import registry

    os.makedirs(profile_dir, exist_ok=True)

    # headless 优先取运行期 settings（网页可改），未指定时回退到启动配置
    if headless is None:
        try:
            from app.repository import get_setting_bool

            use_headless = get_setting_bool("headless", HEADLESS)
        except Exception:
            use_headless = HEADLESS
    else:
        use_headless = headless

    profile_lock = _profile_lock(profile_dir)
    while not profile_lock.acquire(timeout=0.2):
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("任务已停止")
    try:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("任务已停止")
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=use_headless,
                viewport={"width": 1280, "height": 800},
                user_agent=USER_AGENT,
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            )
            context.add_init_script(STEALTH_SCRIPT)
            page = context.new_page()
            page.set_default_timeout(BROWSER_TIMEOUT_MS)

            # 登记浏览器进程（driver 进程树含 chromium），供手动停止
            pid = None
            try:
                pid = context._impl_obj._connection._transport._proc.pid
                registry.register(pid, account_id, label)
            except Exception:
                pass

            try:
                yield context, page
            finally:
                if pid is not None:
                    registry.unregister(pid)
                try:
                    context.close()
                except Exception:
                    pass
    finally:
        profile_lock.release()
