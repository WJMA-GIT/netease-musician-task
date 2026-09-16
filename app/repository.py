"""accounts / task_logs / settings 的数据访问层。"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional

from app.config import PROFILE_BASEDIR
from app.db import db


# ---------- settings ----------
def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def get_setting_int(key: str, default: int) -> int:
    """读取整型配置（settings 表实时值），解析失败回退 default。"""
    val = get_setting(key, None)
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def get_setting_bool(key: str, default: bool) -> bool:
    """读取布尔型配置（settings 表实时值）。"""
    val = get_setting(key, None)
    if val is None:
        return default
    return str(val) not in ("0", "false", "False", "")


def get_all_settings() -> dict[str, str]:
    with db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


def set_setting(key: str, value: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


# ---------- accounts ----------
def _safe_phone(phone: str) -> str:
    digits = "".join(c for c in str(phone) if c.isdigit())
    return digits or str(phone)


def list_accounts() -> list[dict[str, Any]]:
    with db() as conn:
        rows = conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def get_account(account_id: int) -> Optional[dict[str, Any]]:
    with db() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        return dict(row) if row else None


def get_account_by_phone(phone: str) -> Optional[dict[str, Any]]:
    with db() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE phone=?", (phone,)).fetchone()
        return dict(row) if row else None


def create_account(
    phone: str,
    password: str,
    *,
    run_time: Optional[str] = None,
    interval_days: Optional[int] = None,
    enabled: bool = True,
    account_role: str = "musician",
) -> int:
    profile_dir = os.path.join(PROFILE_BASEDIR, _safe_phone(phone))
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO accounts(phone, password, profile_dir, enabled, run_time, interval_days, "
            "account_role, daily_tasks_enabled, local_listen_enabled) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (phone, password, profile_dir, 1 if enabled else 0, run_time, interval_days,
             account_role, 1 if account_role == "musician" else 0,
             1 if account_role == "player" else 0),
        )
        return int(cur.lastrowid)


def update_account(account_id: int, **fields) -> None:
    if not fields:
        return
    allowed = {
        "phone", "password", "uid", "nickname", "profile_dir", "enabled",
        "daily_tasks_enabled",
        "account_role",
        "run_time", "interval_days", "cookie_status", "last_login_at",
        "further_vip_get_time", "last_send_date", "monthly_sends", "month_tag",
        "local_listen_enabled", "local_listen_item_id",
    }
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return
    sets.append("updated_at=?")
    vals.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    vals.append(account_id)
    with db() as conn:
        conn.execute(f"UPDATE accounts SET {', '.join(sets)} WHERE id=?", vals)


def delete_account(account_id: int) -> None:
    with db() as conn:
        conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        conn.execute("DELETE FROM task_logs WHERE account_id=?", (account_id,))


# ---------- task_logs ----------
def add_log(account_id: Optional[int], task_type: str, status: str, message: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO task_logs(account_id, task_type, status, message) VALUES (?, ?, ?, ?)",
            (account_id, task_type, status, message[:2000]),
        )


def list_logs(account_id: Optional[int] = None, limit: int = 100) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 2000))
    with db() as conn:
        if account_id is not None:
            rows = conn.execute(
                "SELECT * FROM task_logs WHERE account_id=? ORDER BY id DESC LIMIT ?",
                (account_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM task_logs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def clear_logs() -> int:
    """清空全部账号的历史日志，返回删除条数。"""
    with db() as conn:
        cur = conn.execute("DELETE FROM task_logs")
        return int(cur.rowcount or 0)


def cleanup_logs(retention_days: int) -> int:
    """删除超过保留天数的任务和实时执行日志，返回删除条数。"""
    days = max(1, int(retention_days))
    with db() as conn:
        cur = conn.execute(
            "DELETE FROM task_logs WHERE created_at < datetime('now','localtime', ?)",
            (f"-{days} days",),
        )
        return int(cur.rowcount or 0)


# ---------- 本地账号互助听歌 ----------
def count_local_listen_successes(account_id: int, *, period: str, as_target: bool = False) -> int:
    column = "target_account_id" if as_target else "listener_account_id"
    if period == "today":
        where = "date(created_at)=date('now','localtime')"
    elif period == "month":
        where = "strftime('%Y-%m',created_at)=strftime('%Y-%m','now','localtime')"
    else:
        raise ValueError("period must be today or month")
    with db() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM local_listen_runs WHERE {column}=? AND status='success' AND {where}",
            (account_id,),
        ).fetchone()
        return int(row["n"] or 0)


def list_local_listen_targets(listener_account_id: int) -> list[dict[str, Any]]:
    """列出其他本地参与账号；今日被听较少者优先。"""
    with db() as conn:
        rows = conn.execute(
            """
            SELECT a.*,
                   COUNT(r.id) AS received_today
            FROM accounts a
            LEFT JOIN local_listen_runs r
              ON r.target_account_id=a.id
             AND r.status='success'
             AND date(r.created_at)=date('now','localtime')
            WHERE a.id<>?
              AND a.enabled=1
              AND a.account_role='musician'
              AND a.local_listen_enabled=1
              AND COALESCE(TRIM(a.local_listen_item_id),'')<>''
            GROUP BY a.id
            ORDER BY received_today ASC, a.id ASC
            """,
            (listener_account_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def next_local_listen_target(listener_account_id: int) -> Optional[dict[str, Any]]:
    targets = list_local_listen_targets(listener_account_id)
    return targets[0] if targets else None


def local_listen_item_success_counts(target_account_id: int) -> dict[str, int]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT target_item_id, COUNT(*) AS n
            FROM local_listen_runs
            WHERE target_account_id=? AND status='success'
              AND date(created_at)=date('now','localtime')
            GROUP BY target_item_id
            """,
            (target_account_id,),
        ).fetchall()
        return {str(row["target_item_id"]): int(row["n"] or 0) for row in rows}


def add_local_listen_run(
    listener_account_id: int,
    target_account_id: int,
    target_item_id: str,
    status: str,
    message: str = "",
) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO local_listen_runs(listener_account_id,target_account_id,target_item_id,status,message) "
            "VALUES (?,?,?,?,?)",
            (listener_account_id, target_account_id, target_item_id, status, message[:2000]),
        )
