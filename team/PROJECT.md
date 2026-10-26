# Chấm công & tính lương: demo chịu tải

> Nguồn sự thật của dự án. Leader viết, sếp duyệt.
> Agent không đọc file này, trừ khi file task chỉ định một mục cụ thể.

## 1. Mục tiêu
- Web chấm công và tính lương để demo cho người hướng dẫn. Chạy trên laptop, public qua Cloudflare Quick Tunnel.
- Thành công nghĩa là chứng minh được hệ thống chịu khoảng 100 request đồng thời (p95 < 500ms, lỗi < 1%) và chỉ ra giới hạn thực tế nằm ở đâu bằng số liệu đo được.
- Phạm vi bản này dừng ở mức demo khả năng chịu tải (sếp chốt ngày 2026-10-26, hạn 06:00 ngày 2026-10-27). Bỏ xuất Excel và module reports.
- Chỉ dùng dữ liệu giả.

## 2. Nghiệp vụ và quy tắc đồng thời
- NV-1 Chấm công idempotent: client gửi `idempotency_key` (UUID). Dùng `INSERT ... ON CONFLICT DO NOTHING`. Request trùng trả lại kết quả cũ.
- NV-2 Sửa hồ sơ dùng optimistic locking: `UPDATE ... WHERE id = :id AND version = :version`. 0 dòng bị ảnh hưởng thì trả 409.
- NV-3 Duyệt đơn dùng conditional update: `UPDATE ... WHERE id = :id AND status = 'pending'`. Chỉ một người thắng, những người còn lại nhận 409.
- NV-4 Tính lương: kỳ đang `calculating` hoặc `locked` thì từ chối mọi điều chỉnh vào kỳ đó (tạo hoặc duyệt đơn có `work_date` thuộc kỳ). Lúc duyệt đơn, đọc kỳ bằng `SELECT ... FOR SHARE` để không chen được với lệnh chuyển kỳ sang `calculating`. Dùng `pg_advisory_lock` để mỗi lúc chỉ một job tính lương chạy.
- NV-5 `attendance_events` chỉ INSERT. Sửa công chỉ đi qua `requests`.
- NV-6 Ràng buộc đặt trong DB (UNIQUE, FK, CHECK), không chỉ kiểm tra trong code.
- Mỗi quy tắc NV-1 đến NV-6 phải có test: bắn đồng thời bằng `asyncio.gather`, rồi kiểm tra kết quả cuối cùng trong DB.
- Trạng thái kỳ lương: `draft → calculating → locked → paid`.
- Phân quyền: `employee` chỉ xem và sửa dữ liệu của mình. `manager` duyệt đơn của phòng mình. `hr` và `admin` làm mọi việc. Chỉ `hr` và `admin` đọc được `employee_sensitive`.
- `audit_logs` ghi lại các hành động xem lương, xem dữ liệu nhạy cảm và sửa hồ sơ.
- Quy tắc tính lương (giá trị mẫu, phải ghi chú là dữ liệu demo và cần kế toán xác nhận):
  - Lương theo công = lương cơ bản × số ngày công / 26.
  - OT = lương giờ × 150% (lương giờ = lương cơ bản / 26 / 8).
  - BHXH 8%, BHYT 1.5%, BHTN 1%, tính trên lương cơ bản.
  - Giảm trừ bản thân 15.500.000đ, mỗi người phụ thuộc 6.200.000đ.
  - Thuế TNCN lũy tiến mẫu 5 bậc: 5% đến 10tr, 10% đến 30tr, 20% đến 60tr, 30% đến 100tr, 35% phần trên 100tr.

## 3. Stack (đã chốt, không đổi)
- Python 3.12, FastAPI (async), Pydantic v2.
- SQLAlchemy 2.x async + asyncpg, Alembic.
- PostgreSQL 16, Redis 7, Arq cho job nền.
- uvicorn 3 worker.
- Docker Compose, cloudflared (quick tunnel) chạy như một service.
- pytest + pytest-asyncio + httpx.
- Locust cho load test, pandas + matplotlib cho script báo cáo.
- Frontend: HTML + JS thuần, tối giản.
- Thư viện thêm: argon2-cffi, PyJWT.

## 4. Cấu trúc thư mục
```
app/
  core/        config (pydantic-settings), DB engine/session, security (JWT, argon2), RBAC dependency, audit
  modules/
    employees/   router.py, service.py, repository.py, schemas.py, models.py
    attendance/
    requests/    đơn nghỉ phép, sửa công, OT
    payroll/
  workers/     Arq WorkerSettings, job tính lương
  static/      frontend tối giản
  main.py
migrations/    Alembic
loadtest/      Dockerfile, locustfile, các kịch bản, run.sh, report.py, results/
scripts/       seed dữ liệu giả, init SQL
tests/
docs/          hướng dẫn sử dụng
```
- Phân tầng: router chỉ validate và gọi service. Service chứa logic nghiệp vụ và quản lý transaction. Repository chỉ chạy query.
- Module này không truy vấn thẳng bảng của module khác, phải đi qua service của module đó.

## 5. Mô hình dữ liệu
- `employees`: id, code (UNIQUE, dạng `E0001`), full_name, department, hired_on, left_on, role (CHECK employee/manager/hr/admin), password_hash, version (int, mặc định 1), created_at.
- `employee_sensitive`: employee_id (PK, FK), national_id (CCCD), bank_account, tax_code, dependents (CHECK >= 0).
- `salary_contracts`: id, employee_id FK, base_salary BIGINT, allowance BIGINT, effective_from, effective_to (nullable). CHECK base_salary > 0.
- `shifts`: id, name, start_time, end_time, crosses_midnight. `schedules`: employee_id, work_date, shift_id, UNIQUE(employee_id, work_date).
- `attendance_events`: id, employee_id FK, event_time timestamptz, source, idempotency_key UUID UNIQUE, created_at. Chỉ INSERT.
- `attendance_daily`: employee_id, work_date, first_in, last_out, worked_minutes, ot_minutes, UNIQUE(employee_id, work_date). Luôn tính lại được từ events.
- `requests`: id, employee_id, type (CHECK leave/adjustment/ot), work_date, start_at, end_at, reason, status (CHECK pending/approved/rejected), approved_by, decided_at, created_at.
- `payroll_periods`: id, year, month, status (CHECK draft/calculating/locked/paid), UNIQUE(year, month).
- `payslips`: id, period_id, employee_id, các cột tiền BIGINT, rule_version, created_at, UNIQUE(period_id, employee_id).
- `payroll_rules`: id, version, effective_from, params JSONB, note.
- `audit_logs`: id, actor_id, action, target_type, target_id, at timestamptz.
- Tiền lưu BIGINT (VND), không dùng float. Thời gian lưu timestamptz, tính theo `Asia/Ho_Chi_Minh` (ngày công của ca đêm qua 0h tính theo ngày bắt đầu ca).
- Index: `attendance_events(employee_id, event_time)`, `requests(status, created_at)`.

## 6. Hợp đồng API
Tất cả đều có tiền tố `/api`, trừ `/health` và file tĩnh. Auth dùng `Authorization: Bearer <JWT>`, JWT hết hạn sau 12 giờ. Danh sách trả về `{items, total, page, size}`, `size` tối đa 100, mặc định 20.

| Method | Path | Quyền | Ghi chú |
|---|---|---|---|
| GET | /health | public | `{"status":"ok","db":"ok","redis":"ok"}`, trả 503 nếu một trong hai lỗi |
| POST | /api/auth/login | public | `{code, password}` → `{access_token, token_type, employee}` |
| GET | /api/me | mọi role | hồ sơ của mình, kèm `version` |
| GET | /api/employees | manager+ | phân trang, lọc `department` |
| PATCH | /api/employees/{id} | chính mình hoặc hr+ | `{version, full_name?, department?}`. Đúng version thì trả 200 với version mới, sai thì 409 |
| GET | /api/employees/{id}/sensitive | hr+ | ghi audit |
| POST | /api/attendance/check | mọi role | `{idempotency_key, source}` → `{event_id, event_time, duplicate}`. Lần đầu trả 201, lần trùng trả 200 |
| GET | /api/attendance/me | mọi role | danh sách attendance_daily, lọc `from` và `to`, phân trang |
| POST | /api/requests | mọi role | `{type, work_date, start_at?, end_at?, reason}` → 201. Trả 409 nếu kỳ đã khóa |
| GET | /api/requests/me | mọi role | phân trang |
| GET | /api/requests | manager+ | lọc `status`, phân trang (manager chỉ thấy phòng mình) |
| POST | /api/requests/{id}/approve | manager+ | 200, hoặc 409 nếu đơn không còn pending hoặc kỳ đã khóa |
| POST | /api/requests/{id}/reject | manager+ | như trên |
| POST | /api/payroll/periods | hr+ | `{year, month}` → 201 |
| POST | /api/payroll/periods/{id}/calculate | hr+ | 202 `{job_id}`, 409 nếu kỳ không ở `draft` |
| GET | /api/payroll/jobs/{job_id} | hr+ | trạng thái job Arq |
| POST | /api/payroll/periods/{id}/lock | hr+ | 409 nếu kỳ chưa tính xong |
| GET | /api/payroll/periods/{id}/payslips/me | mọi role | ghi audit |

Lỗi trả `{"detail": "..."}`.

## 7. Dữ liệu seed
- 300 nhân viên giả, mã `E0001` đến `E0300`, 5 phòng ban. `E0001` là admin, `E0002` và `E0003` là hr, `E0004` đến `E0013` là manager (mỗi phòng 2 người), còn lại là employee.
- Tất cả dùng chung mật khẩu lấy từ biến `SEED_PASSWORD` trong `.env`. Không hard-code. Hash một lần rồi dùng lại cho mọi tài khoản để seed nhanh.
- Có hợp đồng lương, ca làm (gồm một ca đêm qua 0h), lịch phân ca và khoảng 3 tháng chấm công, một ít đơn pending, một kỳ lương `draft` và một bộ `payroll_rules` mẫu.
- Script seed chạy lại được: xóa dữ liệu seed cũ rồi tạo lại.

## 8. Hiệu năng và hạ tầng
- Mục tiêu: 100 request đồng thời, p95 < 500ms, tỷ lệ lỗi < 1%, với giới hạn container: api 2 CPU / 2GB, db 1 CPU / 2GB. Thêm: worker 1 CPU / 512MB, redis 0.5 CPU / 256MB, locust 1 CPU / 1GB.
- Mọi endpoint dùng `async def`. Không gọi code blocking trong async.
- Pool: `pool_size=10, max_overflow=5, pool_timeout=10, pool_pre_ping=True`.
- argon2 chạy trong thread pool (`run_in_threadpool` hoặc `asyncio.to_thread`).
- Mỗi endpoint chỉ 1 đến 3 query, transaction ngắn. Việc nặng đẩy sang Arq.
- File tĩnh trả `Cache-Control: public, max-age=86400`.
- `TrustedHostMiddleware` cho phép `localhost`, `127.0.0.1`, `api`, `*.trycloudflare.com`, cấu hình qua `.env`.
- Compose gồm các service `tunnel`, `api`, `worker`, `db`, `redis`, và `locust` (profile `loadtest`). Riêng `tunnel` đặt trong profile `tunnel` để không tự public ra internet khi chạy `up`.
  - tunnel: `cloudflare/cloudflared`, command `tunnel --no-autoupdate --url http://api:8000`.
  - db: `postgres:16`, `shared_buffers=512MB`, `effective_cache_size=1GB`, `max_connections=100`, có volume, healthcheck. api chỉ khởi động khi db healthy.
  - api publish `127.0.0.1:8000` (chỉ localhost). Cổng 8788 và 5173 trên máy là của sếp, không dùng.
- Secret để trong `.env` (gitignore), có `.env.example`.
- `.gitattributes` ép LF cho mọi file text.

## 9. Load test
- Kịch bản (chọn bằng biến `SCENARIO`):
  - `steady`: 100 user, nghỉ 1–3 giây giữa các request, chạy 3 phút.
  - `burst`: 100 user không nghỉ, tức luôn có khoảng 100 request đang xử lý cùng lúc.
  - `ramp`: tăng dần 50 → 100 → 150 → 200 → 300 user để tìm điểm gãy.
  - `login_storm`: 100 user cùng đăng nhập liên tục, để chỉ ra giới hạn của argon2 trên 2 CPU.
- Ngoài `login_storm`, user chỉ đăng nhập một lần lúc bắt đầu.
- Số chính đo trong mạng Docker (bắn thẳng `http://api:8000`). Qua tunnel chỉ để tham khảo, vì Quick Tunnel giới hạn 200 request đang xử lý và độ trễ phụ thuộc mạng nhà.
- `run.sh` vừa chạy Locust headless vừa lấy mẫu `docker stats` để biết điểm nghẽn là api hay db. `report.py` vẽ biểu đồ và ghi báo cáo markdown so với mục tiêu.

## 10. Lệnh
- Khởi động: `docker compose up -d --build`
- Migrate: `docker compose exec api alembic upgrade head`
- Seed: `docker compose exec api python -m scripts.seed`
- Test: `docker compose run --rm api pytest -q`
- Load test: `./loadtest/run.sh steady`

## 11. Vùng cấm
- Không commit `.env`, secret hay dữ liệu thật.
- Không bật tunnel (public ra internet) khi chưa hỏi sếp.
- Không push khi chưa hỏi sếp.

## 12. Quyết định đã chốt
| Ngày | Quyết định | Lý do |
|---|---|---|
| 2026-10-26 | Phạm vi dừng ở demo chịu tải, bỏ Excel và reports | Hạn 06:00 sáng 27/10 |
| 2026-10-26 | Số chính đo trong mạng Docker, tunnel chỉ để tham khảo | Quick Tunnel có giới hạn và độ trễ mạng nhà |
| 2026-10-26 | Login tách thành kịch bản riêng | argon2 trên 2 CPU là giới hạn thật, cần chỉ ra rõ |
| 2026-10-26 | Tunnel nằm trong profile riêng | Tránh vô tình public ra internet |
