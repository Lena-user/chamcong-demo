# TASK-005: Ghi an toàn, chống mất request, chịu quá tải có kiểm soát

- **Giao cho:** coder
- **Trạng thái:** Đang làm
- **Liên quan:** TASK-003, TASK-004 (lần chạy `login_storm` thật), PROJECT.md NV-1 đến NV-6

## Mục tiêu
Sếp yêu cầu ghi vào DB phải đúng và đủ, hạn chế tối đa mất request. Khi quá tải, hệ thống phải từ chối có kiểm soát (503 kèm `Retry-After`, client gửi lại với cùng key), không được ra 500 và không mất dữ liệu đã xác nhận.

## Đầu vào
- Lần chạy thật `login_storm` 100 user ở TASK-004 cho kết quả: p95 19 giây, **48% lỗi 500**. Log báo `QueuePool limit of size 10 overflow 5 reached, connection timed out`. RAM của api lên 2043/2048 MiB.
- Leader phân tích nguyên nhân:
  1. `employees/service.py::login` vẫn giữ connection DB (transaction đang mở) trong lúc chạy argon2, nên pool cạn.
  2. `asyncio.to_thread` dùng executor mặc định, khoảng 12 thread mỗi process (`os.cpu_count()` trong container là 8). Mỗi hash argon2 mặc định tốn 64 MiB, nên 3 worker × 12 × 64 MiB ≈ 2,3 GB. Có nguy cơ OOM.
- Code hiện tại: `app/core/security.py`, `app/core/db.py`, `app/main.py`, `app/modules/*`, `app/workers/`, `app/static/`.

## Đầu ra mong muốn
1. **Đăng nhập:**
   - Đọc nhân viên xong thì kết thúc transaction và trả connection (`await session.commit()`, `rollback()` hoặc `close()`) **trước khi** hash.
   - Hash và verify chạy trên một `ThreadPoolExecutor` riêng, giới hạn bằng `asyncio.Semaphore`. Kích thước là `HASH_CONCURRENCY`, mặc định 2 mỗi process.
   - Tham số argon2 đưa vào Settings (`ARGON2_TIME_COST`, `ARGON2_MEMORY_KIB`, `ARGON2_PARALLELISM`, mặc định bằng mặc định của argon2-cffi). Hash cũ vẫn verify được.
   - Nếu số request đang chờ hash vượt `LOGIN_MAX_WAITING` (mặc định 200 mỗi process) thì trả 503 kèm `Retry-After`.
2. **Quá tải trả 503, không trả 500:**
   - Thêm exception handler cho các lỗi: pool timeout (`sqlalchemy.exc.TimeoutError`), mất kết nối DB, `too many connections`, Redis không kết nối được. Các trường hợp này trả 503 với `{"detail": "..."}` và `Retry-After: <RETRY_AFTER_SECONDS>`.
   - Riêng serialization failure và deadlock (SQLSTATE 40001, 40P01) thì service tự retry tối đa 3 lần với backoff ngắn, vì các thao tác ghi ở đây đều idempotent. Hết lần retry thì trả 503.
   - Log mỗi lần trả 503, kèm request id.
3. **Idempotency cho mọi lệnh tạo:**
   - `POST /api/requests` nhận header `Idempotency-Key` (UUID). Header là tùy chọn để không phá hợp đồng cũ, nhưng frontend và load test sẽ luôn gửi.
   - Migration mới: cột `requests.idempotency_key` UNIQUE, nullable. Dùng `ON CONFLICT DO NOTHING`. Gửi trùng key thì trả 200 kèm đơn cũ. Key trùng nhưng của nhân viên khác thì trả 409.
   - `POST /api/payroll/periods`: kỳ đã tồn tại thì trả 409, giữ như cũ.
4. **Duyệt hoặc từ chối đơn idempotent:** nếu UPDATE ảnh hưởng 0 dòng mà đơn đã ở đúng trạng thái đích do **chính người này** quyết định, thì trả 200 kèm `"replayed": true`. Các trường hợp khác vẫn trả 409. Nhờ vậy client gửi lại sau timeout không bị báo lỗi sai.
5. **Job nền:**
   - Job tính lương có `max_tries=3`. Thêm `job_timeout` lấy từ Settings.
   - Khi worker khởi động, đưa các kỳ kẹt ở `calculating` lâu hơn `job_timeout` về `draft`. Cần thêm cột thời điểm bắt đầu tính (migration).
   - Job phải idempotent: chạy lại không nhân đôi payslip.
5b. **Khóa kỳ an toàn:** `lock` trả 409 nếu có đơn thuộc kỳ được quyết định sau `calculated_at`, kèm thông báo "cần tính lại lương". Kiểm tra trong cùng transaction, với dòng kỳ đã khóa `FOR UPDATE`. Có test cho trường hợp này.
6. **Tắt êm:** CMD uvicorn thêm `--timeout-graceful-shutdown 25`.
7. **Frontend:**
   - Mọi lệnh ghi (chấm công, tạo đơn, duyệt đơn) retry tối đa 3 lần với backoff khi gặp lỗi mạng, 502, 503 hoặc 504. Mỗi lần retry giữ **cùng** idempotency key.
   - Chấm công có hàng đợi lưu trong `localStorage` (outbox). Khi offline hoặc retry vẫn lỗi thì giữ lại và gửi tiếp khi tải lại trang hoặc khi có mạng lại. Hiện số bản ghi đang chờ gửi.
8. **Test:**
   - Patch hàm hash cho chậm, khoảng 0,5 giây, rồi bắn đồng thời `pool_size + max_overflow + 10` login. Tất cả phải ra 200, không có 500.
   - Giả lập pool timeout qua dependency override → nhận 503 có `Retry-After`.
   - 20 lệnh `POST /api/requests` cùng key → đúng 1 dòng.
   - Approve lặp lại bởi cùng manager → 200 kèm `replayed`. Manager khác làm → 409.
   - Kỳ kẹt ở `calculating` được đưa về `draft` khi worker khởi động.
9. `.env.example`: thêm các biến mới, có comment.
10. **Gia cố theo kiểu hệ thống tài chính** (leader thiết kế):
    - **Timeout ở tầng kết nối**: truyền qua asyncpg `server_settings`, lấy từ Settings:
      - `DB_STATEMENT_TIMEOUT_MS=5000`
      - `DB_LOCK_TIMEOUT_MS=2000`
      - `DB_IDLE_IN_TX_TIMEOUT_MS=10000`

      Lỗi 57014 (query canceled) và 55P03 (lock not available) thì trả 503 có thể retry. Mục đích: lỗi nhanh thay vì để request dồn ứ.
    - **Thời gian lấy từ đồng hồ DB**: `event_time`, `created_at` và `decided_at` lấy từ `now()` của Postgres, không lấy giờ của app. Như vậy 3 worker không lệch giờ nhau.
    - **Health tách hai loại**:
      - `/health/live`: chỉ kiểm process còn sống, không gọi DB.
      - `/health/ready`: kiểm DB và Redis.
      - Giữ `/health` bằng `ready` để tương thích. Healthcheck của compose vẫn dùng `/health`.
    - **Request ID và log có cấu trúc**:
      - Middleware nhận hoặc sinh `X-Request-ID` và trả lại trong response.
      - Mỗi request ghi một dòng log JSON gồm `request_id`, method, route template (không ghi path có id), status, `duration_ms`, `user_id` (nếu có).
      - Bật tắt bằng `ACCESS_LOG=true`. Tắt access log mặc định của uvicorn để không ghi đôi.
    - **Rate limit bằng Redis** (Lua script, nguyên tử):
      - Login giới hạn theo mã nhân viên (`RATE_LOGIN_PER_MIN=20`), chống dò mật khẩu.
      - Lệnh ghi giới hạn theo user (`RATE_WRITE_PER_MIN=600`).
      - Vượt giới hạn thì trả 429 kèm `Retry-After`.
      - `RATE_LIMIT_ENABLED=true` là mặc định. Khi load test thì đặt `false` trong `.env` và ghi rõ trong docs.
      - Redis lỗi thì cho qua (fail-open) và ghi log. Hành vi này cấu hình được bằng `RATE_LIMIT_FAIL_OPEN=true`.
    - **pg_stat_statements**: thêm migration `CREATE EXTENSION IF NOT EXISTS pg_stat_statements`. Phần nạp thư viện (`shared_preload_libraries`) leader tự thêm vào compose.
    - Có test cho các mục: rate limit trả 429, `X-Request-ID` được trả lại, lỗi lock timeout ra 503, `/health/live` không gọi DB.

## Tiêu chí nghiệm thu
- [ ] `docker compose run --rm api pytest -q` pass, chạy 3 lần đều pass.
- [ ] `LOGIN_STORM_DURATION=30 ./loadtest/run.sh login_storm`: không có lỗi 500, RAM của api dưới 1 GB. Chấp nhận p95 cao, vì đó là giới hạn CPU của argon2. Dán phần "Mục tiêu so với thực tế" và "CPU và RAM cao nhất" vào báo cáo.
- [ ] `STEADY_DURATION=30 ./loadtest/run.sh steady` chạy được, dán kết quả vào báo cáo.
- [ ] Code, tên biến và comment bằng tiếng Anh.

## Không được đụng
- `loadtest/`, `scripts/backup/`, `team/`, `docs/`.
- Trong `docker-compose.yml` chỉ được thêm biến vào khối `x-app-env` (theo kiểu `${VAR:-default}`) và sửa command của api nếu cần. Phần còn lại TASK-006 đang sửa, nên sửa bằng Edit nhỏ và đọc lại file trước mỗi lần sửa.
- Không chạy `pkill -f`. Không commit, không push, không bật profile `tunnel`.

---
## Kết quả và review của leader
