# TASK-007: README và docs hướng dẫn dùng chung

- **Giao cho:** coder (viết bản nháp), leader điền số liệu đo thật
- **Trạng thái:** Mới
- **Liên quan:** TASK-001 đến TASK-006

## Mục tiêu
Sếp sẽ dùng lại bộ docs này cho một dự án khác lớn hơn, chưa mô tả được. Vì vậy docs phải đủ kỹ để một người chưa biết gì cũng làm theo được, và tách rõ hai phần: cách dùng bản demo này, và cách áp bộ load test cùng các mẫu chống tranh chấp sang dự án khác. Toàn bộ viết bằng **tiếng Việt**. Lệnh, tên biến và code giữ nguyên tiếng Anh.

## Đầu vào
- Đọc code thật trong repo: `docker-compose.yml`, `.env.example`, `app/`, `scripts/seed.py`, `loadtest/` (kể cả `config.env`) và `tests/`. Không bịa lệnh hay biến không có trong code. Mọi lệnh trong docs phải chạy được.
- Chỗ nào cần số liệu đo thật thì để placeholder `<!-- SO_LIEU: ... -->`, leader sẽ điền.

## Đầu ra mong muốn
1. `README.md`:
   - Giới thiệu 3 câu.
   - Sơ đồ kiến trúc dạng mermaid hoặc ASCII: tunnel → api (3 worker) → db/redis ← worker.
   - Phần chạy nhanh, tối đa khoảng 6 lệnh.
   - Bảng kết quả load test (placeholder).
   - Cấu trúc thư mục.
   - Mục lục link tới `docs/`.
   - Ghi chú dữ liệu giả và quy tắc tính lương mẫu.
2. `docs/01-cai-dat-va-chay.md`:
   - Yêu cầu máy: Windows 11 + WSL2 + Docker Desktop. Bật WSL integration. Đặt repo trong filesystem WSL thay vì `/mnt/c`. `.wslconfig` để cấp RAM, có giải thích vì sao.
   - Clone, tạo `.env` (bảng giải thích **từng** biến trong `.env.example`), `up`, migrate (tự chạy khi api khởi động), seed (bảng tham số CLI).
   - Đăng nhập, mở web, xem log, dừng, xóa sạch dữ liệu (`down -v`, cảnh báo mất dữ liệu).
   - Chạy test.
   - Mục "Lỗi thường gặp": CRLF, cổng bận, Docker chưa chạy, api unhealthy, Host bị 400, quên `SEED_PASSWORD`.
3. `docs/02-load-test.md`:
   - Mục tiêu đo. Vì sao đo trong mạng Docker là số chính.
   - 5 kịch bản.
   - Lệnh chạy, cách ghi đè cấu hình bằng biến môi trường, bảng giải thích **từng** biến trong `loadtest/config.env`.
   - Cấu trúc thư mục kết quả.
   - Cách đọc `report.md` và 3 biểu đồ: p50 khác p95 thế nào, RPS là gì, CPU 200% nghĩa là gì, cách tìm điểm gãy. Dùng `loadtest/sample/` làm ví dụ và ghi rõ đó là dữ liệu giả.
   - Chạy qua Cloudflare Quick Tunnel: lệnh bật profile `tunnel`, cách lấy URL từ log, giới hạn 200 request đang xử lý, cảnh báo đây là public ra internet, cách tắt.
   - Mẹo để số đo đáng tin: cắm sạc, tắt app nặng, chạy 2–3 lần, seed lại trước khi đo.
4. `docs/03-ap-dung-cho-du-an-khac.md`, phần quan trọng nhất:
   - Checklist từng bước để áp `loadtest/` sang một dự án bất kỳ chạy bằng Docker Compose: copy những file nào, khai báo service `locust` trong compose ra sao, viết lại `locustfile.py` (kèm một **mẫu locustfile tối thiểu** chạy được với một API giả định có login và vài endpoint), chuẩn bị tài khoản test, đặt `deploy.resources.limits`, chọn endpoint để đo (endpoint dùng nhiều nhất và endpoint ghi).
   - Thứ tự đo `steady` → `burst` → `ramp` → `login_storm`.
   - Bảng "Triệu chứng → nguyên nhân hay gặp → cách xử lý". Gồm ít nhất: CPU api kịch trần, CPU db kịch trần, p95 cao nhưng CPU thấp (pool cạn, lock, blocking I/O), lỗi tăng vọt (timeout pool, max_connections), đăng nhập chậm (hash mật khẩu), N+1, việc nặng nằm trong request.
   - Cách tính số kết nối DB (`số worker × (pool_size + max_overflow)` cộng với worker nền, phải nhỏ hơn `max_connections`).
   - Gợi ý cho dự án không dùng Python/FastAPI: phần nào vẫn dùng được nguyên (run.sh, report.py, shapes.py, cách đọc), phần nào phải viết lại.
5. `docs/04-thiet-ke-dong-thoi.md`:
   - 6 quy tắc NV-1 đến NV-6. Với mỗi quy tắc: vấn đề tranh chấp, cách giải quyết bằng SQL (trích câu SQL thật từ code), file code, test tương ứng (đường dẫn và tên test), và vì sao phải đặt ràng buộc trong DB.
   - Mục "Mang sang dự án khác": các mẫu idempotency key, optimistic locking, conditional update, advisory lock, bảng chỉ insert.
6. `docs/06-sao-luu-khoi-phuc.md`:
   - Hai lớp sao lưu và khi nào dùng lớp nào. RPO và RTO thực tế.
   - Nơi lưu file. Quy tắc 3-2-1 (chép sang ổ hoặc cloud khác, vì `backups/` nằm cùng máy).
   - Từng lệnh: sao lưu ngay, liệt kê bản sao lưu, khôi phục từ dump, khôi phục về một thời điểm (PITR), chạy diễn tập.
   - Các tình huống sự cố mẫu, mỗi tình huống ghi "làm gì, theo thứ tự nào": lỡ xóa dữ liệu, hỏng volume, máy hỏng.
   - Ghi chú: tuyệt đối không tắt `fsync` hay `synchronous_commit` để lấy hiệu năng.
   - Thêm vào `docs/04-thiet-ke-dong-thoi.md` mục "Chống mất request": idempotency key cho mọi lệnh ghi, 503 kèm Retry-After, retry ở client, outbox, duyệt đơn idempotent, cách đối soát sau load test.
7. `docs/05-kich-ban-demo.md`:
   - Kịch bản buổi demo cho người hướng dẫn, khoảng 15 phút: trình bày gì theo thứ tự nào, lệnh nào, mở file nào, nói gì khi chỉ vào biểu đồ điểm nghẽn.
   - Câu hỏi thầy có thể hỏi kèm gợi ý trả lời. Ví dụ: vì sao không đạt khi đăng nhập ồ ạt, muốn chịu 1000 user thì làm gì, vì sao tách bảng nhạy cảm.

## Tiêu chí nghiệm thu
- [ ] Đủ 7 file, viết bằng tiếng Việt có dấu, câu ngắn rõ ràng. Không dùng emoji làm đề mục.
- [ ] Mọi lệnh, biến và đường dẫn trong docs đều có thật trong repo. Leader sẽ chạy thử ngẫu nhiên.
- [ ] Mẫu locustfile tối thiểu trong docs 03 qua `python -m py_compile` (chạy trong image locust).
- [ ] Không lộ mật khẩu thật trong `.env`. Docs chỉ ghi "đặt `SEED_PASSWORD` trong `.env`".

## Không được đụng
- Code trong `app/`, `loadtest/`, `tests/`, `scripts/`, và `team/`. Task này chỉ tạo `README.md` và `docs/`.
- Không chạy load test. Không commit, không push, không bật tunnel.

---
## Kết quả và review của leader
