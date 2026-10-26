"use strict";
const $ = (id) => document.getElementById(id);
const TYPES = { leave: "Nghỉ phép", adjustment: "Sửa công", ot: "OT" };
const STATUS = { pending: "Chờ duyệt", approved: "Đã duyệt", rejected: "Từ chối" };
const MANAGER_ROLES = ["manager", "hr", "admin"];
let me = null;

function token() { try { return sessionStorage.getItem("token"); } catch { return null; } }

function say(text, kind) {
  const el = $("msg");
  el.textContent = text || "";
  el.className = kind || "";
  el.hidden = !text;
}

class ApiError extends Error {
  constructor(status, detail) { super(detail); this.status = status; }
}

async function api(method, path, body) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token()) headers["Authorization"] = "Bearer " + token();
  const res = await fetch("/api" + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (res.status === 401 && path !== "/auth/login") { logout(); throw new ApiError(401, "Phiên đăng nhập đã hết hạn"); }
  if (!res.ok) {
    const d = data && data.detail;
    throw new ApiError(res.status, typeof d === "string" ? d : "Dữ liệu không hợp lệ");
  }
  return data;
}

const fmtTime = (iso) => (iso ? new Date(iso).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" }) : "-");
const fmtDate = (d) => d.split("-").reverse().join("/");

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}

function show(loggedIn) {
  $("login-view").hidden = loggedIn;
  $("app-view").hidden = !loggedIn;
  $("who").hidden = !loggedIn;
}

function logout() {
  try { sessionStorage.removeItem("token"); } catch { /* ignore */ }
  me = null;
  show(false);
}

async function loadDaily() {
  const data = await api("GET", "/attendance/me?size=10");
  const body = $("daily");
  body.replaceChildren();
  for (const r of data.items) {
    const tr = el("tr");
    [fmtDate(r.work_date), fmtTime(r.first_in), fmtTime(r.last_out), r.worked_minutes, r.ot_minutes]
      .forEach((v) => tr.appendChild(el("td", String(v))));
    body.appendChild(tr);
  }
  if (!data.items.length) { const tr = el("tr"); const td = el("td", "Chưa có dữ liệu công"); td.colSpan = 5; tr.appendChild(td); body.appendChild(tr); }
}

function requestLine(r) {
  return `${TYPES[r.type]} ngày ${fmtDate(r.work_date)}` + (r.start_at ? ` (${fmtTime(r.start_at)}-${fmtTime(r.end_at)})` : "") + (r.reason ? ` - ${r.reason}` : "");
}

async function loadMyRequests() {
  const data = await api("GET", "/requests/me?size=10");
  const ul = $("my-requests");
  ul.replaceChildren();
  for (const r of data.items) ul.appendChild(el("li", `${requestLine(r)} [${STATUS[r.status]}]`));
  if (!data.items.length) ul.appendChild(el("li", "Chưa có đơn nào", "muted"));
}

async function decide(id, action) {
  try {
    await api("POST", `/requests/${id}/${action}`);
    say(action === "approve" ? "Đã duyệt đơn" : "Đã từ chối đơn", "ok");
  } catch (e) {
    say(e.status === 409 ? "Đơn đã được người khác xử lý" : e.message, "error");
  }
  await loadPending();
}

async function loadPending() {
  const data = await api("GET", "/requests?status=pending&size=20");
  const ul = $("pending");
  ul.replaceChildren();
  for (const r of data.items) {
    const li = el("li", `Nhân viên #${r.employee_id}: ${requestLine(r)}`);
    const actions = el("div", undefined, "actions");
    const ok = el("button", "Duyệt");
    const no = el("button", "Từ chối", "bad");
    ok.onclick = () => decide(r.id, "approve");
    no.onclick = () => decide(r.id, "reject");
    actions.append(ok, no);
    li.appendChild(actions);
    ul.appendChild(li);
  }
  if (!data.items.length) ul.appendChild(el("li", "Không có đơn chờ duyệt", "muted"));
}

async function enter() {
  show(true);
  $("who-name").textContent = `${me.full_name} (${me.code})`;
  $("approve-view").hidden = !MANAGER_ROLES.includes(me.role);
  const jobs = [loadDaily(), loadMyRequests()];
  if (MANAGER_ROLES.includes(me.role)) jobs.push(loadPending());
  await Promise.all(jobs);
}

$("login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  say("");
  try {
    const data = await api("POST", "/auth/login", { code: $("code").value.trim(), password: $("password").value });
    sessionStorage.setItem("token", data.access_token);
    me = data.employee;
    $("password").value = "";
    await enter();
  } catch (e) {
    say(e.status === 401 ? "Sai mã nhân viên hoặc mật khẩu" : e.message, "error");
  }
});

$("logout").addEventListener("click", () => { say(""); logout(); });

$("check").addEventListener("click", async () => {
  const btn = $("check");
  btn.disabled = true;
  try {
    // One UUID per click: retrying the same click is safe (idempotent on the server).
    const r = await api("POST", "/attendance/check", { idempotency_key: crypto.randomUUID(), source: "web" });
    $("check-result").textContent = `Đã ghi nhận lúc ${fmtTime(r.event_time)}`;
    say("Chấm công thành công", "ok");
    await loadDaily();
  } catch (e) {
    say(e.message, "error");
  } finally {
    btn.disabled = false;
  }
});

$("req-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const date = $("req-date").value;
  const iso = (t) => (t ? `${date}T${t}:00+07:00` : null);
  const body = { type: $("req-type").value, work_date: date, start_at: iso($("req-start").value), end_at: iso($("req-end").value), reason: $("req-reason").value };
  try {
    await api("POST", "/requests", body);
    say("Đã gửi đơn", "ok");
    ev.target.reset();
    await loadMyRequests();
  } catch (e) {
    say(e.status === 409 ? "Kỳ lương đã khóa, không thể tạo đơn cho ngày này" : e.status === 422 ? "Thiếu giờ bắt đầu/kết thúc hoặc giờ không hợp lệ" : e.message, "error");
  }
});

(async function init() {
  if (!token()) return show(false);
  try {
    me = await api("GET", "/me");
    await enter();
  } catch { show(false); }
})();
