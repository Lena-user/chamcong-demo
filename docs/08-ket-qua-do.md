# Kết quả đo tải và nhật ký tìm điểm nghẽn

Đo rạng sáng 27/10/2026 (giờ Việt Nam) trên laptop của người làm demo. Báo cáo đầy đủ của từng lượt (bảng theo endpoint, CPU/RAM, DB đang chờ gì, truy vấn tốn nhất, biểu đồ) nằm trong [`docs/ket-qua/`](ket-qua/).

## 1. Cấu hình lúc đo

| Mục | Giá trị |
|---|---|
| Máy | Windows 11 + WSL2, 8 luồng CPU, 7,6 GB RAM cấp cho WSL, Docker 29.6 |
| api | 2 CPU / 2 GB, uvicorn 3 worker, pool 10 + 5 mỗi worker |
| db | PostgreSQL 16, 1 CPU / 2 GB, `fsync` và `synchronous_commit` giữ mặc định (bật) |
| Máy bắn tải | Locust 2.32.5, 2 process, giới hạn 2 CPU, bắn thẳng `http://api:8000` trong mạng Docker |
| Dữ liệu | Seed lại trước mỗi lượt: 300 nhân viên, khoảng 45 nghìn lượt chấm công |
| Cấu hình app | `RATE_LIMIT_ENABLED=false`, `DB_POOL_PRE_PING=true` (đúng spec), argon2id ở mức tối thiểu của OWASP |
| Mục tiêu | p95 dưới 500 ms, lỗi dưới 1% |

## 2. Kết quả chính thức

| Kịch bản | Tải | RPS | p95 | Lỗi | Kết quả | Báo cáo |
|---|---|---|---|---|---|---|
| `steady` | 100 user, nghỉ 1–3 giây | 52 | 16 ms | 0% | **ĐẠT** | [steady](ket-qua/steady/report.md) |
| `ramp` | 50 → 300 user, có nghỉ | 155 ở mức 300 user | 39 ms ở mức 300 user | 0% | **ĐẠT**, chưa gãy | [ramp](ket-qua/ramp/report.md) |
| `ramp` kéo dài | 100 → 1000 user, có nghỉ | 176 ở mức 400 user | 170 ms ở mức 300 user, 1.700 ms ở mức 400 user | 0% tới 400 user, 1,6% (503) ở 600 user | Gãy ở **400 user** | [ramp-den-1000-user](ket-qua/ramp-den-1000-user/report.md) |
| `burst` | 100 request liên tục cùng lúc, đọc lẫn ghi | 215 | 1.300 ms | 0% | KHÔNG ĐẠT | [burst](ket-qua/burst/report.md) |
| `write_storm` | 100 lệnh ghi liên tục cùng lúc | 236 | 1.000 ms | 0% | KHÔNG ĐẠT | [write_storm](ket-qua/write_storm/report.md) |
| `write_ramp` | 50 → 100 → 200 lệnh ghi cùng lúc | 229 ở 50 user | 390 ms ở 50 user, 1.000 ms ở 100 user | 0% | Đạt tới 50 user, gãy ở **100 user** | [write_ramp](ket-qua/write_ramp/report.md) |
| `login_storm` | 100 người đăng nhập liên tục | 29 lần/giây | 5.100 ms | 0% | KHÔNG ĐẠT (có chủ đích, xem mục 4) | [login_storm](ket-qua/login_storm/report.md) |

## 3. Trả lời câu hỏi của buổi demo

1. **Có chịu được 100 người dùng đồng thời không?** Có. Với 100 người dùng thật (mỗi người nghỉ 1–3 giây giữa hai thao tác), p95 chỉ 16 ms và không có lỗi.
2. **Chịu được tối đa bao nhiêu người dùng thật?** Tới 300 người dùng (khoảng 150 request/giây) vẫn đạt mục tiêu, p95 170 ms. Gãy ở 400 người dùng (p95 1,7 giây). Ở 600 người dùng, hệ thống bắt đầu từ chối 1,6% request bằng mã 503 kèm `Retry-After`: từ chối có kiểm soát, không có lỗi 500 và không mất dữ liệu.
3. **100 request ghi bắn dồn cùng một lúc thì sao?** Không đạt p95 dưới 500 ms. Lý do nằm ở công suất chứ không phải lỗi:
   - Hệ thống ghi được tối đa khoảng 230 lệnh/giây với api 2 CPU và db 1 CPU.
   - Theo định luật Little, luôn có 100 request đang chờ mà mỗi giây chỉ xử lý được 230 request, thì mỗi request phải chờ trung bình 100 / 230 ≈ 0,43 giây. Vì vậy p95 khoảng 1 giây.
   - Với 50 lệnh ghi cùng lúc thì đạt (p95 390 ms).
4. **Ghi có đúng và đủ không?** Đúng và đủ. Đối soát 74.467 lệnh ghi (46.119 ở `write_storm`, 28.348 ở `write_ramp`): mọi lệnh client được báo thành công đều có trong DB, **0 mất, 0 trùng**.
   Kiểm tra thêm sau khi bổ sung bước kiểm tra toàn vẹn (lượt `write_storm` 100 user, 30.865 lệnh ghi):
   - **0 ghi nhầm người**.
   - Bảng công theo ngày tính lại từ dữ liệu chấm thô khớp 100%, trên cả 22.804 cặp nhân viên-ngày của toàn database.
   - Dữ liệu chấm thô chưa từng bị sửa hay xóa.
   - Mọi luật của đơn đều đúng.

## 4. Giới hạn nằm ở đâu

Ba nguồn số liệu cùng chỉ về một chỗ: CPU từng container (`docker stats`), DB đang chờ gì (`pg_stat_activity`), và truy vấn tốn nhất (`pg_stat_statements`).

| Nút thắt | Bằng chứng | Giải thích |
|---|---|---|
| **CPU của api (thứ nhất)** | 206–216% trên giới hạn 200% ở mọi kịch bản nặng | Mỗi lệnh ghi tốn khoảng 9 ms CPU của Python (2 CPU chia 230 request/giây): FastAPI, Pydantic, SQLAlchemy ORM, JWT, log JSON |
| **CPU của db và việc ghi WAL (thứ hai)** | 96–104% trên giới hạn 100%. 70–80% session đang chạy phải chờ `WALWrite`/`WALSync` ở kịch bản ghi | Mỗi commit phải chờ WAL được ghi xuống đĩa rồi mới báo thành công. Đây là cái giá của "không mất request", và đó là chủ ý |
| **Đăng nhập** | `login_storm`: api 210% CPU, db chỉ 29% | argon2id cố ý tốn CPU. Mỗi lần xác minh mất 38 ms, nên 2 CPU làm được khoảng 30–50 lần/giây |

**Thí nghiệm tăng CPU** (api 3 CPU, db 2 CPU, kịch bản `write_ramp`, xem [write_ramp-api3-db2](ket-qua/write_ramp-api3-db2/report.md)):
- 100 lệnh ghi cùng lúc đạt 281 request/giây (+20%), p95 830 ms.
- api vẫn kịch CPU (311% trên 300%). db dùng 159% trên 200% và vẫn chờ WAL 71%.
- Kết luận: muốn 100 lệnh ghi dồn cùng lúc đạt p95 dưới 500 ms thì cần khoảng gấp đôi công suất hiện tại. Cách hợp lý là chạy nhiều container api (scale ngang) và đặt WAL trên đĩa nhanh hơn. Thêm CPU cho DB không phải bước đầu tiên.

## 5. Nhật ký tìm và sửa

Mỗi dòng là một vấn đề mà phép đo tìm ra, kèm bằng chứng, cách sửa và kết quả sau khi sửa.

| # | Phát hiện | Bằng chứng | Cách sửa | Sau khi sửa |
|---|---|---|---|---|
| 1 | Đăng nhập giữ kết nối DB trong lúc băm mật khẩu, và có khoảng 36 lần băm chạy song song (executor mặc định) | `login_storm`: 48% lỗi 500 do `QueuePool limit ... reached`, RAM api 2.043/2.048 MiB | Kết thúc transaction trước khi băm. Executor riêng, mỗi process tối đa 2 lần băm (`HASH_CONCURRENCY`). Hàng đợi quá dài thì trả 503 | 0 lỗi, RAM 624 MiB |
| 2 | Tham số argon2 mặc định tốn 150 ms mỗi lần xác minh | Đo trong container | Dùng argon2id mức tối thiểu của OWASP (19 MiB, t=2, p=1) | 38 ms mỗi lần. `login_storm` p95 giảm từ khoảng 27 giây xuống 5,1 giây, từ 3,8 lên 28,6 lần đăng nhập/giây |
| 3 | Bug trong locustfile: user crash ngay sau khi đăng nhập | `steady` chỉ thấy request đăng nhập, Locust dùng 98% CPU | Gán header đúng cách của `FastHttpSession` | Mọi endpoint đều có request |
| 4 | Máy bắn tải là nút thắt | Locust 108% trên giới hạn 100% CPU | Chạy 2 process (`LOCUST_PROCESSES`) | Locust còn 32–82%, số đo phản ánh đúng api |
| 5 | Hai process Locust đăng nhập trùng tài khoản, gây tranh khóa giả trên `attendance_daily` | Câu upsert trung bình 1,34 ms, 11% session chờ `Lock` | Chia tài khoản xen kẽ theo process (`shapes.pick_code`) | 0,28 ms, hết chờ `Lock` |
| 6 | `pool_pre_ping` với asyncpg tốn `BEGIN` + `ROLLBACK` mỗi lần lấy kết nối | `pg_stat_statements`: 2 lệnh `BEGIN` cho mỗi request | Đưa ra biến `DB_POOL_PRE_PING`, mặc định vẫn bật theo spec | Khi tắt: ghi ở mức 100 user tăng từ 198 lên 232 request/giây (+15%) |
| 7 | Thử group commit (`commit_delay=1000`) | 216 so với 232 request/giây, nằm trong sai số | Đưa ra biến `PG_COMMIT_DELAY`, để mặc định 0 | Không cải thiện rõ trên laptop này |
| 8 | Vòng sao lưu tự động bị crash khi thư mục dump còn rỗng | Container `backup` khởi động lại liên tục | Hàm `newest_epoch` trả 0 khi thư mục rỗng | Dump và base backup tự chạy |
| 9 | `archive_command` có thể kẹt mãi nếu segment đã được lưu trước khi crash | Xem lại cấu hình | Segment đã có thì coi là thành công | Không còn nguy cơ kẹt |
| 10 | Khóa kỳ lương bị chặn cả khi đơn bị *từ chối* sau lúc tính lương | Review code | Chỉ tính đơn đã duyệt | Đơn bị từ chối không còn chặn khóa kỳ |

## 6. Sao lưu và khôi phục (diễn tập)

`./scripts/backup/drill.sh --yes` chạy trên một project riêng (`chamcong-drill`) nên không đụng dữ liệu thật. Kết quả lần đo này:

| Bước | Kết quả | Thời gian |
|---|---|---|
| PITR: giả lập `TRUNCATE attendance_events`, khôi phục về thời điểm T | Có mốc A, không có mốc B, số dòng mọi bảng khớp | Khoảng 18 giây, gồm cả việc nén bản dữ liệu hiện tại để an toàn |
| Dump: xóa bảng rồi `restore-dump.sh` | Số dòng khớp | Khoảng 3 giây (dump 1,8 MB) |
| Toàn bộ diễn tập | OVERALL: PASS | 73 giây |

- **RPO:**
  - PITR mất tối đa `WAL_ARCHIVE_TIMEOUT` (300 giây) dữ liệu.
  - Nếu chỉ dùng dump thì mất tối đa `BACKUP_INTERVAL_MINUTES` (30 phút).
- **RTO** tăng theo kích thước dữ liệu và lượng WAL phải phát lại, nên chạy lại diễn tập mỗi khi dữ liệu lớn lên.

## 7. Chạy lại các phép đo

```bash
cd ~/projects/chamcong-demo
docker compose up -d --build
docker compose exec api python -m scripts.seed
./loadtest/run.sh steady
```

Mỗi lần chạy, seed lại trước, rồi lần lượt `burst`, `ramp`, `login_storm`, `write_storm`, `write_ramp`. Tìm điểm gãy với người dùng thật:

```bash
RAMP_STEPS=100,200,300,400,600,800,1000 RAMP_STEP_SECONDS=45 ./loadtest/run.sh ramp
```

Thử với nhiều CPU hơn. Biến môi trường đặt khi gọi lệnh sẽ thay giá trị trong `.env`:

```bash
API_CPUS=3 DB_CPUS=2 docker compose up -d
```

```bash
API_CPUS=3 DB_CPUS=2 ./loadtest/run.sh write_ramp
```

Tổng hợp mọi lượt chạy vào `loadtest/results/SUMMARY.md`:

```bash
docker compose --profile loadtest run --rm --no-deps --entrypoint python locust report.py --summary
```

## 8. Hướng tối ưu tiếp (chưa làm trong demo)

1. **Scale ngang api**: chạy nhiều container api sau một load balancer. Đây là việc đầu tiên nên làm, vì api là nút thắt thứ nhất. Đến lúc đó nên thêm PgBouncer để gom kết nối. Lưu ý advisory lock mức session của job tính lương phải kết nối thẳng vào DB.
2. **Giảm CPU mỗi request**:
   - Dùng SQLAlchemy Core thay ORM ở các đường ghi nóng.
   - Gộp 2 câu INSERT của chấm công thành một câu CTE (bớt một lượt đi-về DB).
   - Trả JSON bằng orjson.
3. **Đĩa nhanh cho WAL**: chạy trên Linux thật hoặc dùng ổ NVMe riêng cho WAL. Trên WSL2, mỗi lần flush xuống đĩa chậm hơn.
4. **Không làm**: tắt `fsync` hoặc đặt `synchronous_commit=off` để lấy tốc độ. Như vậy sẽ mất lệnh đã báo thành công khi máy sập, trái với mục tiêu "không mất request".
