// Live monitor for HR/admin: polls /api/monitor/live once a second while the tab is visible.
// Reuses the login token that the main page keeps in sessionStorage (same origin).
const BUSINESS_TZ = "Asia/Ho_Chi_Minh"; // TIMEZONE in .env
const WINDOW_SECONDS = 60;
const FEED_SIZE = 25;
const $ = (id) => document.getElementById(id);
const fmtTime = (iso) => new Date(iso).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: BUSINESS_TZ });
const fmtNum = (n) => n.toLocaleString("vi-VN");
let lastSeenId = 0;

function token() { try { return sessionStorage.getItem("token"); } catch { return null; } }

async function tick() {
  if (document.hidden) return;
  const t = token();
  if (!t) { location.href = "./"; return; }
  let res;
  try {
    res = await fetch(`/api/monitor/live?seconds=${WINDOW_SECONDS}&feed=${FEED_SIZE}`, { headers: { Authorization: `Bearer ${t}` } });
  } catch {
    $("state").textContent = "Mất kết nối tới máy chủ, đang thử lại…";
    return;
  }
  if (res.status === 401) { location.href = "./"; return; }
  if (res.status === 403) { $("state").textContent = "Chỉ HR hoặc admin xem được trang này. Hãy đăng nhập bằng tài khoản HR."; return; }
  if (!res.ok) { $("state").textContent = `Máy chủ đang bận (mã ${res.status}), sẽ tự thử lại.`; return; }
  render(await res.json());
}

function render(d) {
  $("state").textContent = `Cập nhật lúc ${fmtTime(new Date().toISOString())}, tự làm mới mỗi giây.`;
  $("checkins").textContent = fmtNum(d.checkins);
  $("employees").textContent = fmtNum(d.active_employees);
  $("requests").textContent = fmtNum(d.requests_created);

  // One slot per second, ending at the server's clock; seconds without check-ins are 0.
  const counts = new Map(d.per_second.map((p) => [Math.floor(Date.parse(p.ts) / 1000), p.events]));
  const end = Math.floor(Date.parse(d.server_time) / 1000);
  const series = Array.from({ length: WINDOW_SECONDS }, (_, i) => counts.get(end - WINDOW_SECONDS + 1 + i) || 0);
  // The newest second is still filling up, so the rate uses the 5 complete seconds before it.
  const recent = series.slice(-6, -1);
  $("rate").textContent = fmtNum(Math.round(recent.reduce((a, b) => a + b, 0) / recent.length));
  const max = Math.max(1, ...series);
  $("bars").replaceChildren(...series.map((n) => {
    const bar = document.createElement("div");
    bar.style.height = `${(n / max) * 100}%`;
    bar.title = `${n} lượt`;
    return bar;
  }));

  const rows = d.latest.map((e) => {
    const tr = document.createElement("tr");
    if (lastSeenId && e.event_id > lastSeenId) tr.className = "new";
    for (const text of [fmtTime(e.event_time), e.code, e.full_name, e.source]) {
      const td = document.createElement("td");
      td.textContent = text;
      tr.append(td);
    }
    return tr;
  });
  $("feed").tBodies[0].replaceChildren(...rows);
  if (d.latest.length) lastSeenId = Math.max(lastSeenId, d.latest[0].event_id);
}

tick();
setInterval(tick, 1000);
document.addEventListener("visibilitychange", tick);
