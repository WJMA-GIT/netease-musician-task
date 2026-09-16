"""账号 CRUD。删除账号时可选择是否一并删除浏览器 profile 目录。"""

from __future__ import annotations

import shutil

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import repository as repo
from app.logging_conf import logger
from app.account_identity import account_label
from app.local_listen import normalize_item_ids

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


class AccountCreate(BaseModel):
    phone: str
    password: str
    run_time: str | None = None
    interval_days: int | None = None
    enabled: bool = True
    account_role: str = "musician"


class AccountUpdate(BaseModel):
    password: str | None = None
    run_time: str | None = None
    interval_days: int | None = None
    enabled: bool | None = None
    account_role: str | None = None
    local_listen_enabled: bool | None = None
    local_listen_item_id: str | None = None


def _safe(acc: dict) -> dict:
    """对外隐藏密码。"""
    out = dict(acc)
    out.pop("password", None)
    account_id = int(out["id"])
    out["local_listen_helped_today"] = repo.count_local_listen_successes(account_id, period="today")
    out["local_listen_received_today"] = repo.count_local_listen_successes(
        account_id, period="today", as_target=True
    )
    return out


@router.get("")
def list_accounts() -> list[dict]:
    return [_safe(a) for a in repo.list_accounts()]


@router.get("/{account_id}")
def get_account(account_id: int) -> dict:
    acc = repo.get_account(account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    return _safe(acc)


@router.post("")
def create_account(body: AccountCreate) -> dict:
    if repo.get_account_by_phone(body.phone):
        raise HTTPException(400, "该手机号已存在")
    if body.account_role not in {"musician", "player"}:
        raise HTTPException(422, "账号角色必须是 musician 或 player")
    account_id = repo.create_account(
        body.phone,
        body.password,
        run_time=body.run_time,
        interval_days=body.interval_days,
        enabled=body.enabled,
        account_role=body.account_role,
    )
    _reschedule()
    return _safe(repo.get_account(account_id))


@router.patch("/{account_id}")
def update_account(account_id: int, body: AccountUpdate) -> dict:
    if not repo.get_account(account_id):
        raise HTTPException(404, "账号不存在")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "account_role" in fields:
        role = fields["account_role"]
        if role not in {"musician", "player"}:
            raise HTTPException(422, "账号角色必须是 musician 或 player")
        fields["daily_tasks_enabled"] = 1 if role == "musician" else 0
    if "enabled" in fields:
        fields["enabled"] = 1 if fields["enabled"] else 0
    if "local_listen_enabled" in fields:
        fields["local_listen_enabled"] = 1 if fields["local_listen_enabled"] else 0
    if "local_listen_item_id" in fields:
        try:
            fields["local_listen_item_id"] = normalize_item_ids(fields["local_listen_item_id"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    if fields.get("account_role") == "player":
        fields["daily_tasks_enabled"] = 0
        fields["local_listen_enabled"] = 1
    if fields.get("enabled") == 0:
        from app import runner
        from app.browser import registry

        runner.stop_continuous_local_listen(account_id)
        registry.force_stop(account_id)
    repo.update_account(account_id, **fields)
    _reschedule()
    return _safe(repo.get_account(account_id))


@router.delete("/{account_id}")
def delete_account(account_id: int, delete_profile: bool = False) -> dict:
    acc = repo.get_account(account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    profile_dir = acc.get("profile_dir")
    from app import runner
    from app.browser import registry

    runner.stop_continuous_local_listen(account_id)
    registry.force_stop(account_id)
    repo.delete_account(account_id)
    removed = False
    if delete_profile and profile_dir:
        try:
            shutil.rmtree(profile_dir, ignore_errors=True)
            removed = True
            logger.info(f"已删除账号 {account_label(account_id, account=acc)} 的浏览器 profile 目录")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"删除 profile 目录失败：{e}")
    _reschedule()
    return {"ok": True, "profile_removed": removed}


def _reschedule() -> None:
    try:
        from app.scheduler import reschedule_all

        reschedule_all()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"重排调度失败：{e}")
