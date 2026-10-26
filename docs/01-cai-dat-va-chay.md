# 01. Cài đặt và chạy

Tài liệu này dẫn từng bước từ máy trắng đến lúc mở được web và chạy được test. Lệnh, tên biến và code giữ nguyên tiếng Anh.

## 1. Yêu cầu máy

- Windows 11 với WSL2 (bản Ubuntu) và Docker Desktop.
- Docker Desktop: vào Settings, Resources, WSL integration, bật tích cho bản Ubuntu bạn dùng. Sau đó gõ `docker compose version` trong terminal WSL phải ra phiên bản.
- Đặt repo trong filesystem của WSL (ví dụ `~/projects/chamcong-demo`), **không** đặt ở `/mnt/c/...`. Lý do: đọc ghi file qua `/mnt/c` chậm hơn nhiều lần, và Postgres/Docker volume dễ gặp lỗi quyền file.
- RAM và CPU: tổng giới hạn trong `docker-compose.yml` là khoảng 6 GB RAM (api 2 GB, db 2 GB, worker 512 MB, redis 256 MB, backup 256 MB, locust 1 GB) và 6 CPU khi chạy đủ. Máy nên có ít nhất 8 GB RAM trống cho WSL và 4 nhân CPU.

### Cấp RAM cho WSL bằng `.wslconfig`

WSL2 mặc định lấy khoảng một nửa RAM máy. Nếu để mặc định trên máy 8 GB, container sẽ bị kill vì hết bộ nhớ khi load test. Tạo file `C:\Users\<tên bạn>\.wslconfig` (trên Windows, không phải trong WSL):

```ini
[wsl2]
memory=8GB
processors=6
swap=2GB
```

Vì sao: `memory` quyết định trần RAM của toàn bộ container cộng lại, `processors` là số nhân mà Docker nhìn thấy. Nếu `processors` nhỏ hơn tổng giới hạn CPU của các container, số đo load test sẽ sai vì các container tranh nhau nhân thật. Sửa xong chạy `wsl --shutdown` trong PowerShell, rồi mở lại WSL và Docker Desktop. Chỉnh `memory` và `processors` cho khớp máy của bạn.

## 2. Clone và tạo `.env`

```bash
git clone <địa chỉ repo> chamcong-demo
cd chamcong-demo
cp .env.example .env
```

Mở `.env`, sửa tối thiểu 3 chỗ: `POSTGRES_PASSWORD` (compose tự ghép thành `DATABASE_URL` và `TEST_DATABASE_URL`), `JWT_SECRET` (chuỗi ngẫu nhiên dài) và `SEED_PASSWORD` (mật khẩu chung của mọi tài khoản demo). Có thể sinh chuỗi ngẫu nhiên bằng `openssl rand -hex 32`.

Các biến sau phải có trong `.env` (compose không có giá trị mặc định cho chúng): `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `REDIS_URL`, `JWT_SECRET`, `SEED_PASSWORD`, `ALLOWED_HOSTS`. Các biến còn lại có giá trị mặc định.

### Bảng biến trong `.env.example`

PostgreSQL và Redis:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `POSTGRES_USER` | `chamcong` | Tên user của container `db`. |
| `POSTGRES_PASSWORD` | `change-me-db` | Mật khẩu user đó. Phải đổi. |
| `POSTGRES_DB` | `chamcong` | Tên database chính. |
| `DATABASE_URL` | tự ghép từ `POSTGRES_*` | Địa chỉ DB cho api và worker. Chỉ đặt khi muốn ghi đè. Host `db` là tên service trong compose. |
| `TEST_DATABASE_URL` | tự ghép từ `POSTGRES_*` | DB riêng cho pytest, do `scripts/init-db.sql` tạo ở lần đầu khởi tạo volume. |
| `REDIS_URL` | `redis://redis:6379/0` | Redis cho hàng đợi Arq, rate limit và health check. |

Xác thực và HTTP:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `JWT_SECRET` | `change-me-to-a-long-random-string` | Khóa ký JWT (HS256). Phải đổi, dùng chuỗi dài ngẫu nhiên. |
| `JWT_EXPIRE_HOURS` | `12` | Thời hạn token, tính bằng giờ. |
| `SEED_PASSWORD` | `change-me-demo` | Mật khẩu chung của mọi tài khoản do `seed.py` tạo. Locust cũng lấy mật khẩu từ biến này. |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1,api,*.trycloudflare.com` | Danh sách Host header được chấp nhận. Header khác bị trả 400. |

Nghiệp vụ và vận hành (đều tùy chọn):

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `TIMEZONE` | `Asia/Ho_Chi_Minh` | Múi giờ để gán sự kiện chấm công vào ngày công. |
| `DEFAULT_PAGE_SIZE` | `20` | Số dòng mặc định của API danh sách. |
| `MAX_PAGE_SIZE` | `100` | Số dòng tối đa mỗi trang. |
| `PAYROLL_LOCK_KEY` | `7300001` | Khóa `pg_advisory_lock`: mỗi lúc chỉ một job tính lương chạy. |
| `PAYROLL_JOB_TIMEOUT` | `300` | Timeout job tính lương (giây). |
| `STANDARD_DAILY_MINUTES` | `480` | Số phút của một ngày công chuẩn. Vượt quá là OT. |
| `UNPAID_BREAK_AFTER_MINUTES` | `360` | Làm quá số phút này thì trừ giờ nghỉ không lương. |
| `UNPAID_BREAK_MINUTES` | `60` | Số phút nghỉ không lương bị trừ. |
| `NIGHT_SHIFT_CUTOFF_HOUR` | `12` | Sự kiện trước giờ này, nếu ngày hôm trước có ca qua nửa đêm, thì tính cho ngày hôm trước. |
| `SCENARIO` | `steady` | Kịch bản mặc định của service `locust`. `run.sh` tự đặt biến này nên thường không cần sửa. |

Sao lưu (xem `docs/06-sao-luu-khoi-phuc.md`):

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `BACKUP_DIR` | `./backups` | Thư mục trên máy chủ chứa `dump/`, `base/`, `wal/`, `pre-restore/`. Đã gitignore. |
| `BACKUP_INTERVAL_MINUTES` | `30` | Chu kỳ `pg_dump`. |
| `BASEBACKUP_INTERVAL_HOURS` | `24` | Chu kỳ base backup (điểm xuất phát cho PITR). |
| `BACKUP_KEEP_DUMPS` | `48` | Số bản dump giữ lại. |
| `BACKUP_KEEP_BASE` | `3` | Số base backup giữ lại. WAL cũ hơn base backup cũ nhất bị xóa. |
| `WAL_ARCHIVE_TIMEOUT` | `300` | Ép chuyển file WAL ít nhất mỗi N giây. Đây là cận trên lượng dữ liệu mất khi PITR. |

Cổng:

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `API_PORT` | `8000` | Cổng trên máy chủ, chỉ bind `127.0.0.1`. |

Ghi an toàn và quá tải (đều tùy chọn):

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `ARGON2_TIME_COST` | `2` | Số vòng argon2id. Mặc định theo mức tối thiểu của OWASP. |
| `ARGON2_MEMORY_KIB` | `19456` | Bộ nhớ mỗi lần hash (19 MiB). |
| `ARGON2_PARALLELISM` | `1` | Số lane. Hash cũ vẫn xác minh được khi đổi ba giá trị này, vì tham số nằm trong chuỗi hash. |
| `HASH_CONCURRENCY` | `2` | Số hash đồng thời mỗi process (mỗi cái tốn `ARGON2_MEMORY_KIB` RAM). |
| `LOGIN_MAX_WAITING` | `200` | Số đăng nhập xếp hàng chờ hash mỗi process. Vượt thì trả 503. |
| `RETRY_AFTER_SECONDS` | `2` | Giá trị header `Retry-After` trên mọi 503. |
| `DB_STATEMENT_TIMEOUT_MS` | `5000` | Query chạy quá lâu thì bị Postgres hủy. |
| `DB_LOCK_TIMEOUT_MS` | `2000` | Chờ khóa quá lâu thì báo lỗi. |
| `DB_IDLE_IN_TX_TIMEOUT_MS` | `10000` | Transaction bỏ dở quá lâu thì bị đóng. |
| `UVICORN_WORKERS` | `3` | Số process của api. |
| `DB_POOL_SIZE` | `10` | Số kết nối thường trực mỗi process api. |
| `DB_MAX_OVERFLOW` | `5` | Số kết nối tạm thêm mỗi process api. |
| `DB_POOL_TIMEOUT` | `10` | Chờ lấy kết nối từ pool tối đa N giây, quá thì 503. |
| `WORKER_DB_POOL_SIZE` | `2` | Pool riêng của worker Arq. |
| `ARQ_MAX_JOBS` | `2` | Số job nền chạy đồng thời. |
| `DB_RESERVED_CONNECTIONS` | `10` | Chỗ dành sẵn cho psql, backup, probe, khi tính ngân sách kết nối. |
| `ACCESS_LOG` | `true` | Mỗi request một dòng log JSON. Header `X-Request-ID` luôn được trả về. |
| `RATE_LIMIT_ENABLED` | `true` | Bật giới hạn tần suất bằng Redis. Đặt `false` trước khi load test. |
| `RATE_LIMIT_FAIL_OPEN` | `true` | Redis hỏng thì cho request đi qua (có ghi log). `false` thì trả 503. |
| `RATE_LOGIN_PER_MIN` | `20` | Số lần đăng nhập mỗi phút cho mỗi mã nhân viên. |
| `RATE_WRITE_PER_MIN` | `600` | Số lệnh ghi mỗi phút cho mỗi user. |
| `AUTH_CACHE_TTL_SECONDS` | `30` | Cache tra cứu danh tính trong process. `0` là tắt. Nhân viên đã nghỉ có thể dùng token đến N giây trên process đã cache. |
| `AUTH_CACHE_MAX` | `10000` | Số mục tối đa của cache đó. |
| `DB_POOL_PRE_PING` | `true` | Kiểm tra kết nối trước mỗi lần lấy khỏi pool. Với asyncpg mỗi lần kiểm tra tốn `BEGIN` + `ROLLBACK` (thêm 3 lượt đi-về DB). Tắt đi thì ghi nhanh hơn khoảng 15% (đo ở `docs/08`), nhưng sau khi DB khởi động lại thì request đầu tiên của mỗi kết nối cũ sẽ lỗi. |
| `API_CPUS`, `API_MEMORY` | `2`, `2G` | Giới hạn tài nguyên container api. |
| `DB_CPUS`, `DB_MEMORY` | `1`, `2G` | Giới hạn container db. |
| `WORKER_CPUS`, `WORKER_MEMORY` | `1`, `512M` | Giới hạn container worker. |
| `REDIS_CPUS`, `REDIS_MEMORY` | `0.5`, `256M` | Giới hạn container redis. |
| `BACKUP_CPUS`, `BACKUP_MEMORY` | `0.5`, `256M` | Giới hạn container backup. |
| `LOCUST_CPUS` | `2` | Giới hạn CPU của máy bắn tải (chỉ dùng khi load test). |
| `PG_MAX_WAL_SIZE`, `PG_CHECKPOINT_TIMEOUT` | `2GB`, `10min` | Checkpoint thưa hơn cho tải ghi nặng. |
| `PG_COMMIT_DELAY`, `PG_COMMIT_SIBLINGS` | `0`, `5` | Group commit của Postgres (micro giây). Đã thử `1000`, không thấy lợi rõ trên laptop này nên để tắt. |

Ngân sách kết nối DB: `UVICORN_WORKERS x (DB_POOL_SIZE + DB_MAX_OVERFLOW) + WORKER_DB_POOL_SIZE x ARQ_MAX_JOBS + DB_RESERVED_CONNECTIONS` phải nhỏ hơn `max_connections` (100). Với mặc định: 3 x 15 + 2 x 2 + 10 = 59. Vượt thì api ghi WARNING lúc khởi động.

## 3. Khởi động

```bash
docker compose up -d --build
docker compose ps
```

Kết quả mong đợi: `db`, `redis`, `api` ở trạng thái healthy, `worker` và `backup` đang chạy. Service `tunnel` **không** chạy (nằm trong profile riêng).

Migrate: không phải làm tay. `Dockerfile` chạy `alembic upgrade head` trước khi bật uvicorn, nên mỗi lần api khởi động schema được đưa lên bản mới nhất. Muốn chạy tay: `docker compose exec api alembic upgrade head`.

Kiểm tra: `curl -s localhost:8000/health` phải trả `{"status":"ok","db":"ok","redis":"ok"}`.

## 4. Seed dữ liệu giả

```bash
docker compose exec api python -m scripts.seed
```

Seed **xóa sạch** dữ liệu các bảng nghiệp vụ rồi tạo lại (chạy lại bao nhiêu lần cũng được). Mật khẩu của mọi tài khoản là `SEED_PASSWORD` trong `.env`. Với 300 nhân viên mất vài giây.

| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `--employees` | `300` | Số nhân viên. Phải đủ chỗ cho 1 admin, 2 hr, 2 manager mỗi phòng và ít nhất 1 employee. |
| `--departments` | `5` | Số phòng ban. |
| `--days` | `91` | Số ngày lịch sử chấm công. |
| `--pending-requests` | `40` | Số đơn đang chờ duyệt. |

Ví dụ nhỏ để thử nhanh: `docker compose exec api python -m scripts.seed --employees 50 --days 30`. Xem `--help` để biết thêm.

Ghi chú: file load test mặc định dùng tài khoản `E0014` đến `E0300` (nhân viên) và `E0004` đến `E0013` (quản lý). Nếu seed ít hơn 300 người, chỉnh `EMPLOYEE_RANGE` và `MANAGER_RANGE` (xem `docs/02-load-test.md`).

## 5. Đăng nhập và mở web

Mở http://localhost:8000. Đăng nhập bằng mã `E0001` (admin) hoặc `E0004` (quản lý), mật khẩu là `SEED_PASSWORD`. Tài liệu API tự sinh của FastAPI ở http://localhost:8000/docs.

## 6. Xem log, dừng, xóa

```bash
docker compose logs -f api            # log api (JSON một dòng mỗi request)
docker compose logs -f worker db      # log nhiều service
docker compose stop                   # dừng, giữ dữ liệu
docker compose up -d                  # chạy lại
docker compose down                   # xóa container, GIỮ volume dữ liệu
docker compose down -v                # xóa cả volume: MẤT TOÀN BỘ DỮ LIỆU
```

Cảnh báo: `down -v` xóa volume `pgdata` và `redisdata`, tức là mất cơ sở dữ liệu. Thư mục `backups/` nằm trên ổ đĩa máy chủ nên vẫn còn, nhưng bạn sẽ phải khôi phục (xem `docs/06-sao-luu-khoi-phuc.md`) hoặc seed lại.

## 7. Chạy test

```bash
docker compose run --rm api pytest -q
```

Test dùng DB `chamcong_test` (không đụng dữ liệu demo) và tự tắt rate limit. Test của bộ load test (không cần stack) chạy bằng:

```bash
python3 -m unittest loadtest/test_loadtest.py
```

Bài test `BreakGuard` trong đó cần locust nên chỉ chạy trong image locust, ở máy thường nó được bỏ qua.

## 8. Lỗi thường gặp

| Triệu chứng | Nguyên nhân | Cách xử lý |
|---|---|---|
| `/usr/bin/env: 'bash\r': No such file or directory`, hoặc script `.sh` báo lỗi lạ | File bị chuyển sang CRLF khi clone trên Windows. Repo có `.gitattributes` ép LF, nhưng clone bằng Git for Windows với cấu hình cũ vẫn có thể ra CRLF. | Clone lại bên trong WSL. Hoặc `git config core.autocrlf false`, xóa thư mục rồi clone lại. Không sửa tay từng file. |
| `port is already allocated` hoặc `address already in use` | Cổng 8000 đã có chương trình khác dùng. | Đặt `API_PORT=8001` trong `.env` rồi `docker compose up -d`. Tìm tiến trình đang chiếm: `ss -ltnp \| grep 8000`. |
| `Cannot connect to the Docker daemon` | Docker Desktop chưa chạy, hoặc chưa bật WSL integration. | Mở Docker Desktop, chờ biểu tượng xanh. Kiểm tra Settings, Resources, WSL integration. |
| `api` ở trạng thái `unhealthy` hoặc cứ khởi động lại | Thường là DB chưa sẵn sàng, sai mật khẩu DB, hoặc thiếu biến bắt buộc. | `docker compose logs api`. Kiểm tra mật khẩu trong `DATABASE_URL` khớp `POSTGRES_PASSWORD`. Lưu ý: đổi `POSTGRES_PASSWORD` sau khi volume đã tạo thì Postgres vẫn dùng mật khẩu cũ. Hoặc đặt lại mật khẩu trong DB, hoặc `docker compose down -v` (mất dữ liệu). |
| `curl` hoặc trình duyệt nhận `400 Invalid host header` | Host header không nằm trong `ALLOWED_HOSTS`. | Thêm tên host vào `ALLOWED_HOSTS` trong `.env`, rồi `docker compose up -d --no-deps api`. |
| Compose cảnh báo `The "SEED_PASSWORD" variable is not set`, hoặc không ai đăng nhập được sau khi seed | Quên đặt `SEED_PASSWORD` trong `.env`, nên mật khẩu đã hash là chuỗi rỗng. | Đặt `SEED_PASSWORD` rồi `docker compose up -d --no-deps api` để api nhận biến, sau đó seed lại. |
| Đăng nhập báo 401 dù đúng mật khẩu | Đổi `SEED_PASSWORD` sau khi seed. Mật khẩu đã hash lúc seed. | Chạy lại `docker compose exec api python -m scripts.seed`. |
| Sửa `.env` nhưng không có tác dụng | Container chưa nạp lại biến. | `docker compose up -d --no-deps api worker` (chỉ tạo lại container, không build lại). |
| Container bị kill, `exit code 137` | Hết RAM của WSL. | Tăng `memory` trong `.wslconfig` (mục 1). |
