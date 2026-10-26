# TASK-008: Load test ghi dồn dập, tự dừng khi gãy, đối soát không mất request

- **Giao cho:** coder
- **Trạng thái:** Đang làm
- **Liên quan:** TASK-004 (bộ load test dùng chung), TASK-005 (idempotency cho `POST /api/requests` qua header `Idempotency-Key`)

## Mục tiêu
Sếp muốn thử ghi thật dồn dập: khoảng 100 request ghi cùng lúc, rồi tăng dần cho tới khi nghẽn thì dừng. Sau khi chạy phải **đối soát** để chứng minh số liệu: mọi lệnh ghi đã được xác nhận đều nằm trong DB, không mất và không trùng.

## Đầu vào
- `loadtest/shapes.py`, `locustfile.py`, `run.sh`, `report.py`, `config.env` (TASK-004).
- Hợp đồng API ở PROJECT.md mục 6. Thêm header `Idempotency-Key` cho `POST /api/requests`. Gửi trùng key thì trả 200 kèm đơn cũ.
- Chấm công trả `event_id`. Bảng `attendance_events(idempotency_key)` và `requests(idempotency_key)`.

## Đầu ra mong muốn
1. **Kịch bản mới** trong `shapes.py`, cấu hình qua `config.env`:
   - `write_storm`: `WRITE_STORM_USERS=100`, không nghỉ, thời lượng `WRITE_STORM_DURATION=120`.
   - `write_ramp`: `WRITE_RAMP_STEPS=50,100,200,300,400,600,800`, mỗi bậc `WRITE_RAMP_STEP_SECONDS=60`.
2. **Tự dừng khi gãy** (phần chung, dùng cho cả `ramp` và `write_ramp`):
   - `STOP_ON_BREAK=1` (mặc định).
   - Cuối mỗi bậc, shape đọc `self.runner.stats` của cửa sổ hiện tại. Nếu p95 > `TARGET_P95_MS × BREAK_FACTOR` (mặc định 2) hoặc tỷ lệ lỗi > `BREAK_ERR_PCT` (mặc định 5) thì chạy thêm `BREAK_HOLD_SECONDS` (mặc định 30) để thu số liệu, rồi dừng.
   - Ghi bậc gãy vào log để `report.py` đọc.
3. **User ghi** trong `locustfile.py` (phần riêng của app):
   - `WriterUser` chỉ ghi: chấm công (trọng số 3) và tạo đơn OT có `Idempotency-Key` (trọng số 1). Mỗi lệnh dùng một UUID mới.
   - **Retry giống client thật**: khi gặp 503, 502, 504, lỗi kết nối hoặc timeout thì gửi lại **cùng key**, tối đa `RETRY_MAX=3` lần với backoff `RETRY_BACKOFF_MS`, tôn trọng `Retry-After`.
   - Thống kê tách riêng hai loại:
     - Lần gửi đầu bị lỗi: dùng `name` của lần đầu, tính vào tỷ lệ lỗi để số liệu trung thực.
     - Lần retry: đặt `name` dạng `... (retry)`.
   - Mỗi lệnh ghi ghi một dòng vào `<result_dir>/writes.jsonl` gồm `key`, `kind`, `final_status`, `attempts`, `id` trả về và thời điểm. Đường dẫn truyền qua env `RESULT_DIR`.
4. **Đối soát:**
   - `loadtest/app_reconcile.sql` (phần riêng của app, comment rõ là thay được) chứa câu lấy key đã ghi trong DB từ thời điểm bắt đầu test, gồm cả 2 bảng.
   - `run.sh`, sau khi Locust kết thúc:
     - Nếu kịch bản có `writes.jsonl`, chạy SQL qua `docker compose exec -T db psql` và xuất CSV vào thư mục kết quả.
     - Nếu `RECONCILE_RETRY=1` (mặc định), gửi lại một lần những key có trạng thái cuối là lỗi, bằng `reconcile.py --retry`, rồi xuất CSV lần nữa.
   - `loadtest/reconcile.py <result_dir>` (phần chung) ghi `reconcile.md` và thêm một mục vào `report.md`:
     - Tổng lệnh ghi đã gửi.
     - Số được xác nhận (2xx).
     - Số **đã xác nhận nhưng không có trong DB**. Con số này phải bằng 0, nếu khác 0 thì in đậm KHÔNG ĐẠT.
     - Số key bị trùng trong DB.
     - Số lỗi sau khi hết retry, và trong số đó bao nhiêu thật ra đã vào DB (commit nhưng client không nhận được trả lời).
     - Số liệu sau vòng retry đối soát.
5. **`run.sh` và `report.py`:**
   - Lấy mẫu cả container Locust runner (`*-locust-run-*`) và hiển thị là "locust (máy bắn tải)". Nếu CPU của nó ≥ 90% giới hạn thì cảnh báo: "Máy bắn tải đã kịch, số đo có thể thấp hơn khả năng thật của api".
   - Với `write_ramp`, báo cáo ghi rõ RPS ghi cao nhất mà vẫn đạt mục tiêu, và bậc gãy.

6. **Nhìn vào bên trong DB khi đo** (`run.sh` và `report.py`, đây là phần chung):
   - Trước khi chạy: `SELECT pg_stat_statements_reset()`. Nếu extension chưa có thì bỏ qua, không báo lỗi.
   - Trong lúc chạy, mỗi 2 giây lấy mẫu vào `pg_samples.jsonl`:
     - `wait_event_type`/`wait_event` của các session đang `active`, đếm theo nhóm.
     - Số connection theo `state`.
     - `xact_commit` và `xact_rollback` từ `pg_stat_database`, dùng để tính TPS.
   - Sau khi chạy: xuất top 15 câu SQL theo `total_exec_time` (calls, mean ms, total ms, rows, câu SQL cắt còn 120 ký tự) vào `pg_top_queries.csv`.
   - Các câu SQL theo dõi đặt trong `loadtest/db_probe.sql`, comment rõ là dùng được cho mọi app Postgres.
   - `report.md` thêm các mục:
     - "TPS của DB": biểu đồ thứ 4, `chart_db.png`, gồm TPS và số connection active theo thời gian.
     - "DB đang chờ gì": bảng wait event chiếm nhiều nhất. Nhiều `Lock` nghĩa là tranh chấp khóa, `LWLock/WALWrite` nghĩa là nghẽn ghi WAL, `Client` nghĩa là app chậm.
     - "Truy vấn tốn nhất".
   - Mục "Điểm nghẽn" kết hợp cả CPU container lẫn wait event để kết luận.
   - Ghi giá trị `RATE_LIMIT_ENABLED` của api vào `meta.json`, đọc bằng `docker compose exec api printenv`.

## Tiêu chí nghiệm thu
- [ ] `locust --list` qua cho mọi kịch bản. Kịch bản cũ giữ nguyên hành vi.
- [ ] Kiểm thử offline với dữ liệu giả: `reconcile.py` phát hiện đúng một key "đã xác nhận nhưng mất" mà mình cố ý chèn vào dữ liệu giả.
- [ ] Chạy thật `WRITE_STORM_DURATION=30 ./loadtest/run.sh write_storm`: có `writes.jsonl`, CSV đối soát, `reconcile.md`, và báo cáo có mục đối soát. Dán mục đó vào báo cáo.
- [ ] Chạy thật `WRITE_RAMP_STEP_SECONDS=20 ./loadtest/run.sh write_ramp`: tự dừng khi gãy (hoặc chạy hết các bậc), báo cáo ghi bậc gãy. Dán kết quả vào báo cáo.
- [ ] Shellcheck sạch. Code và comment bằng tiếng Anh, báo cáo bằng tiếng Việt.

## Không được đụng
- Mọi thứ ngoài `loadtest/`.
- Không chạy kịch bản dài quá 5 phút.
- Không chạy `pkill -f`. Không commit, không push, không bật profile `tunnel`.

---
## Kết quả và review của leader
