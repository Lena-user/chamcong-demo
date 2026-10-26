# 03. Áp dụng cho dự án khác

Đây là phần quan trọng nhất nếu bạn muốn dùng lại bộ load test cho một dự án lớn hơn. Hãy đọc từ trên xuống và làm theo checklist. Bộ công cụ chia làm hai phần:

- **Dùng nguyên, không sửa:** `loadtest/run.sh`, `loadtest/shapes.py`, `loadtest/report.py`, `loadtest/reconcile.py`, `loadtest/db_probe.sql`, `loadtest/Dockerfile`.
- **Viết lại cho dự án mới:** `loadtest/locustfile.py`, `loadtest/config.env` (đổi giá trị), `loadtest/app_reconcile.sql` (chỉ khi muốn đối soát lệnh ghi).

## 1. Điều kiện để dùng được nguyên bộ

`run.sh` giả định dự án của bạn:

1. Chạy bằng Docker Compose.
2. Có service tên **`db`** là PostgreSQL, với biến môi trường `POSTGRES_USER` và `POSTGRES_DB` (image `postgres` chuẩn đã có). `run.sh` gọi `docker compose exec -T db psql` để lấy mẫu và đối soát.
3. (Khuyến nghị) có service tên `api`, để `run.sh` ghi giá trị `RATE_LIMIT_ENABLED` vào `meta.json`. Nếu không có thì trường này là `null`, không gây lỗi.
4. (Khuyến nghị) Postgres nạp `shared_preload_libraries=pg_stat_statements` (xem `docker-compose.yml` của demo) để có bảng top câu lệnh SQL. Không có thì `run.sh` báo và bỏ qua bảng đó, các phần khác vẫn chạy.

Nếu DB của bạn là MySQL hay loại khác, `db_probe.sql` và các lệnh `psql` phải viết lại (mục 6).

## 2. Checklist từng bước

### Bước 1. Sao chép file

Chép cả thư mục `loadtest/` sang dự án mới, bỏ kết quả cũ:

```bash
cp -r chamcong-demo/loadtest du-an-moi/loadtest
rm -rf du-an-moi/loadtest/results/* du-an-moi/loadtest/sample
touch du-an-moi/loadtest/results/.gitkeep
```

Thêm vào `.gitignore` của dự án mới (nếu chưa có):

```
loadtest/results/*
!loadtest/results/.gitkeep
```

Chép luôn `.gitattributes` có dòng `* text=auto eol=lf` để script `.sh` không bị đổi sang CRLF trên Windows.

### Bước 2. Khai báo service `locust` trong compose

Thêm vào `docker-compose.yml` của dự án mới (đây là khai báo đang dùng trong demo):

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

Giải thích:

- `profiles: ["loadtest"]`: service không tự chạy khi `docker compose up`, chỉ `run.sh` gọi nó.
- `volumes`: mount cả thư mục để Locust ghi kết quả ra `loadtest/results/` trên máy chủ.
- `LOADTEST_PASSWORD`: mật khẩu tài khoản test, lấy từ `.env`. Đổi tên biến nguồn nếu dự án bạn đặt khác, nhưng đừng ghi cứng mật khẩu vào file được commit.
- `depends_on`: đổi `api` thành tên service ứng dụng của bạn (ví dụ `web`, `backend`).
- `deploy.resources.limits` của máy bắn tải: **đừng bỏ**. Nó giữ cho máy bắn tải không ăn hết máy, và để báo cáo biết khi chính nó nghẽn.

`loadtest/Dockerfile` ghim `locustio/locust:2.32.5`, `pandas==2.2.3`, `matplotlib==3.9.2` để biểu đồ lặp lại được. Giữ nguyên.

### Bước 3. Đặt giới hạn tài nguyên cho các service

Số đo chỉ có ý nghĩa khi giới hạn được đặt rõ và giống môi trường dự kiến. Với **mọi** service trong compose, thêm dạng:

```yaml
    deploy:
      resources:
        limits: { cpus: "2", memory: 2G }
```

Demo dùng: api 2 CPU / 2 GB, db 1 CPU / 2 GB, worker 1 CPU / 512 MB, redis 0,5 CPU / 256 MB. `run.sh` đọc giới hạn này bằng `docker inspect` để `report.py` tính "CPU 204% / 200%" và tìm điểm nghẽn. Không đặt giới hạn thì giới hạn được ghi là `null` (không giới hạn) và không kết luận được điểm nghẽn theo phần trăm.

Hai lưu ý: tổng giới hạn CPU của các container nên **nhỏ hơn** số nhân mà Docker nhìn thấy (`host_cpus` trong `meta.json`), nếu không các container tranh nhau nhân thật; và `.wslconfig` phải cấp đủ RAM (`docs/01-cai-dat-va-chay.md`).

### Bước 4. Viết lại `locustfile.py`

Quy tắc của bộ công cụ:

- Dùng `FastHttpUser` (nhẹ hơn, tốn ít CPU máy bắn tải).
- Lấy tham số từ `shapes` (`shapes.password()`, `shapes.user_codes(...)`, `shapes.wait_range()`, `shapes.env_int(...)`), đừng đọc `os.environ` rải rác.
- Cuối file phải có `Shape = shapes.make_shape()`. Biến này để Locust điều khiển số user và thời gian theo kịch bản, nên **không** truyền `-u -r -t`.
- Đặt tên endpoint bằng tham số `name=` (ví dụ `name="/items"`), để các URL có id khác nhau gộp thành một dòng trong báo cáo.
- Sau khi đăng nhập, gán `self.client.auth_header = "Bearer <token>"`. `FastHttpSession` không có `.headers`; gán vào `.headers` sẽ không gửi token và mọi request sau đó là 401 (lỗi này đã từng xảy ra ở demo và làm một lượt đo chỉ đo đăng nhập).
- Mỗi class User ứng với một vai (nhân viên, quản lý...). Locust sinh **mọi** class không `abstract`, nên khi một kịch bản chỉ cần một số class thì đặt `abstract = True` cho class không dùng (xem cuối `loadtest/locustfile.py` của demo).

#### Mẫu locustfile tối thiểu

Mẫu dưới đây giả định một API có `POST /auth/login` trả `{"token": "..."}`, một endpoint đọc `GET /items` và một endpoint ghi `POST /orders`. Đã kiểm tra: biên dịch bằng `python -m py_compile` trong image locust của demo, và import được cùng `loadtest/shapes.py`. Hãy đổi đường dẫn, tên trường JSON và dữ liệu gửi cho đúng API của bạn.

```python
"""Minimal locustfile for the load-test kit (assumed API: POST /auth/login, GET /items, POST /orders)."""
import itertools
import uuid

from locust import FastHttpUser, between, task
from locust.exception import StopUser

import shapes  # from the kit: config and load shapes, unchanged

PASSWORD = shapes.password()                    # LOADTEST_PASSWORD, comes from compose
CODES = shapes.user_codes("EMPLOYEE_RANGE", "1-200")
WAIT_MIN, WAIT_MAX = shapes.wait_range()
_seq = itertools.count()


class AppUser(FastHttpUser):
    wait_time = between(WAIT_MIN, WAIT_MAX)

    def on_start(self):
        code = CODES[next(_seq) % len(CODES)]
        with self.client.post(
            "/auth/login", json={"username": code, "password": PASSWORD},
            name="/auth/login", catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"login {resp.status_code}")
                raise StopUser()
            token = resp.json()["token"]
        self.client.auth_header = f"Bearer {token}"  # FastHttpSession has no .headers

    @task(8)  # the most used read endpoint
    def list_items(self):
        self.client.get("/items?size=20", name="/items")

    @task(2)  # the write endpoint, with an idempotency key
    def create_order(self):
        key = str(uuid.uuid4())
        with self.client.post(
            "/orders", json={"item_id": 1, "qty": 1}, headers={"Idempotency-Key": key},
            name="/orders (create)", catch_response=True,
        ) as resp:
            if resp.status_code in (200, 201):
                resp.success()
            else:
                resp.failure(f"create order {resp.status_code}")


Shape = shapes.make_shape()
```

Dùng mẫu: lưu thành `loadtest/locustfile.py`, đặt `EMPLOYEE_RANGE` và `USER_CODE_FORMAT` trong `config.env` cho khớp tài khoản test, rồi chạy `./loadtest/run.sh steady`. Mẫu chỉ có một class nên chạy được với `steady`, `burst`, `ramp`, `custom`. Muốn `login_storm` hoặc `write_*` thì thêm class riêng và dùng `abstract` như file gốc của demo.

Kiểm tra cú pháp trước khi chạy thật (không cần dựng stack). Sau `docker compose --profile loadtest build locust`, chạy lệnh dưới đây từ thư mục gốc dự án; `<image locust>` là tên image vừa build, xem bằng `docker image ls`:

```bash
docker run --rm --network none -v "$PWD/loadtest:/kit:ro" -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/kit \
  --entrypoint python <image locust> -c "import ast; ast.parse(open('/kit/locustfile.py').read()); print('ok')"
```

### Bước 5. Chuẩn bị tài khoản test

- Cần **nhiều** tài khoản, vì cùng một tài khoản đăng nhập hàng trăm lần sẽ dính rate limit và làm sai số đo. Demo có 300 tài khoản.
- Tạo bằng script seed của dự án (như `scripts/seed.py`): băm mật khẩu **một lần** rồi dùng chung cho mọi tài khoản để seed nhanh.
- Mật khẩu chung lấy từ `.env`, không ghi cứng.
- Mã đăng nhập đặt theo quy luật để `USER_CODE_FORMAT` và `*_RANGE` mô tả được (ví dụ `E0014` đến `E0300`).
- Tách vai: tài khoản thường và tài khoản quản lý/duyệt, vì kịch bản thường có hai nhóm hành vi (`MANAGER_WEIGHT`).
- Dữ liệu nền phải đủ lớn và giống thật (demo có khoảng 45 nghìn sự kiện chấm công), vì truy vấn trên bảng rỗng luôn nhanh.
- Chỉ dùng dữ liệu giả.

### Bước 6. Chọn endpoint để đo

Đừng đo tất cả. Chọn theo các tiêu chí:

1. **Endpoint dùng nhiều nhất** (thường là đọc: danh sách, trang chủ, hồ sơ). Chúng quyết định tải nền. Đặt trọng số cao bằng `@task(n)`.
2. **Endpoint ghi quan trọng nhất** (tạo đơn, thanh toán, chấm công). Chúng lộ khóa, tranh chấp và hàng đợi. Trọng số thấp hơn nhưng luôn có, và gửi kèm khóa idempotency nếu hệ thống hỗ trợ.
3. **Đăng nhập** thành kịch bản riêng (`login_storm`), vì băm mật khẩu có đường cong tải khác hẳn.
4. Thao tác hiếm nhưng nặng (báo cáo, xuất file, tính lương): đo riêng hoặc đưa vào job nền thay vì để trong request.

Mỗi endpoint cần một `name=` riêng để biết endpoint nào chậm.

### Bước 7. Chỉnh `config.env`

Đặt lại ít nhất: `TARGET_HOST` (địa chỉ service trong mạng compose, ví dụ `http://web:3000`), `USER_CODE_FORMAT`, `EMPLOYEE_RANGE`, `MANAGER_RANGE`, `MANAGER_WEIGHT`, và mục tiêu `TARGET_P95_MS`, `TARGET_ERR_PCT`. Các biến còn lại có thể giữ. Bảng đầy đủ ở `docs/02-load-test.md`.

### Bước 8. Đối soát lệnh ghi (tùy chọn nhưng nên có)

Muốn chứng minh "không mất lệnh ghi": mỗi lệnh ghi trong locustfile cần một khóa duy nhất (UUID) và được ghi vào `writes.jsonl` (xem `WriterUser._write` trong `loadtest/locustfile.py`). Sau đó viết lại `loadtest/app_reconcile.sql` để liệt kê các khóa nằm trong DB từ thời điểm `:since` (cột `kind,key,id,created_at`). `reconcile.py` và `run.sh` không cần sửa. Với loại ghi nào mà bảng chưa có cột khóa, thêm dòng `meta:<kind>_has_key` giá trị `false` như file mẫu, để báo cáo ghi "không kiểm chứng được" thay vì "mất".

Muốn chứng minh thêm "ghi đúng":
- Cho `app_reconcile.sql` xuất thêm cột `owner`, là người sở hữu dòng, ví dụ mã user. Ghi `code` của người gửi vào `writes.jsonl`. Như vậy đối soát kiểm được cả việc ghi nhầm người.
- Viết một script chỉ đọc kiểm các bất biến của dự án mới: bảng tổng hợp khớp với dữ liệu gốc, luật trạng thái, bảng chỉ-thêm không bị sửa. Trỏ `POST_CHECK_CMD` trong `loadtest/config.env` vào script đó. Mẫu: `scripts/check_integrity.py`.
- Viết thêm một test cố ý làm hỏng dữ liệu, để chắc script bắt được lỗi thật.

## 3. Thứ tự đo

Mỗi bước trả lời một câu hỏi và quyết định bước sau:

1. **`steady`** (tải bình thường). Ở tải mục tiêu, hệ thống có đạt p95 và tỷ lệ lỗi không? Nếu không đạt ngay ở đây thì dừng và sửa, đừng thử tải nặng hơn. Kiểm tra trước là không có lỗi đăng nhập hay 4xx nào và mọi endpoint đều có request thật.
2. **`burst`** (không nghỉ). Khi mọi user bắn liên tục, độ trễ tăng đều (hàng đợi) hay vọt lên (có khóa, pool cạn)? So p50 với p95.
3. **`ramp`** (tăng dần). Giới hạn nằm ở đâu? Ghi số user ở điểm gãy và container nào chạm trần.
4. **`login_storm`** (đăng nhập ồ ạt). Băm mật khẩu có kéo sập hệ thống không? Đây thường là điểm yếu bị bỏ sót, vì đăng nhập chỉ là vài phần trăm tải thường ngày, nhưng khi nhiều người cùng đăng nhập (đầu ca, sáng thứ hai) thì tốn CPU rất lớn.
5. **`write_storm` rồi `write_ramp`** (nếu đã làm bước 8). Dưới tải ghi cao, có mất hay trùng lệnh không?

Giữa các bước: sửa **một** nguyên nhân, đo lại, ghi số trước và sau. Sửa nhiều thứ cùng lúc thì không biết thứ nào có tác dụng.

## 4. Bảng "Triệu chứng, nguyên nhân hay gặp, cách xử lý"

| Triệu chứng trong báo cáo | Nguyên nhân hay gặp | Cách xác nhận và xử lý |
|---|---|---|
| CPU api chạm trần (ví dụ 200% / 200%), p95 tăng dần theo số user, db còn rảnh | Ứng dụng tốn CPU: băm mật khẩu, tuần tự hóa JSON lớn, logic nặng trong request, hoặc số worker ít hơn số nhân. | Xem endpoint nào chậm nhất trong bảng từng endpoint. Tăng số worker cho khớp số nhân, tăng CPU cho api, cache kết quả đọc, đưa việc nặng sang job nền. Nếu nghẽn ở `login`, xem hàng "đăng nhập chậm". |
| CPU db chạm trần (100% / 100%), api còn rảnh | Truy vấn nặng hoặc thiếu index, quét toàn bảng, quá nhiều truy vấn nhỏ (N+1). | Mở `pg_top_queries.csv`: câu nào có `total_ms` lớn nhất. Chạy `EXPLAIN (ANALYZE, BUFFERS)` trên câu đó. Thêm index đúng cột lọc/sắp xếp. Giảm số truy vấn mỗi request. Tăng CPU cho db là bước cuối. |
| p95 cao nhưng CPU mọi container đều thấp | Request đang **chờ** chứ không tính toán: pool kết nối cạn, chờ khóa dòng, I/O blocking trong code async, gọi dịch vụ ngoài chậm, hoặc chính máy bắn tải nghẽn. | Xem `chart_db.png` và `pg_samples.jsonl`: nhiều phiên đang chờ `Lock/...` không? Log api có `503 db pool timeout` không? Dòng `locust` trong bảng CPU có chạm trần không? Rút ngắn transaction. Thay thư viện blocking bằng bản async hoặc đẩy vào thread pool. Tăng pool chỉ khi ngân sách kết nối cho phép (mục 5). |
| Lỗi tăng vọt: 500 hoặc 503 do hết pool (`QueuePool limit`, `db pool timeout`) | Pool nhỏ hơn số request đồng thời, hoặc request giữ kết nối quá lâu (ví dụ vừa giữ kết nối vừa băm mật khẩu). | Trả kết nối về pool trước khi làm việc tốn CPU (demo làm vậy ở `login`). Rút ngắn transaction. Tăng pool trong ngân sách. Đổi lỗi tạm thời thành 503 kèm `Retry-After` thay vì 500. |
| Lỗi `too many connections` (SQLSTATE 53300) hoặc `remaining connection slots are reserved` | Tổng kết nối của mọi process vượt `max_connections` của Postgres. | Tính lại ngân sách (mục 5). Giảm `pool_size`/`max_overflow` hoặc số worker, hoặc dùng bộ gộp kết nối như PgBouncer (xem lưu ý ở `docs/07-kien-truc-va-thiet-ke.md`). Đừng chỉ tăng `max_connections`: mỗi kết nối tốn RAM. |
| Đăng nhập chậm: `login_storm` có p95 hàng chục giây, CPU api kịch trần | Băm mật khẩu (bcrypt, argon2) cố ý tốn CPU và RAM. Nhiều lần đăng nhập đồng thời xếp hàng trên số nhân hữu hạn. | Giới hạn số hash đồng thời mỗi process (`HASH_CONCURRENCY`) để không hết RAM. Đặt hàng chờ tối đa và trả 503 kèm `Retry-After` khi đầy (`LOGIN_MAX_WAITING`). Dùng tham số tối thiểu của OWASP, không cao hơn mức cần. Cấp token sống lâu (demo: 12 giờ) để người dùng ít phải đăng nhập lại. Đừng hạ độ mạnh của hash xuống dưới khuyến nghị chỉ để đạt số đo; hãy chấp nhận giới hạn thật và ghi vào báo cáo. |
| Cùng một câu SQL được gọi hàng nghìn lần trong `pg_top_queries.csv`, số truy vấn mỗi request lớn | N+1: lặp qua danh sách rồi truy vấn từng phần tử. | Gom thành một truy vấn `JOIN` hoặc `WHERE id = ANY(...)`. Viết test đếm số truy vấn mỗi endpoint (demo có `tests/test_query_count.py::test_query_budget`) để lỗi không quay lại. |
| Một endpoint có p50 cao (hàng trăm ms) kể cả khi tải thấp | Việc nặng nằm trong request: tính toán lớn, tạo báo cáo, gửi email, gọi API ngoài. | Chuyển sang hàng đợi và job nền; request chỉ trả `202` kèm id công việc (demo: tính lương qua Arq). Chỉ giữ trong request những gì câu trả lời bắt buộc cần. |
| Lỗi 409 hoặc 5xx tăng khi nhiều người sửa cùng một bản ghi | Tranh chấp ghi: không có khóa lạc quan, cập nhật đè nhau. | Xem `docs/04-thiet-ke-dong-thoi.md`: optimistic locking, conditional update, idempotency key. |
| Đối soát báo "mất" lệnh ghi | Có thể mất thật (client nhận 2xx mà DB không có), hoặc DB bị seed lại/xóa giữa lúc đo. | Chắc chắn không ai seed lại trong lúc đo. Nếu vẫn mất: kiểm tra code có trả lời trước khi commit không (ghi bất đồng bộ). Xem `docs/07-kien-truc-va-thiet-ke.md`. |
| RPS đi ngang dù tăng user, p95 tăng | Bão hòa: hệ thống đã ở công suất tối đa, thêm tải chỉ làm hàng đợi dài hơn. | Đây chính là điểm gãy. Ghi lại, rồi tìm container chạm trần để biết nâng cấp ở đâu. |

## 5. Cách tính số kết nối DB

Mỗi process ứng dụng giữ một pool kết nối riêng. Công thức:

```
tổng = số_worker_api x (pool_size + max_overflow)
     + pool_của_worker_nền x số_job_đồng_thời
     + kết_nối_dành_riêng (psql, backup, probe, migration)

điều kiện: tổng < max_connections của Postgres
```

Với demo, dùng đúng các biến trong `.env`:

```
UVICORN_WORKERS x (DB_POOL_SIZE + DB_MAX_OVERFLOW)  =  3 x (10 + 5)  = 45
WORKER_DB_POOL_SIZE x ARQ_MAX_JOBS                  =  2 x 2         =  4
DB_RESERVED_CONNECTIONS                                              = 10
                                                       tổng          = 59   (max_connections = 100)
```

Api ghi một dòng WARNING lúc khởi động nếu tổng vượt `max_connections` (xem `check_connection_budget` trong `app/main.py`). Cần nhớ:

- Mỗi worker uvicorn là một process riêng với pool riêng, nên nhân đôi số worker là nhân đôi số kết nối.
- `max_overflow` là kết nối tạm, chỉ mở khi pool cạn, nhưng vẫn phải tính vào trần.
- Pool lớn hơn không làm nhanh hơn. Postgres chạy tốt khi số truy vấn **đang chạy** chỉ gấp vài lần số nhân CPU; pool quá lớn chỉ dời hàng đợi từ ứng dụng vào trong DB mà không thêm thông lượng.
- Đặt `pool_timeout` ngắn (demo: 10 giây) để hết kết nối thì trả 503 nhanh, thay vì giữ request chờ lâu.

## 6. Dự án không dùng Python/FastAPI

| Phần | Dùng nguyên? | Ghi chú |
|---|---|---|
| `run.sh` | Dùng nguyên | Chỉ cần compose có service `db` (Postgres). Tên service khác thì sửa biến `PSQL` và lệnh `exec -T api printenv` trong `run.sh`. |
| `shapes.py` | Dùng nguyên | Chỉ phụ thuộc Locust, không phụ thuộc ngôn ngữ của ứng dụng bị đo. Locust bắn HTTP nên đo được mọi backend. |
| `report.py` | Dùng nguyên | Đọc CSV của Locust, `docker_stats.jsonl`, `meta.json`. Tự nhận service theo tên trong compose. |
| `reconcile.py` | Dùng nguyên | Chỉ đọc `writes.jsonl` và `db_keys.csv`. |
| `db_probe.sql` | Dùng nguyên với PostgreSQL | Với DB khác, viết lại hai mục `sample` và `top_queries`. |
| `Dockerfile` (locust) | Dùng nguyên | Đã ghim phiên bản. |
| `locustfile.py` | **Viết lại** | Đường dẫn, JSON, luồng đăng nhập, tên vai trò. |
| `config.env` | **Chỉnh giá trị** | Giữ nguyên tên biến. |
| `app_reconcile.sql` | **Viết lại** | Tên bảng và cột khóa của dự án. |
| `sample/` | Không cần | Chỉ để minh họa định dạng báo cáo. |

Với backend Node, Go, Java...: Locust chỉ cần địa chỉ HTTP nên mọi thứ dùng được. Phần phải tính lại là ngân sách kết nối (mục 5) theo thư viện pool của ngôn ngữ đó. Các mẫu chống tranh chấp ở `docs/04-thiet-ke-dong-thoi.md` mang sang được vì chúng là SQL.
