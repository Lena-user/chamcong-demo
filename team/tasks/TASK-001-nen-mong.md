# TASK-001: Nền móng (hạ tầng, core, schema, auth, seed)

- **Giao cho:** coder
- **Trạng thái:** Đang làm
- **Liên quan:** PROJECT.md mục 3, 4, 5, 6, 7, 8

## Mục tiêu
Dựng bộ khung chạy được bằng `docker compose up`: hạ tầng, core, toàn bộ schema DB, đăng nhập, `/api/me`, `/health` và script seed. Các task sau sẽ chỉ thêm module trên nền này.

## Đầu vào
- Thư mục dự án: `~/projects/chamcong-demo` (đã `git init`, chưa có code).
- Đọc kỹ `team/PROJECT.md` mục 2 đến 8 trước khi làm.
- Máy chỉ có Python 3.14, nên mọi thứ chạy trong container `python:3.12-slim`. Docker có sẵn trong WSL.

## Đầu ra mong muốn
1. `.gitattributes` (`* text=auto eol=lf`, và đánh dấu binary cho `*.png`), `.gitignore` (`.env`, `__pycache__`, `.pytest_cache`, `loadtest/results/*` trừ `.gitkeep`), `.env.example` có comment giải thích từng biến.
2. `requirements.txt` ghim phiên bản cụ thể: fastapi, uvicorn[standard], pydantic, pydantic-settings, sqlalchemy[asyncio], asyncpg, alembic, redis, arq, argon2-cffi, PyJWT, pytest, pytest-asyncio, httpx.
3. `Dockerfile` dùng `python:3.12-slim`, chạy bằng user không phải root. CMD: `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 3 --proxy-headers --forwarded-allow-ips='*'`.
4. `docker-compose.yml` (`name: chamcong`) đúng mục 8 của PROJECT.md:
   - Gồm `api`, `worker` (`arq app.workers.settings.WorkerSettings`), `db`, `redis`.
   - `tunnel` nằm trong profile `tunnel`.
   - `locust` nằm trong profile `loadtest`, khai báo đúng như sau (TASK-002 sẽ viết nội dung `loadtest/`):
     ```yaml
     locust:
       build: ./loadtest
       profiles: ["loadtest"]
       volumes: ["./loadtest:/mnt/locust"]
       working_dir: /mnt/locust
       environment:
         SCENARIO: ${SCENARIO:-steady}
         LOADTEST_PASSWORD: ${SEED_PASSWORD}
       depends_on: { api: { condition: service_healthy } }
       deploy: { resources: { limits: { cpus: "1", memory: 1G } } }
     ```
   - Giới hạn tài nguyên dùng `deploy.resources.limits`.
   - api có healthcheck gọi `/health`.
   - db có healthcheck `pg_isready`, volume tên `pgdata`, và mount `scripts/init-db.sql` để tạo thêm DB `chamcong_test` cho pytest.
5. `app/core/`:
   - `config.py`: Settings đọc từ `.env`.
   - `db.py`: async engine với pool đúng mục 8, cùng dependency session.
   - `security.py`: argon2 hash và verify chạy qua `asyncio.to_thread`, JWT HS256 hạn 12 giờ.
   - `deps.py`: `get_current_user` và `require_roles(...)`.
   - `audit.py`: helper ghi `audit_logs`.
   - `redis.py`: pool Arq/Redis dùng chung, tạo trong lifespan.
6. `app/modules/<module>/models.py` cho **mọi bảng** ở mục 5, kèm UNIQUE/FK/CHECK/index. `app/core/models_base.py` (hoặc tương đương) để Alembic thấy hết model.
7. `migrations/` dùng Alembic async, có một migration đầu tạo toàn bộ schema (viết tay hoặc autogenerate rồi soát lại).
8. Module `employees` (router, service, repository, schemas) gồm `POST /api/auth/login` và `GET /api/me`. Các endpoint employees khác để TASK-003 làm.
9. `app/main.py`:
   - App có lifespan, `TrustedHostMiddleware` lấy danh sách host từ `.env`.
   - `/health` kiểm tra DB (`SELECT 1`) và Redis (`PING`), trả 503 khi lỗi.
   - Mount `app/static` ở `/` với `Cache-Control: public, max-age=86400` (`index.html` thì dùng `no-cache`). Tạo sẵn `app/static/index.html` placeholder.
10. `app/workers/settings.py`: WorkerSettings rỗng (chưa có job), chạy được.
11. `scripts/seed.py` chạy bằng `python -m scripts.seed` đúng mục 7 của PROJECT.md:
    - Dùng `COPY` hoặc bulk insert để chạy nhanh.
    - Event chấm công phải có `idempotency_key` ngẫu nhiên.
    - `attendance_daily` được tính từ events.
    - In ra tóm tắt số dòng.
12. `tests/conftest.py`:
    - Dùng DB `chamcong_test`: tạo schema (chạy migration hoặc `metadata.create_all`) và dọn dữ liệu giữa các test.
    - Client là `httpx.AsyncClient` với `ASGITransport`.
    - Có fixture tạo nhân viên theo role và lấy token.
    - Kèm test cho login (đúng/sai mật khẩu), `/api/me` (có và không có token) và `/health`.

## Tiêu chí nghiệm thu
- [ ] Copy `.env.example` sang `.env` rồi chạy `docker compose up -d --build`: api, worker, db và redis đều healthy hoặc đang chạy, `tunnel` **không** chạy.
- [ ] `curl -s localhost:8000/health` trả `{"status":"ok","db":"ok","redis":"ok"}`.
- [ ] `docker compose exec api python -m scripts.seed` chạy xong dưới 90 giây, chạy lại lần 2 vẫn được.
- [ ] Login bằng `E0021` và `SEED_PASSWORD` nhận được JWT, gọi `/api/me` trả đúng hồ sơ.
- [ ] Gọi với `Host: abc.trycloudflare.com` vẫn được, `Host: evil.com` bị 400.
- [ ] `docker compose run --rm api pytest -q` pass.
- [ ] `grep -rn "time.sleep\|import requests" app/` không có kết quả.
- [ ] Không còn TODO bỏ dở. Code, tên biến và comment viết bằng tiếng Anh.

## Không được đụng
- `team/`, `loadtest/` (trừ việc khai báo service `locust` trong compose), `docs/`.
- Cổng 8788 và 5173 trên máy là của sếp. api chỉ publish `127.0.0.1:8000`.
- Không chạy `pkill -f` (sẽ giết luôn shell). Muốn dừng thì dùng `docker compose down`.
- Không commit, không push, không bật profile `tunnel`.

## Mục PROJECT.md cần đọc
Mục 2 đến 8.

---
## Kết quả và review của leader

### Lần 1 (2026-10-26)
- Báo cáo của agent (tóm tắt): đã dựng xong nền móng gồm 12 bảng, migration 0001, login và /me, /health, seed (2,2 giây cho 300 nhân viên và 45 nghìn event), 10 test pass, `alembic check` sạch.
- Đánh giá: Đạt.
- Nhận xét: leader đã soát pool, `to_thread` cho argon2, giới hạn tài nguyên trong compose, tunnel nằm trong profile riêng, `.env` đã vào gitignore, `/health` trả ok. Ghim `redis==5.3.1` vì arq cần `redis<6`, hợp lý. Login có verify giả để chống dò mã nhân viên, tốt.
- Bước tiếp theo: TASK-003.
