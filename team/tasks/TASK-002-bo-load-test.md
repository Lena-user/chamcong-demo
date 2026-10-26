# TASK-002: Bộ load test (Locust, run.sh, report.py)

- **Giao cho:** coder
- **Trạng thái:** Đang làm
- **Liên quan:** PROJECT.md mục 6, 7, 9. Chạy song song với TASK-001 (một coder khác đang dựng app).

## Mục tiêu
Làm bộ load test dùng lại được. Một lệnh chạy một kịch bản, vừa thu số liệu Locust vừa thu CPU/RAM của từng container, rồi ra báo cáo markdown kèm biểu đồ. Báo cáo phải cho thấy có đạt mục tiêu hay không và điểm nghẽn nằm ở đâu.

## Đầu vào
- Hợp đồng API: PROJECT.md mục 6. Tài khoản seed: mục 7. Kịch bản: mục 9.
- App chưa có, vì TASK-001 và TASK-003 đang làm song song. Viết theo hợp đồng API, không phụ thuộc vào code trong `app/`.
- Service `locust` trong compose do TASK-001 khai báo như sau. Đừng sửa compose:
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

## Đầu ra mong muốn
Mọi file nằm trong `loadtest/`.

1. `Dockerfile`: `FROM locustio/locust:<phiên bản 2.x cụ thể>`, cài thêm pandas và matplotlib với phiên bản ghim.

2. `locustfile.py`, dùng `FastHttpUser`.
   - Chọn kịch bản bằng env `SCENARIO`. Chỉ khai báo LoadTestShape tương ứng: một cách là định nghĩa class shape có điều kiện theo SCENARIO.
   - Kịch bản chạy đúng mục 9:

     | SCENARIO | User | Nghỉ giữa request | Thời lượng |
     |---|---|---|---|
     | `steady` | 100 | 1–3s | 3 phút (gồm ramp 20 user/s) |
     | `burst` | 100 | 0s | 2 phút |
     | `ramp` | 50 → 100 → 150 → 200 → 300 | 1–3s | mỗi bậc 60s |
     | `login_storm` | 100 | 0s, chỉ gọi login | 1 phút |

   - Mỗi user lấy một mã nhân viên riêng, xoay vòng trong dải `E0014`–`E0300`. Khoảng 5% user là manager, dùng dải `E0004`–`E0013`. Đăng nhập một lần trong `on_start`.
   - Task của employee (trọng số gợi ý):
     - `POST /api/attendance/check` với UUID mới (3).
     - Gửi lại đúng key cũ, coi 200 có `duplicate=true` là đúng (1).
     - `GET /api/attendance/me?size=20` (5).
     - `GET /api/me` (2).
     - `GET /api/requests/me` (2).
     - `POST /api/requests` loại `ot` cho hôm nay (1).
   - Task của manager:
     - `GET /api/requests?status=pending&size=20`.
     - Duyệt một đơn trong số đó. 409 là kết quả hợp lệ khi tranh chấp: dùng `catch_response` đánh dấu success, nhưng đếm riêng.
   - Đặt `name=` cho request có path động (ví dụ `/api/requests/[id]/approve`) để thống kê gộp.

3. `run.sh <scenario> [host]` (bash, `set -euo pipefail`, chạy từ gốc repo hoặc bất kỳ đâu).
   - host mặc định là `http://api:8000`. Nếu truyền URL trycloudflare thì Locust bắn qua tunnel.
   - Tạo thư mục `loadtest/results/<YYYYmmdd-HHMMSS>-<scenario>/`.
   - Chạy `docker compose --profile loadtest run --rm -e SCENARIO=... locust` ở chế độ headless, có `--csv <dir>/locust --csv-full-history --html <dir>/locust.html`. Riêng `steady` và `burst` mà không dùng shape thì truyền `-u -r -t` tương ứng.
   - Trong lúc chạy, lấy mẫu `docker stats --no-stream --format '{{json .}}'` mỗi 2 giây vào `<dir>/docker_stats.jsonl`. Chỉ lấy các container của project `chamcong`. Dừng khi Locust kết thúc, kể cả khi Locust lỗi (dùng `trap`).
   - Cuối cùng gọi `report.py` trong container locust.
   - Ghi `<dir>/meta.json` gồm scenario, host, thời điểm, giới hạn CPU/RAM đọc từ `docker inspect` và commit git hiện tại.

4. `report.py <result_dir>` và `report.py --summary` (đọc mọi thư mục trong `results/` để ghi `results/SUMMARY.md`, mỗi kịch bản lấy lần chạy mới nhất).
   - Vẽ 3 biểu đồ PNG:
     - Thời gian phản hồi p50/p95 theo thời gian, chồng với số user.
     - RPS và lỗi/giây theo thời gian.
     - CPU% của từng container theo thời gian (`api`, `db`, `worker`, `redis`; CPU 200% nghĩa là đã kịch 2 CPU).
   - Ghi `report.md` bằng **tiếng Việt**, gồm:
     - Bảng mục tiêu so với thực tế (p95 < 500ms, lỗi < 1%), có kết luận ĐẠT/KHÔNG ĐẠT.
     - Bảng theo từng endpoint: số request, p50, p95, p99, % lỗi.
     - CPU và RAM cao nhất của từng container.
     - Dòng "Điểm nghẽn": container nào chạm gần giới hạn CPU (≥ 90% giới hạn) hoặc RAM.
     - Với `ramp`: mức user đầu tiên mà p95 vượt 500ms hoặc lỗi vượt 1%, tính theo từng bậc từ `stats_history`. Đó là "điểm gãy".
   - Biểu đồ dùng matplotlib backend `Agg`, nhãn tiếng Việt có dấu (font DejaVu Sans là được), nhìn rõ ở khổ 1200×600.

5. `results/.gitkeep`.

## Tiêu chí nghiệm thu
- [ ] `docker compose --profile loadtest build locust` thành công. Nếu compose chưa có thì `docker build loadtest/`.
- [ ] `python -m py_compile` qua với `locustfile.py` và `report.py`, chạy trong image locust.
- [ ] Trong image, chạy `locust -f locustfile.py --list` với từng giá trị SCENARIO đều không lỗi.
- [ ] Tạo một bộ dữ liệu mẫu giả (CSV giống format Locust và `docker_stats.jsonl` giả) trong `loadtest/sample/`. `report.py loadtest/sample` sinh đủ 3 PNG và `report.md` đúng định dạng.
- [ ] `bash -n run.sh` không lỗi, `shellcheck` sạch nếu có sẵn.
- [ ] Code và comment viết bằng tiếng Anh. Nội dung `report.md` bằng tiếng Việt.

## Không được đụng
- Mọi thứ ngoài `loadtest/`: `app/`, `docker-compose.yml`, `team/` và `docs/` là của người khác.
- Không chạy `pkill -f`. Không commit, không push.

## Mục PROJECT.md cần đọc
Mục 6, 7, 9.

---
## Kết quả và review của leader

### Lần 1 (2026-10-26)
- Báo cáo của agent (tóm tắt): bộ load test xong (locust 2.32.5, 4 kịch bản đều dùng LoadTestShape, run.sh có lấy mẫu docker stats, report.py ra 3 PNG cùng report.md/SUMMARY.md). Chạy đúng trên dữ liệu mẫu, shellcheck sạch.
- Đánh giá: Đạt (phần offline). Phần chạy thật sẽ kiểm ở TASK-005.
- Nhận xét: 409 lúc duyệt đơn ghi thành dòng EVENT riêng, không làm lệch p95, hợp lý. Đơn OT gửi `start_at`/`end_at` dạng ISO có +07:00, đã báo cho TASK-003. Giữ `loadtest/sample/` để docs làm ví dụ, ghi rõ đây là dữ liệu giả.
- Bước tiếp theo: chạy thật sau TASK-003.
