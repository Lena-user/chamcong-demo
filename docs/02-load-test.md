# 02. Load test

## 1. Mục tiêu đo

- Chứng minh hệ thống chịu khoảng 100 request đồng thời với p95 dưới 500 ms và tỷ lệ lỗi dưới 1%.
- Chỉ ra giới hạn thật: container nào chạm trần trước (api, db hay máy bắn tải), và ở bao nhiêu user thì hệ thống gãy.
- Với lệnh ghi, chứng minh không mất và không trùng: so sánh những gì client được báo thành công với những gì nằm trong DB (đối soát).

### Vì sao số đo trong mạng Docker là số chính

Mặc định Locust bắn thẳng vào `http://api:8000` bên trong mạng Docker của compose. Đây là số chính vì:

- Nó đo đúng ứng dụng, không lẫn độ trễ mạng nhà, đường truyền ra ngoài và giới hạn của Cloudflare.
- Cloudflare Quick Tunnel chỉ cho tối đa 200 request đang xử lý, vượt thì trả 429, và không có cam kết chất lượng dịch vụ. Số đo qua tunnel chỉ để tham khảo ("đi qua internet thật thì cảm giác thế nào").
- Kết quả lặp lại được: cùng máy, cùng cấu hình thì ra gần cùng số.

## 2. Các kịch bản

Chọn bằng tham số đầu của `run.sh` (hoặc biến `SCENARIO`). Có 7 kịch bản cố định cộng một kịch bản tùy chỉnh.

| Kịch bản | Làm gì | Dùng để |
|---|---|---|
| `steady` | 100 user, nghỉ ngẫu nhiên 1 đến 3 giây giữa các request, chạy 180 giây. Mỗi user đăng nhập một lần rồi chấm công, xem công, xem hồ sơ, tạo đơn OT. Quản lý thì duyệt đơn. | Tải bình thường. Kịch bản chính để đối chiếu mục tiêu. |
| `burst` | Như `steady` nhưng không nghỉ (`WAIT_*` bị bỏ qua), 100 user trong 120 giây. Luôn có khoảng 100 request đang xử lý cùng lúc. | Tải dồn dập, tìm độ trễ khi hàng đợi đầy. |
| `ramp` | Tăng dần 50, 100, 150, 200, 300 user, mỗi bậc 60 giây. Tự dừng khi một bậc "gãy". | Tìm điểm gãy. |
| `login_storm` | 100 user chỉ đăng nhập, liên tục, 60 giây. | Đo giới hạn của băm mật khẩu (argon2) trên số CPU hiện có. |
| `write_storm` | Chỉ ghi, không nghỉ: chấm công 3 phần, tạo đơn OT 1 phần. 100 user, 120 giây. Client thử lại như thật khi gặp 502, 503, 504 hoặc timeout, gửi lại **cùng** `Idempotency-Key`. Sau khi xong thì đối soát với DB. | Chứng minh ghi đúng, đủ, không mất. |
| `write_ramp` | Như `write_storm` nhưng tăng dần 50, 100, 200, 300, 400, 600, 800 user, tự dừng khi gãy. | Tìm điểm gãy của đường ghi. |
| `custom` | Tự đặt `USERS`, `SPAWN_RATE`, `DURATION`. | Thử nghiệm nhanh. |

Ghi chú: ở các kịch bản không phải `login_storm`, user chỉ đăng nhập một lần lúc bắt đầu. Ở `write_*`, việc đăng nhập nằm ngoài thống kê (hiện ở dòng riêng `login (not in totals)`), vì băm mật khẩu lúc khởi động sẽ làm p95 xấu giả và gây "gãy" giả.

## 3. Chạy

Chuẩn bị (một lần): stack đang chạy và đã seed (`docs/01-cai-dat-va-chay.md`), và **tắt rate limit**:

```bash
# trong .env:  RATE_LIMIT_ENABLED=false
docker compose up -d --no-deps api
```

Vì sao: một user test sẽ chạm trần 600 lệnh ghi mỗi phút và nhận 429, làm số đo sai.

Chạy một kịch bản:

```bash
./loadtest/run.sh steady
./loadtest/run.sh ramp
./loadtest/run.sh login_storm
./loadtest/run.sh write_storm
./loadtest/run.sh custom           # dùng USERS, SPAWN_RATE, DURATION
./loadtest/run.sh steady https://xxxx.trycloudflare.com   # bắn qua tunnel (mục 7)
```

`run.sh` làm lần lượt: nạp cấu hình, ghi `meta.json`, bật hai bộ lấy mẫu (docker stats và Postgres, mỗi 2 giây), chạy Locust headless trong container `locust`, dừng bộ lấy mẫu, lấy top query từ `pg_stat_statements`, đối soát (nếu là kịch bản ghi), rồi gọi `report.py` dựng `report.md` và biểu đồ. Cuối cùng in đường dẫn `Report: .../report.md`.

Mã thoát: `0` là bình thường, `3` là đối soát thất bại (có lệnh ghi đã báo thành công mà không có trong DB, hoặc một key bị trùng).

### Ghi đè cấu hình bằng biến môi trường

Quy tắc: **biến môi trường thắng file**. Mọi biến trong `loadtest/config.env` đều có thể ghi đè ngay trên dòng lệnh:

```bash
STEADY_USERS=50 STEADY_DURATION=60 ./loadtest/run.sh steady
USERS=250 SPAWN_RATE=25 DURATION=90 ./loadtest/run.sh custom
RAMP_STEPS=20,40,60 RAMP_STEP_SECONDS=30 ./loadtest/run.sh ramp
```

Dùng cả một file cấu hình khác: `LOADTEST_CONFIG=loadtest/config.local.env ./loadtest/run.sh steady` (tên `config.local.env` đã nằm trong `loadtest/.gitignore`). Mỗi lần chạy, mọi giá trị đã dùng được ghi vào `meta.json` của thư mục kết quả.

### Bảng biến trong `loadtest/config.env`

Địa chỉ:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `TARGET_HOST` | `http://api:8000` | Địa chỉ bắn tải. Mặc định ở trong mạng compose. Tham số thứ hai của `run.sh` ghi đè. |

Kịch bản `steady`:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `STEADY_USERS` | `100` | Số user đồng thời. |
| `STEADY_SPAWN_RATE` | `20` | Số user khởi động mỗi giây. |
| `STEADY_DURATION` | `180` | Thời gian chạy (giây). |

Kịch bản `burst`:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `BURST_USERS` | `100` | Số user đồng thời. |
| `BURST_SPAWN_RATE` | `100` | Số user khởi động mỗi giây. |
| `BURST_DURATION` | `120` | Thời gian chạy (giây). |

Kịch bản `login_storm`:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `LOGIN_STORM_USERS` | `100` | Số user đồng thời. |
| `LOGIN_STORM_SPAWN_RATE` | `100` | Số user khởi động mỗi giây. |
| `LOGIN_STORM_DURATION` | `60` | Thời gian chạy (giây). |

Kịch bản `ramp`:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `RAMP_STEPS` | `50,100,150,200,300` | Các bậc số user, cách nhau bằng dấu phẩy. |
| `RAMP_STEP_SECONDS` | `60` | Thời gian mỗi bậc (giây). |
| `RAMP_SPAWN_RATE` | `20` | Số user khởi động mỗi giây khi chuyển bậc. |

Kịch bản `write_storm`:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `WRITE_STORM_USERS` | `100` | Số user đồng thời. |
| `WRITE_STORM_SPAWN_RATE` | `100` | Số user khởi động mỗi giây. |
| `WRITE_STORM_DURATION` | `120` | Thời gian chạy (giây). |

Kịch bản `write_ramp`:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `WRITE_RAMP_STEPS` | `50,100,200,300,400,600,800` | Các bậc số user. |
| `WRITE_RAMP_STEP_SECONDS` | `60` | Thời gian mỗi bậc (giây). |
| `WRITE_RAMP_SPAWN_RATE` | `50` | Số user khởi động mỗi giây khi chuyển bậc. |

Kịch bản `custom`:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `USERS` | `100` | Số user đồng thời. |
| `SPAWN_RATE` | `20` | Số user khởi động mỗi giây. |
| `DURATION` | `60` | Thời gian chạy (giây). |

Thời gian nghỉ và tài khoản:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `WAIT_MIN` | `1` | Nghỉ tối thiểu giữa hai request của một user (giây). |
| `WAIT_MAX` | `3` | Nghỉ tối đa (giây). Chọn ngẫu nhiên trong khoảng. |
| `USER_CODE_FORMAT` | `E{:04d}` | Định dạng mã đăng nhập, `{:04d}` được thay bằng số. |
| `EMPLOYEE_RANGE` | `14-300` | Khoảng số (gồm hai đầu) của tài khoản nhân viên thường. |
| `MANAGER_RANGE` | `4-13` | Khoảng số của tài khoản quản lý. |
| `MANAGER_WEIGHT` | `5` | Phần trăm user mô phỏng là quản lý, còn lại là nhân viên. |
| `LOADTEST_TZ` | `Asia/Ho_Chi_Minh` | Múi giờ khi test tự dựng ngày giờ. |

Ngưỡng đạt và nghẽn:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `TARGET_P95_MS` | `500` | Mục tiêu p95 (ms). |
| `TARGET_ERR_PCT` | `1` | Mục tiêu tỷ lệ lỗi (%). |
| `BOTTLENECK_PCT` | `90` | Container vượt mức này (% giới hạn CPU hoặc RAM) bị đánh dấu là điểm nghẽn. |

Dừng khi gãy (`ramp`, `write_ramp`):

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `STOP_ON_BREAK` | `1` | `1` là dừng ngay khi một bậc gãy, `0` là chạy hết mọi bậc. |
| `BREAK_FACTOR` | `2` | Bậc gãy khi p95 vượt `TARGET_P95_MS x BREAK_FACTOR` (mặc định 1000 ms). |
| `BREAK_ERR_PCT` | `5` | ...hoặc tỷ lệ lỗi của bậc vượt mức này (%). |
| `BREAK_HOLD_SECONDS` | `30` | Sau khi gãy, giữ tải thêm N giây để thu số liệu rồi dừng. |

Thử lại như client thật (kịch bản ghi):

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `RETRY_MAX` | `3` | Số lần thử lại tối đa mỗi lệnh ghi. |
| `RETRY_BACKOFF_MS` | `200` | Chờ lần đầu (ms), nhân đôi mỗi lần, có nhiễu ngẫu nhiên. |
| `RETRY_AFTER_CAP_S` | `10` | Tối đa bao nhiêu giây user chịu nghe theo `Retry-After`. |
| `REQUEST_TIMEOUT_S` | `30` | Timeout mỗi request (giây). |
| `RECONCILE_RETRY` | `1` | Sau kịch bản ghi, gửi lại một lần các key cuối cùng vẫn lỗi rồi kiểm tra DB lần nữa (`1` là có). |

Hạ tầng chạy Locust:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `LOCUST_SERVICE` | `locust` | Tên service compose chạy Locust. |
| `COMPOSE_PROFILE` | `loadtest` | Profile compose bật service đó. |
| `LOCUST_PROCESSES` | `2` | Số process Locust (tham số `--processes`). Mỗi process dùng tối đa khoảng 1 CPU. Giữ nhỏ hơn hoặc bằng `LOCUST_CPUS` trong `.env` gốc. Mỗi user được gán một tài khoản riêng, không trùng giữa các process (`shapes.pick_code`). |

Mật khẩu **không** nằm ở đây: nó đi từ `SEED_PASSWORD` trong `.env`, qua compose thành `LOADTEST_PASSWORD`.

## 4. Thư mục kết quả

Mỗi lần chạy tạo `loadtest/results/<YYYYMMDD-HHMMSS>-<kịch bản>/` (đã gitignore, trừ `.gitkeep`):

| File | Nội dung |
|---|---|
| `report.md` | Báo cáo tiếng Việt: so với mục tiêu, từng endpoint, CPU/RAM, điểm gãy, đối soát. Đọc file này trước. |
| `chart_latency.png`, `chart_rps.png`, `chart_cpu.png` | Ba biểu đồ chính (mục 5). Lần chạy thật còn có `chart_db.png` về hoạt động của Postgres. |
| `meta.json` | Kịch bản, địa chỉ, giờ, commit git, giới hạn tài nguyên từng container, giá trị `RATE_LIMIT_ENABLED` của api, mọi cấu hình đã dùng. |
| `locust_stats.csv`, `locust_stats_history.csv`, `locust.html` | Số liệu thô và báo cáo gốc của Locust. |
| `locust_failures.csv`, `locust_exceptions.csv` | Chi tiết lỗi. |
| `docker_stats.jsonl`, `runner_limits.json` | Mẫu CPU/RAM mỗi 2 giây, và giới hạn của máy bắn tải. |
| `pg_samples.jsonl`, `pg_top_queries.csv` | Mẫu Postgres (chờ, TPS) và 15 câu lệnh tốn thời gian nhất. |
| `break.json` | Chỉ có ở `ramp` và `write_ramp` khi hệ thống gãy: bậc nào, vì sao. |
| `writes.jsonl`, `writes_retry.jsonl`, `db_keys.csv`, `reconcile.md` | Chỉ có ở kịch bản ghi: nhật ký từng lệnh ghi, các key trong DB, kết quả đối soát. |

Tạo bảng tổng hợp lần chạy mới nhất của từng kịch bản: `docker compose --profile loadtest run --rm -T --no-deps --entrypoint python locust report.py --summary` (ghi `loadtest/results/SUMMARY.md`).

## 5. Cách đọc `report.md` và biểu đồ

Ví dụ trong tài liệu này lấy từ `loadtest/sample/`. **Đó là dữ liệu giả**, do `loadtest/sample/make_sample.py` sinh ra để minh họa định dạng, không phải số đo của hệ thống. Hãy mở `loadtest/sample/report.md` và ba ảnh `chart_*.png` để đối chiếu khi đọc.

### Các khái niệm

- **p50 (trung vị)**: một nửa số request nhanh hơn mức này. Cho biết trải nghiệm "bình thường".
- **p95**: 95% số request nhanh hơn mức này, 5% chậm hơn. Cho biết trải nghiệm của nhóm xui xẻo. Một hệ thống có p50 40 ms nhưng p95 2.000 ms nghĩa là phần lớn nhanh, nhưng cứ 20 request có 1 bị xếp hàng lâu, thường do tắc nghẽn (hàng đợi, khóa, pool cạn). **Khoảng cách p50 và p95 càng rộng thì càng có tắc nghẽn.** Mục tiêu của dự án đặt trên p95, vì p50 đẹp có thể che giấu hàng đợi.
- **p99**: như trên với 1% chậm nhất. Có trong bảng từng endpoint, hữu ích để thấy "đuôi" dài.
- **RPS (request mỗi giây)**: số request xử lý xong mỗi giây, là thông lượng. Khi tăng user mà RPS không tăng thêm nữa (đi ngang) thì hệ thống đã bão hòa: thêm user chỉ làm thêm hàng đợi, nghĩa là độ trễ tăng.
- **CPU 200%**: Docker tính CPU theo "mỗi nhân là 100%". Container api có giới hạn 2 CPU nên trần của nó là 200%. Thấy `api 204% / 200%` nghĩa là api đã dùng hết hai nhân được cấp, đó là điểm nghẽn. Container db giới hạn 1 CPU nên trần là 100%.

### Phần đầu `report.md`

Bảng "Mục tiêu so với thực tế" so p95 tổng và tỷ lệ lỗi với `TARGET_P95_MS`, `TARGET_ERR_PCT`. Chỉ khi cả hai đều đạt thì "Kết luận" mới là ĐẠT. Trong ví dụ mẫu: p95 230 ms (đạt) nhưng lỗi 1,01% (không đạt) nên kết luận là KHÔNG ĐẠT.

Bảng "Theo từng endpoint" cho biết endpoint nào chậm hoặc lỗi. Dòng `POST /api/auth/login` thường nặng nhất vì băm mật khẩu.

Mục "Kết quả hợp lệ được đếm riêng" liệt kê các tình huống đúng nghiệp vụ nhưng hiếm, ví dụ `approve 409 (contention)`: hai quản lý cùng duyệt một đơn, một người nhận 409. Đó không phải lỗi và không tính vào p95.

### Mục "CPU và RAM cao nhất" và dòng "Điểm nghẽn"

Bảng liệt kê đỉnh CPU, RAM của từng container so với giới hạn. Container nào vượt `BOTTLENECK_PCT` (90%) giới hạn bị ghi vào dòng **Điểm nghẽn**. Ví dụ mẫu: `api` chạm giới hạn CPU (204% / 200%) còn `db` mới 42% / 100%. Kết luận: nghẽn ở tầng ứng dụng, thêm CPU cho api hoặc giảm việc tốn CPU sẽ có lợi, tăng sức db thì chưa cần.

Cảnh báo quan trọng: nếu dòng `locust (máy bắn tải)` chạm giới hạn thì số đo không còn tin được, vì máy bắn tải là thứ đang nghẽn chứ chưa chắc hệ thống.

### Bảng "Điểm gãy" (chỉ ở `ramp`, `write_ramp`)

Mỗi hàng là một bậc số user với p95, % lỗi và RPS. Cách tìm điểm gãy:

1. Nhìn p95: bậc nào p95 vượt mục tiêu 500 ms lần đầu, bậc nào vượt 1.000 ms (`BREAK_FACTOR`).
2. Nhìn RPS: bậc nào RPS thôi tăng tỷ lệ với số user. Trong ví dụ mẫu RPS tăng đều đến 300 user (vì là số giả), nhưng p95 nhảy từ 404 ms lên 756 ms lên 2.005 ms, còn lỗi bắt đầu xuất hiện từ bậc 200.
3. Nhìn lỗi: bậc nào lỗi vượt `BREAK_ERR_PCT` (5%).

Dòng "Điểm gãy" ghi bậc đầu tiên vi phạm. Trong ví dụ mẫu là 200 user (p95 756 ms, lỗi 0,40%). Dung lượng an toàn nằm ở bậc ngay trước đó.

### Ba biểu đồ

- `chart_latency.png`: p50 (xanh), p95 (đỏ), đường đứt nét là mục tiêu p95, và vùng nền là số user theo thời gian. Đường p95 tách xa p50 là dấu hiệu hàng đợi. Với `ramp`, thấy rõ p95 leo theo từng bậc user.
- `chart_rps.png`: request mỗi giây (xanh) và lỗi mỗi giây (đỏ) theo thời gian. Đường RPS đi ngang trong khi số user còn tăng là đã bão hòa, đường lỗi nhô lên là bắt đầu rơi request.
- `chart_cpu.png`: CPU từng container so với giới hạn của nó. Container nào chạm trần là nút cổ chai. Đối chiếu thời điểm chạm trần với thời điểm p95 tăng trên biểu đồ độ trễ để chắc chắn đó là nguyên nhân.

## 6. Đối soát lệnh ghi

Với `write_storm` và `write_ramp`, mỗi lệnh ghi có một UUID. Locust ghi từng lệnh vào `writes.jsonl` (key, trạng thái cuối, số lần thử). Sau khi chạy, `run.sh` xuất mọi key có trong DB từ lúc bắt đầu (`loadtest/app_reconcile.sql`) và `reconcile.py` so sánh:

- Lệnh client được báo thành công (200 hoặc 201) mà không có trong DB: **mất**. Phải bằng 0.
- Một key xuất hiện hai lần trong DB: **trùng**. Phải bằng 0.
- Lệnh cuối cùng vẫn lỗi (ví dụ 503): được gửi lại một lần với cùng key (`RECONCILE_RETRY=1`), vì client thật cũng sẽ thử lại. Gửi lại không được tạo bản ghi trùng.

Kết quả ở `reconcile.md` và một mục trong `report.md`.

Lưu ý: không seed lại hoặc xóa dữ liệu trong lúc đang chạy kịch bản ghi, nếu không đối soát sẽ báo "mất" giả. Bước này đã thật sự xảy ra một lần khi phát triển, và cũng là bằng chứng rằng đối soát bắt được sự thiếu hụt.

## 7. Chạy qua Cloudflare Quick Tunnel

Cảnh báo: tunnel đưa API **ra internet công khai**, ai có URL cũng truy cập được (và đăng nhập được nếu biết mã và mật khẩu demo). Chỉ bật khi cần demo hoặc đo, dữ liệu phải là dữ liệu giả, và tắt ngay sau khi xong.

Bật (service `tunnel` nằm trong profile `tunnel`, nên `up` thường không bật nó):

```bash
docker compose --profile tunnel up -d tunnel
```

Lấy URL từ log (dạng `https://<ba-từ-ngẫu-nhiên>.trycloudflare.com`):

```bash
docker compose --profile tunnel logs tunnel | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | head -n 1
```

URL đổi mỗi lần bật. Kiểm tra: `curl -s https://<url>/health`.

Bắn tải qua tunnel (số chỉ để tham khảo):

```bash
./loadtest/run.sh steady https://<url>
```

Giới hạn của Quick Tunnel (theo tài liệu Cloudflare): tối đa 200 request đang xử lý cùng lúc, vượt thì nhận 429; không hỗ trợ Server-Sent Events; không có SLA; chỉ dùng để thử nghiệm. Nguồn: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/. Vì vậy đừng dùng kịch bản trên 200 user qua tunnel, và đừng coi 429 ở đó là lỗi của ứng dụng.

Nếu bắn tải qua tunnel, nhớ `ALLOWED_HOSTS` đã có `*.trycloudflare.com` (mặc định có sẵn).

Khi demo công khai thật sự (người khác dùng web qua tunnel), **bật lại rate limit**: đặt `RATE_LIMIT_ENABLED=true` trong `.env`, rồi `docker compose up -d --no-deps api`. Lúc load test thì ngược lại, đặt `false`.

Tắt tunnel:

```bash
docker compose --profile tunnel stop tunnel
docker compose --profile tunnel rm -f tunnel
```

## 8. Mẹo để số đo đáng tin

- Cắm sạc laptop và chọn chế độ hiệu năng cao. Pin yếu hoặc tiết kiệm điện làm CPU giảm xung, số đo dao động.
- Tắt các ứng dụng nặng (trình duyệt nhiều tab, game, build, máy ảo) trước khi đo.
- Đừng chạy hai thứ cùng lúc: một load test khác, hay tester đang chạy test trên cùng stack.
- Chạy 2 đến 3 lần mỗi kịch bản và lấy số ở giữa. Lần đầu thường chậm hơn vì cache còn nguội.
- Seed lại trước khi đo (`docker compose exec api python -m scripts.seed`) để dữ liệu ở trạng thái giống nhau. Riêng `steady` và `burst`, các quản lý duyệt dần đơn chờ, nên sau nhiều lần chạy số đơn pending giảm và bài đo thay đổi.
- Kiểm tra `meta.json`: `rate_limit_enabled` phải là `false`, `host_cpus` đủ cho tổng giới hạn CPU, và dòng `locust` trong bảng CPU không được chạm trần.
- Với số cuối dùng để báo cáo: ghi lại commit git và cấu hình (đã có trong `meta.json`).
