// 网易音乐人任务管理 前端逻辑
const $ = (s) => document.querySelector(s);
const api = async (url, opts = {}) => {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) {
      location.href = res.status === 403 ? "/change-password" : "/login";
    }
    const t = await res.text();
    throw new Error(t || res.statusText);
  }
  return res.status === 204 ? null : res.json();
};
const escapeHtml = (s) =>
  String(s).replace(
    /[&<>]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c],
  );

// ---------- 运行日志弹窗 ----------
// 当前「运行日志」弹窗正在查看的账号；WS 日志按此过滤显示
let viewingAccountId = null;
// 当前正在运行浏览器的账号（支持多账号并发）
let runningAccountIds = new Set();
// 「一键播放」启动的持续播放账号
let continuousListeningIds = new Set();
let activeRefreshVersion = 0;

function openRunModal(title, accountId) {
  $("#run-title").textContent = title;
  $("#run-modal-account").value = accountId != null ? accountId : "";
  viewingAccountId = accountId != null ? Number(accountId) : null;
  $("#log-box").innerHTML = "";
  hideQR();
  $("#modal-run").classList.remove("hidden");
}
async function openViewModal(accountId, phone) {
  // 查看：从 SQLite 拉取历史日志，并继续接收实时更新。
  openRunModal(`账号 ${phone || accountId} 历史日志`, accountId);
  await loadHistoryLogs(accountId);
  try {
    const data = await api(`/api/tasks/${accountId}/live`);
    if (data.qr && data.qr.qr_url) showQR(data.qr.qr_url, data.qr.tip);
  } catch (err) {
    appendLog("", "拉取当前状态失败：" + err.message, "error");
  }
}
async function loadHistoryLogs(accountId) {
  try {
    const rows = await api(`/api/tasks/logs?account_id=${accountId}&limit=1000`);
    $("#log-box").innerHTML = "";
    for (const row of [...rows].reverse()) {
      const prefix = row.task_type === "runtime"
        ? ""
        : `【${row.task_type || "task"}/${row.status || "info"}】`;
      appendLog(row.created_at, `${prefix}${row.message || ""}`, row.status);
    }
    if (!rows.length) appendLog("", "暂无历史日志", "info");
  } catch (err) {
    appendLog("", "拉取历史日志失败：" + err.message, "error");
  }
}
function appendLog(ts, line, level) {
  const box = $("#log-box");
  if (!box) return;
  const div = document.createElement("div");
  div.className = "log-line " + (level || "info");
  const value = String(line || "");
  const screenshot = value.match(/\/api\/debug\/screenshots\/\d+\/[A-Za-z0-9_.-]+\.png/);
  const text = screenshot ? value.replace(screenshot[0], "") : value;
  div.innerHTML = `<span class="t">${ts || ""}</span>${escapeHtml(text)}`;
  if (screenshot) {
    const link = document.createElement("a");
    link.href = screenshot[0];
    link.target = "_blank";
    link.rel = "noopener";
    link.className = "debug-shot-link";
    link.textContent = "查看截图";
    div.appendChild(link);
  }
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}
function showQR(url, tip) {
  $("#qr-tip").textContent = tip || "请扫码";
  $("#qr-img").src = url;
  $("#qr-box").classList.remove("hidden");
}
function hideQR() {
  $("#qr-box").classList.add("hidden");
}

// ---------- WebSocket ----------
let ws;
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => setConn(true);
  ws.onclose = () => {
    setConn(false);
    setTimeout(connectWS, 2000);
  };
  ws.onmessage = (e) => handleEvent(JSON.parse(e.data));
}
function setConn(on) {
  $("#ws-dot").className = "dot " + (on ? "on" : "off");
  $("#ws-text").textContent = on ? "已连接" : "重连中...";
}
function handleEvent(msg) {
  const modalOpen = !$("#modal-run").classList.contains("hidden");
  const forThisView =
    viewingAccountId != null && Number(msg.account_id) === viewingAccountId;

  if (msg.type === "log") {
    if (modalOpen && forThisView) appendLog(msg.ts, msg.line, msg.level);
  } else if (msg.type === "qrcode") {
    if (modalOpen && forThisView) showQR(msg.qr_url, msg.tip);
  } else if (msg.type === "status") {
    if (modalOpen && forThisView) {
      appendLog(msg.ts, `【状态】${msg.status} ${msg.detail || ""}`, "info");
      if (msg.status === "login_ok") hideQR();
    }
    const startStates = ["logging_in", "running", "secondary"];
    const endStates = ["done", "stopped", "login_ok", "login_fail"];
    const accountId = Number(msg.account_id);
    if (startStates.includes(msg.status)) {
      runningAccountIds.add(accountId);
      if (msg.detail === "持续播放已启动") continuousListeningIds.add(accountId);
      syncListenButtons();
      loadAccounts();
      // running 事件可能早于浏览器进程登记，稍后以后端状态校准。
      setTimeout(refreshActiveAndList, 300);
    } else if (endStates.includes(msg.status)) {
      // login_fail 等也可能是持续播放的中间状态，不能直接移除。
      setTimeout(refreshActiveAndList, 300);
    }
  }
}

// ---------- 账号列表 ----------
let globalSendTime = "09:30";

async function loadAccounts() {
  const accounts = await api("/api/accounts");
  const body = $("#acc-body");
  body.innerHTML = "";
  $("#empty-hint").classList.toggle("hidden", accounts.length > 0);
  for (const a of accounts) {
    const status = a.cookie_status || "unknown";
    const statusText =
      { ok: "有效", expired: "过期", unknown: "未知" }[status] || status;
    const runTime = a.run_time
      ? escapeHtml(a.run_time)
      : `${escapeHtml(globalSendTime)} <span class="tag-global">全局</span>`;
    const running = runningAccountIds.has(a.id);
    const actionBtn = running
      ? `<button class="btn btn-sm btn-view" data-act="view" data-id="${a.id}" data-phone="${escapeHtml(a.phone)}">查看</button>`
      : `<button class="btn btn-sm" data-act="run" data-id="${a.id}" data-phone="${escapeHtml(a.phone)}" data-role="${a.account_role || "musician"}">执行</button>`;
    const historyBtn = running
      ? ""
      : `<button class="btn btn-sm" data-act="history" data-id="${a.id}" data-phone="${escapeHtml(a.phone)}">日志</button>`;
    const enabled = !!a.enabled;
    const activityBadge = continuousListeningIds.has(a.id)
      ? `<span class="badge running">正在播放</span> `
      : running ? `<span class="badge running">运行中</span> ` : "";
    const enabledBadge = enabled
      ? `${activityBadge}<span class="badge ok">启用</span> <span class="badge unknown">${a.account_role === "player" ? "普通播放" : "音乐人"}</span>`
      : `<span class="badge expired">暂停</span>`;
    const toggleBtn = `<button class="btn btn-sm" data-act="toggle" data-id="${a.id}" data-enabled="${enabled ? 1 : 0}">${enabled ? "暂停" : "启用"}</button>`;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td data-label="手机号">${escapeHtml(a.phone)}</td>
      <td data-label="昵称">${escapeHtml(a.nickname || "-")}</td>
      <td data-label="Cookie 状态"><span class="badge ${status}">${statusText}</span></td>
      <td data-label="状态">${enabledBadge}</td>
      <td data-label="运行时间">${runTime}</td>
      <td data-label="本月发布">${a.monthly_sends || 0}</td>
      <td data-label="本地互助（今日）">${a.local_listen_enabled ? `帮助 ${a.local_listen_helped_today || 0} / 被帮助 ${a.local_listen_received_today || 0}` : "未加入"}</td>
      <td data-label="操作" class="cell-actions">
        <button class="btn btn-sm btn-primary" data-act="login" data-id="${a.id}" data-phone="${escapeHtml(a.phone)}" ${running ? "disabled" : ""}>登录</button>
        ${actionBtn}
        ${historyBtn}
        ${toggleBtn}
        <button class="btn btn-sm" data-act="edit" data-id="${a.id}">编辑</button>
        <button class="btn btn-sm btn-danger" data-act="delete" data-id="${a.id}" data-phone="${escapeHtml(a.phone)}">删除</button>
      </td>`;
    body.appendChild(tr);
  }
}

async function refreshGlobalSendTime() {
  try {
    const s = await api("/api/settings");
    if (s.default_send_time) globalSendTime = s.default_send_time;
  } catch (e) {
    /* ignore */
  }
}

async function refreshActiveAndList() {
  const version = ++activeRefreshVersion;
  try {
    const data = await api("/api/tasks/active");
    if (version !== activeRefreshVersion) return;
    runningAccountIds = new Set(
      (data.actives || (data.active ? [data.active] : [])).map((item) => Number(item.account_id)),
    );
    continuousListeningIds = new Set((data.continuous || []).map(Number));
  } catch (e) {
    /* ignore */
  }
  if (version !== activeRefreshVersion) return;
  syncListenButtons();
  await loadAccounts();
}

function syncListenButtons() {
  const playing = continuousListeningIds.size > 0;
  const start = $("#btn-listen-all");
  start.textContent = playing ? `正在播放（${continuousListeningIds.size}）` : "一键播放";
  start.disabled = playing;
  $("#btn-stop-listen-all").disabled = !playing;
}

$("#acc-body").addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const id = btn.dataset.id;
  const act = btn.dataset.act;
  try {
    if (act === "login") {
      openLoginConfirm(id, btn.dataset.phone);
    } else if (act === "run") {
      openRunSelect(id, btn.dataset.phone, btn.dataset.role);
    } else if (act === "toggle") {
      const next = btn.dataset.enabled === "1" ? false : true;
      await api(`/api/accounts/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: next }),
      });
      await loadAccounts();
    } else if (act === "view") {
      openViewModal(id, btn.dataset.phone);
    } else if (act === "history") {
      openViewModal(id, btn.dataset.phone);
    } else if (act === "edit") {
      openEdit(id);
    } else if (act === "delete") {
      onDelete(id, btn.dataset.phone);
    }
  } catch (err) {
    appendLog("", "操作失败：" + err.message, "error");
    alert("操作失败：" + err.message);
  }
});

// ---------- 登录确认 ----------
function openLoginConfirm(id, phone) {
  $("#login-account-id").value = id;
  $("#login-phone").textContent = phone || `#${id}`;
  $("#modal-login").classList.remove("hidden");
}
$("#btn-confirm-login").addEventListener("click", async () => {
  const id = $("#login-account-id").value;
  const phone = $("#login-phone").textContent || id;
  try {
    $("#modal-login").classList.add("hidden");
    openRunModal(`账号 ${phone} 登录中`, id);
    runningAccountIds.add(Number(id));
    loadAccounts();
    await api(`/api/login/${id}`, { method: "POST" });
  } catch (err) {
    runningAccountIds.delete(Number(id));
    loadAccounts();
    appendLog("", "启动登录失败：" + err.message, "error");
  }
});

// ---------- 执行任务多选 ----------
function openRunSelect(id, phone, role = "musician") {
  $("#run-account-id").value = id;
  $("#run-account-id").dataset.phone = phone || `#${id}`;
  document.querySelectorAll(".run-task").forEach((c) => {
    c.disabled = role === "player" && c.value !== "local_listen";
    c.checked = role === "player" ? c.value === "local_listen" : c.value === "checkin";
  });
  $("#modal-run-select").classList.remove("hidden");
}
$("#btn-confirm-run").addEventListener("click", async () => {
  const id = $("#run-account-id").value;
  const phone = $("#run-account-id").dataset.phone || id;
  const tasks = [...document.querySelectorAll(".run-task:checked")].map(
    (c) => c.value,
  );
  if (tasks.length === 0) {
    alert("请至少选择一项任务");
    return;
  }
  try {
    $("#modal-run-select").classList.add("hidden");
    openRunModal(`账号 ${phone} 执行任务`, id);
    runningAccountIds.add(Number(id));
    loadAccounts();
    await api(`/api/tasks/${id}/run`, {
      method: "POST",
      body: JSON.stringify({ tasks }),
    });
  } catch (err) {
    runningAccountIds.delete(Number(id));
    loadAccounts();
    appendLog("", "启动失败：" + err.message, "error");
  }
});

async function onDelete(id, phone) {
  $("#del-id").value = id;
  $("#del-phone").textContent = phone;
  $("#del-profile").checked = false;
  $("#modal-delete").classList.remove("hidden");
}
$("#btn-confirm-delete").addEventListener("click", async () => {
  const id = $("#del-id").value;
  const delProfile = $("#del-profile").checked;
  try {
    await api(`/api/accounts/${id}?delete_profile=${delProfile}`, {
      method: "DELETE",
    });
    $("#modal-delete").classList.add("hidden");
    await loadAccounts();
  } catch (err) {
    alert("删除失败：" + err.message);
  }
});

// ---------- 弹窗通用 ----------
document
  .querySelectorAll("[data-close]")
  .forEach((b) =>
    b.addEventListener("click", () =>
      b.closest(".modal").classList.add("hidden"),
    ),
  );

// ---------- 新增账号 ----------
$("#btn-add").addEventListener("click", () => {
  $("#in-phone").value = "";
  $("#in-password").value = "";
  $("#in-runtime").value = globalSendTime || "";
  $("#in-account-role").value = "musician";
  $("#modal-add").classList.remove("hidden");
});
$("#btn-save-add").addEventListener("click", async () => {
  const phone = $("#in-phone").value.trim();
  const password = $("#in-password").value;
  const run_time = $("#in-runtime").value.trim() || null;
  const account_role = $("#in-account-role").value;
  if (!phone || !password) {
    alert("请填写手机号和密码");
    return;
  }
  let createdId = null;
  try {
    const acc = await api("/api/accounts", {
      method: "POST",
      body: JSON.stringify({ phone, password, run_time, account_role }),
    });
    createdId = Number(acc.id);
    $("#modal-add").classList.add("hidden");
    openRunModal(`账号 ${phone} 登录中`, acc.id);
    runningAccountIds.add(Number(acc.id));
    await loadAccounts();
    await api(`/api/login/${acc.id}`, { method: "POST" });
  } catch (err) {
    if (createdId != null) runningAccountIds.delete(createdId);
    loadAccounts();
    alert("创建失败：" + err.message);
  }
});

// ---------- 编辑账号 ----------
async function openEdit(id) {
  const a = await api(`/api/accounts/${id}`);
  $("#edit-id").value = a.id;
  $("#edit-password").value = "";
  $("#edit-runtime").value = a.run_time || "";
  $("#edit-interval").value = a.interval_days || "";
  $("#edit-enabled").checked = !!a.enabled;
  $("#edit-account-role").value = a.account_role || "musician";
  $("#edit-local-listen-enabled").checked = !!a.local_listen_enabled;
  $("#edit-local-listen-item").value = a.local_listen_item_id || "";
  syncEditRoleUI();
  $("#modal-edit").classList.remove("hidden");
}
function syncEditRoleUI() {
  const player = $("#edit-account-role").value === "player";
  $("#edit-local-listen-enabled").disabled = player;
  $("#edit-local-listen-item").disabled = player;
  if (player) $("#edit-local-listen-enabled").checked = true;
}
$("#edit-account-role").addEventListener("change", syncEditRoleUI);
$("#btn-save-edit").addEventListener("click", async () => {
  const id = $("#edit-id").value;
  const payload = {};
  const pw = $("#edit-password").value;
  const rt = $("#edit-runtime").value.trim();
  const iv = $("#edit-interval").value.trim();
  if (pw) payload.password = pw;
  if (rt) payload.run_time = rt;
  if (iv) payload.interval_days = parseInt(iv, 10);
  payload.enabled = $("#edit-enabled").checked;
  payload.account_role = $("#edit-account-role").value;
  payload.local_listen_enabled = $("#edit-local-listen-enabled").checked;
  payload.local_listen_item_id = $("#edit-local-listen-item").value.trim();
  try {
    await api(`/api/accounts/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
    $("#modal-edit").classList.add("hidden");
    await loadAccounts();
  } catch (err) {
    alert("保存失败：" + err.message);
  }
});

// ---------- 全局设置 ----------
$("#btn-settings").addEventListener("click", async () => {
  const s = await api("/api/settings");
  $("#set-send-time").value = s.default_send_time || "";
  $("#set-interval").value = s.execution_interval_days || "";
  $("#set-max-sends").value = s.max_monthly_sends || "";
  $("#set-local-listen-time").value = s.local_listen_start_time || "09:30";
  $("#set-local-listen-daily").value = s.local_listen_daily_max || "25";
  $("#set-local-listen-monthly").value = s.local_listen_monthly_max || "650";
  $("#set-local-listen-percent").value = s.local_listen_play_percent || "34";
  $("#set-log-retention").value = s.log_retention_days || "3";
  $("#set-headless").checked = s.headless === "1";
  $("#set-login-method").value = s.login_method || "auto";
  $("#set-wecom").value = s.wecom_webhook_key || "";
  $("#set-webhook-url").value = s.custom_webhook_url || "";
  $("#set-webhook-method").value = s.custom_webhook_method || "POST";
  $("#set-webhook-headers").value = s.custom_webhook_headers || "";
  $("#set-webhook-body").value = s.custom_webhook_body || "";
  $("#set-current-admin-password").value = "";
  $("#set-new-admin-password").value = "";
  $("#set-confirm-admin-password").value = "";
  $("#modal-settings").classList.remove("hidden");
});
$("#btn-save-settings").addEventListener("click", async () => {
  const values = {
    default_send_time: $("#set-send-time").value.trim(),
    execution_interval_days: $("#set-interval").value.trim(),
    max_monthly_sends: $("#set-max-sends").value.trim(),
    local_listen_start_time: $("#set-local-listen-time").value.trim(),
    local_listen_daily_max: $("#set-local-listen-daily").value.trim(),
    local_listen_monthly_max: $("#set-local-listen-monthly").value.trim(),
    local_listen_play_percent: $("#set-local-listen-percent").value.trim(),
    log_retention_days: $("#set-log-retention").value.trim(),
    headless: $("#set-headless").checked ? "1" : "0",
    login_method: $("#set-login-method").value,
    wecom_webhook_key: $("#set-wecom").value.trim(),
    custom_webhook_url: $("#set-webhook-url").value.trim(),
    custom_webhook_method: $("#set-webhook-method").value,
    custom_webhook_headers: $("#set-webhook-headers").value.trim(),
    custom_webhook_body: $("#set-webhook-body").value.trim(),
  };
  try {
    const currentPassword = $("#set-current-admin-password").value;
    const newPassword = $("#set-new-admin-password").value;
    const confirmPassword = $("#set-confirm-admin-password").value;
    if (newPassword) {
      if (!currentPassword) throw new Error("修改管理密码需要填写当前密码");
      if (newPassword !== confirmPassword) throw new Error("两次输入的新管理密码不一致");
      await api("/api/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
    }
    await api("/api/settings", {
      method: "PUT",
      body: JSON.stringify({ values }),
    });
    $("#modal-settings").classList.add("hidden");
    await refreshGlobalSendTime();
    await loadAccounts();
  } catch (err) {
    alert("保存失败：" + err.message);
  }
});

$("#btn-logout").addEventListener("click", async () => {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (_) {}
  location.href = "/login";
});

$("#btn-listen-all").addEventListener("click", async () => {
  const button = $("#btn-listen-all");
  button.disabled = true;
  button.textContent = "启动中...";
  try {
    const res = await api("/api/tasks/local-listen/start-all", { method: "POST" });
    alert(res.message);
  } catch (err) {
    alert("启动失败：" + err.message);
  } finally {
    await refreshActiveAndList();
  }
});

$("#btn-stop-listen-all").addEventListener("click", async () => {
  const button = $("#btn-stop-listen-all");
  button.disabled = true;
  button.textContent = "停止中...";
  try {
    const res = await api("/api/tasks/local-listen/stop-all", { method: "POST" });
    alert(res.message);
  } catch (err) {
    alert("停止失败：" + err.message);
  } finally {
    button.textContent = "停止播放";
    await refreshActiveAndList();
  }
});

$("#btn-clear-all-logs").addEventListener("click", async () => {
  if (!confirm("确认清除所有账号的历史日志？此操作不可撤销。")) return;
  try {
    const res = await api("/api/tasks/logs", { method: "DELETE" });
    alert(res.message);
    if (viewingAccountId != null) $("#log-box").innerHTML = "";
  } catch (err) {
    alert("清除失败：" + err.message);
  }
});

$("#btn-clear-log").addEventListener("click", () => {
  $("#log-box").innerHTML = "";
});
$("#btn-refresh-log").addEventListener("click", async () => {
  const id = $("#run-modal-account").value;
  if (id) await loadHistoryLogs(id);
});

// ---------- 强制停止 ----------
$("#btn-force-stop").addEventListener("click", () => {
  const id = $("#run-modal-account").value;
  if (!id) {
    alert("当前无可停止的任务");
    return;
  }
  $("#stop-account-id").value = id;
  $("#modal-stop").classList.remove("hidden");
});
$("#btn-confirm-stop").addEventListener("click", async () => {
  const id = $("#stop-account-id").value;
  try {
    const res = await api(`/api/tasks/${id}/stop`, { method: "POST" });
    $("#modal-stop").classList.add("hidden");
    appendLog("", res.message || "已发送停止指令", "warn");
    await refreshActiveAndList();
  } catch (err) {
    alert("停止失败：" + err.message);
  }
});

// ---------- 初始化 ----------
connectWS();
refreshGlobalSendTime().then(refreshActiveAndList);
setInterval(refreshActiveAndList, 30000);
