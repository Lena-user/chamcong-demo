# TASK-004: Bộ load test dùng chung, bỏ hard-code

- **Giao cho:** coder
- **Trạng thái:** Đang làm
- **Liên quan:** TASK-002 (bộ load test hiện tại). Chạy song song với TASK-003 (coder khác đang làm `app/`).

## Mục tiêu
Sếp muốn người khác dùng lại được bộ load test này cho dự án khác. Cần tách phần chung (kịch bản tải, `run.sh`, `report.py`) khỏi phần riêng của app chấm công (các user/task Locust). Mọi tham số đưa ra file cấu hình, mặc định giữ đúng hành vi hiện tại.

## Đầu vào
- `loadtest/` hiện có: `locustfile.py`, `run.sh`, `report.py`, `Dockerfile`, `sample/`.
- Đang hard-code:
  - Dải mã nhân viên `E0014`–`E0300` và manager `E0004`–`E0013`.
  - Tham số của từng kịch bản (số user, tốc độ spawn, thời lượng, các bậc ramp, thời gian nghỉ).
  - Múi giờ +7.
  - Ngưỡng 500ms/1% trong `report.py`.
  - Danh sách service `api/db/worker/redis` cùng `DEFAULT_LIMITS` và `COLORS`.
  - Host `http://api:8000` và tên service `locust` trong `run.sh`.
- Stack đang chạy, nhưng mới có `POST /api/auth/login`, `GET /api/me` và `/health`. Các endpoint khác TASK-003 đang làm.

## Đầu ra mong muốn
1. **Tách file:**
   - `loadtest/shapes.py`: phần chung. Đọc cấu hình từ env và cung cấp LoadTestShape cho các kịch bản `steady`, `burst`, `ramp`, `login_storm` và `custom`. `custom` đọc `USERS`, `SPAWN_RATE`, `DURATION`.
   - `loadtest/locustfile.py`: chỉ còn phần riêng của app (user class và task), import shape từ `shapes.py`.
   - Đầu `locustfile.py` có comment ngắn: muốn dùng cho app khác thì viết lại file này, giữ nguyên `shapes.py`, `run.sh`, `report.py`.
2. **`loadtest/config.env`** (commit được, không chứa secret), mỗi biến có comment tiếng Anh:
   - `TARGET_HOST`.
   - `STEADY_USERS/SPAWN_RATE/DURATION`, `BURST_*`, `LOGIN_STORM_*`, `RAMP_STEPS=50,100,150,200,300`, `RAMP_STEP_SECONDS`, `RAMP_SPAWN_RATE`, `WAIT_MIN`, `WAIT_MAX`.
   - `USER_CODE_FORMAT=E{:04d}`, `EMPLOYEE_RANGE=14-300`, `MANAGER_RANGE=4-13`, `MANAGER_WEIGHT=5`, `LOADTEST_TZ=Asia/Ho_Chi_Minh` (dùng zoneinfo).
   - `TARGET_P95_MS=500`, `TARGET_ERR_PCT=1`, `BOTTLENECK_PCT=90`.
   - `LOCUST_SERVICE=locust`, `COMPOSE_PROFILE=loadtest`.
   - Mật khẩu vẫn lấy từ `.env` gốc (`SEED_PASSWORD`, đi vào container dưới tên `LOADTEST_PASSWORD`), không đặt trong `config.env`.
3. **`run.sh`:**
   - Đọc `config.env`. Biến môi trường đặt sẵn khi gọi lệnh được ưu tiên hơn file, ví dụ `STEADY_USERS=50 ./loadtest/run.sh steady`.
   - Chuyển mọi biến vào container bằng `-e`.
   - Hỗ trợ `custom`.
   - Ghi toàn bộ cấu hình đã dùng vào `meta.json`.
   - Cho phép đổi file cấu hình qua `LOADTEST_CONFIG=path`.
4. **`report.py`:**
   - Không còn danh sách service cứng. Tự nhận diện service từ tên container trong `docker_stats.jsonl`: bỏ tiền tố `<project>-` và hậu tố `-<n>`, đọc tên project từ `meta.json` nếu có.
   - Màu gán tự động theo một palette cố định, theo thứ tự service.
   - Giới hạn CPU/RAM lấy từ `meta.json`. Container không đặt giới hạn thì ghi "không giới hạn" và tính % theo số CPU của máy.
   - Ngưỡng đọc từ env hoặc từ `meta.json` (cấu hình đã dùng).
   - Đường kẻ giới hạn CPU vẽ cho service có CPU cao nhất, không cố định là `api`.
5. **`.gitignore` trong `loadtest/`** nếu cần (ví dụ cho `config.local.env`). Không sửa `.gitignore` gốc.

## Tiêu chí nghiệm thu
- [ ] Chạy với cấu hình mặc định thì hành vi giống TASK-002, `locust --list` qua cho cả 5 kịch bản.
- [ ] `RAMP_STEPS=10,20 RAMP_STEP_SECONDS=5` có tác dụng (kiểm bằng cách in shape hoặc chạy ngắn).
- [ ] `report.py loadtest/sample` vẫn ra đủ 3 PNG cùng `report.md`. Thêm một dòng service lạ (ví dụ `chamcong-web-1`) vào jsonl mẫu thì report vẫn chạy và vẽ được service đó.
- [ ] **Chạy thật end-to-end:** `LOGIN_STORM_DURATION=20 ./loadtest/run.sh login_storm` trên stack đang chạy. Có thư mục kết quả với đủ CSV, `docker_stats.jsonl`, `meta.json`, 3 PNG và `report.md`, và trong report có số liệu CPU của api/db. Dán phần "Mục tiêu so với thực tế" và "Điểm nghẽn" của report vào báo cáo.
- [ ] `bash -n` cùng `shellcheck` sạch. Code và comment bằng tiếng Anh.

## Không được đụng
- Mọi thứ ngoài `loadtest/`.
- Không chạy kịch bản nào dài hơn 30 giây, vì coder TASK-003 đang chạy test trên cùng máy.
- Không chạy `pkill -f`. Không commit, không push, không bật profile `tunnel`.

## Mục PROJECT.md cần đọc
Mục 9.

---
## Kết quả và review của leader

### Lần 1 (2026-10-26)
- Báo cáo của agent (tóm tắt): đã tách `shapes.py`/`config.env`, `report.py` tự nhận diện service. Chạy thật `login_storm` 20 giây được 105 request, trong đó 51 lỗi 500 do pool timeout. api chạm 236% CPU và 2043/2048 MiB RAM.
- Đánh giá: Đạt.
- Nhận xét: bộ đo đã làm đúng việc, tức là chỉ ra lỗi thật của app. Có hai nguyên nhân: login giữ connection trong lúc hash, và executor mặc định chạy khoảng 36 hash song song, mỗi hash tốn 64 MiB. Đã chuyển sang TASK-005.
- Bước tiếp theo: TASK-005, TASK-008.
