"""手动触发任务 + 查看日志。"""

from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import repository as repo
from app.logging_conf import logger

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

_VALID_TASKS = {"checkin", "publish", "vip", "local_listen"}
_TASK_ALIASES = {"publishing": "publish"}  # 兼容修复前的网页缓存


class RunSelection(BaseModel):
    tasks: list[str]


@router.post("/local-listen/start-all")
def start_all_local_listen() -> dict:
    from app import runner

    started = runner.start_continuous_local_listen_all()
    active = runner.continuous_local_listen_account_ids()
    if not active:
        raise HTTPException(400, "没有可启动的已启用账号")
    return {"ok": True, "started": started, "active": active,
            "message": f"持续播放已启动，共 {len(active)} 个账号并发"}


@router.post("/local-listen/stop-all")
def stop_all_local_listen() -> dict:
    from app import runner
    from app.browser import registry

    signaled = runner.stop_continuous_local_listen()
    stopped = registry.force_stop(label="本地互助听歌")
    return {"ok": bool(signaled or stopped), "message": f"已停止 {len(signaled)} 个持续播放任务"}


@router.post("/{account_id}/run")
def run_selected(account_id: int, body: RunSelection) -> dict:
    account = repo.get_account(account_id)
    if not account:
        raise HTTPException(404, "账号不存在")
    normalized = [_TASK_ALIASES.get(t, t) for t in body.tasks]
    # 去重并保持用户勾选顺序。
    tasks = list(dict.fromkeys(t for t in normalized if t in _VALID_TASKS))
    if not tasks:
        raise HTTPException(400, "请至少选择一项任务")
    if account.get("account_role", "musician") == "player" and any(
        task != "local_listen" for task in tasks
    ):
        raise HTTPException(400, "普通播放账号只能执行本地互助听歌")

    def _bg() -> None:
        try:
            from app import runner

            runner.run_selected(account_id, tasks)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"后台任务异常：{e}")

    threading.Thread(target=_bg, name=f"run-{account_id}", daemon=True).start()
    return {"ok": True, "message": f"已在后台执行：{', '.join(tasks)}"}


@router.get("/logs")
def logs(account_id: int | None = None, limit: int = 100) -> list[dict]:
    return repo.list_logs(account_id, limit)


@router.delete("/logs")
def clear_logs() -> dict:
    from app.event_bus import bus

    deleted = repo.clear_logs()
    bus.clear_buffers()
    return {"ok": True, "deleted": deleted, "message": f"已清除 {deleted} 条历史日志"}


@router.get("/active")
def active() -> dict:
    """返回全部活动账号，兼容保留 active 单值。"""
    from app import runner
    from app.browser import registry

    continuous = runner.continuous_local_listen_account_ids()
    infos = registry.active_infos()
    known = {info["account_id"] for info in infos}
    infos.extend(
        {"account_id": account_id, "label": "持续播放", "pid": None}
        for account_id in continuous
        if account_id not in known
    )
    return {"active": infos[0] if infos else None, "actives": infos, "continuous": continuous}


@router.get("/{account_id}/live")
def live_logs(account_id: int) -> dict:
    """拉取该账号的累积实时日志 + 最新二维码（供「查看」弹窗回看，不清空）。"""
    from app.event_bus import bus

    return {"logs": bus.get_buffer(account_id), "qr": bus.get_qr(account_id)}


@router.post("/{account_id}/stop")
def stop(account_id: int) -> dict:
    """强制停止该账号正在运行的浏览器任务。"""
    from app import runner
    from app.browser import registry

    signaled = runner.stop_continuous_local_listen(account_id)
    stopped = registry.force_stop(account_id) or bool(signaled)
    return {"ok": stopped, "message": "已强制停止" if stopped else "该账号当前没有正在运行的任务"}
