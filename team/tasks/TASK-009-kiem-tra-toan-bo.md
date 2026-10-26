# TASK-009: Kiểm tra toàn bộ trước khi đo tải

- **Giao cho:** tester
- **Trạng thái:** Đang làm
- **Liên quan:** TASK-001, 003, 005, 006, 008

## Mục tiêu
Xác nhận toàn bộ hệ thống chạy đúng từ đầu, theo đúng thứ tự một người mới sẽ làm. Báo pass/fail kèm bằng chứng. Không sửa code.

## Các bước (chạy ở `~/projects/chamcong-demo`)
1. `docker compose config -q`, sau đó `docker compose up -d --build`. Chờ `curl -s localhost:8000/health` trả `"status":"ok"`. Kiểm tra `docker compose ps`: có `api`, `worker`, `db`, `redis`, `backup` và **không có** `tunnel`.
2. `docker compose exec api python -m scripts.seed`, rồi chạy thêm `--help` để xem tham số.
3. `docker compose run --rm api pytest -q`, chạy **3 lần**, ghi số test pass/fail của từng lần.
4. Smoke test bằng curl, lấy mật khẩu từ biến `SEED_PASSWORD` trong `.env` và **không in mật khẩu ra báo cáo**:
   - Login E0021 (employee), E0004 (manager), E0002 (hr).
   - Employee: `GET /api/me`. Chấm công 2 lần cùng key: lần đầu 201, lần sau 200 kèm `duplicate`. Tạo đơn OT có `Idempotency-Key`.
   - Manager: `GET /api/requests?status=pending`, duyệt 1 đơn được 200. Duyệt lại cùng đơn đó được 200 kèm `replayed`.
   - HR:
     - `POST /api/payroll/periods/{id}/calculate` cho kỳ draft: 202, sau đó poll `GET /api/payroll/jobs/{job_id}` tới khi xong.
     - Employee gọi `GET /api/payroll/periods/{id}/payslips/me` thấy payslip.
   - Employee gọi endpoint của hr: 403. Không có token: 401. Header `Host: evil.com`: 400.
   - `curl -sI localhost:8000/app.js | grep -i cache-control` có `max-age=86400`.
5. `./scripts/backup/list-backups.sh`, `./scripts/backup/backup-now.sh`, rồi `./scripts/backup/drill.sh --yes`. Dán bảng PASS/FAIL của drill.
6. `./loadtest/run.sh --help` hoặc đọc phần usage. Chạy `STEADY_DURATION=20 ./loadtest/run.sh steady`. Kiểm thư mục kết quả có đủ file và `report.md` có các mục.
7. Chụp màn hình frontend ở khổ 360px bằng Playwright:
   - Gồm màn đăng nhập và màn sau khi đăng nhập bằng E0004.
   - Nếu máy có Playwright của Node trong `~/projects/quan-ly-tai-chinh/node_modules`, chạy bằng `LD_LIBRARY_PATH=$HOME/.local/pwlibs/root/usr/lib/x86_64-linux-gnu`. Lưu ảnh vào scratchpad, không lưu vào repo.
   - Nếu không chạy được thì ghi "bỏ qua" kèm lý do.

## Đầu ra mong muốn
Báo cáo theo format của tester. Với từng bước: pass/fail, lệnh đã chạy, và đoạn output ngắn làm bằng chứng. Nếu fail thì ghi file:line hoặc log lỗi.

## Không được đụng
- Không sửa file nào trong repo. Không bật profile `tunnel`, không `docker compose down -v` stack chính, không chạy `pkill -f`.

---
## Kết quả và review của leader

### Lần 1 (2026-10-26)
- Báo cáo của tester (tóm tắt):
  - Bước 1–6 PASS: stack chạy, seed xong, pytest 92 passed ×3, smoke test đủ mã trạng thái, drill OVERALL PASS 6/6, steady 20 giây có đủ file và mục.
  - Bước 7 bỏ qua vì không có thư viện Playwright.
- Đánh giá: Đạt.
- Nhận xét: leader chụp màn đăng nhập ở 360px bằng chrome-headless-shell có sẵn trong `~/.cache/ms-playwright` (cờ `--screenshot`), hiển thị ổn. Màn sau khi đăng nhập cần điều khiển bằng JS nên chưa chụp, ghi nhận là hạn chế.
