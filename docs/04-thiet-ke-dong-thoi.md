# 04. Thiết kế chống tranh chấp dữ liệu

Khi nhiều request cùng ghi vào một dữ liệu, hệ thống dễ sai theo những cách mà test tuần tự không bắt được: ghi trùng, ghi đè nhau, duyệt hai lần. Demo đặt ra 6 quy tắc (NV-1 đến NV-6). Mỗi quy tắc dưới đây có: vấn đề, cách giải bằng SQL, file code, test, và vì sao phải đặt ràng buộc trong DB.

Nguyên tắc chung: **mọi test đồng thời bắn bằng `asyncio.gather`, rồi kiểm tra kết quả cuối cùng nằm trong DB** (không chỉ nhìn mã trả về). Ràng buộc phải nằm trong DB vì code ứng dụng có thể chạy trên nhiều process (ở demo là 3 worker uvicorn và 1 worker nền), nên kiểm tra "xem đã có chưa rồi mới ghi" trong code luôn có khe hở giữa hai bước. DB là nơi duy nhất nhìn thấy mọi người ghi.

## NV-1. Chấm công idempotent

- **Vấn đề:** người dùng bấm hai lần, hoặc mạng chập chờn nên client gửi lại. Không được ghi hai sự kiện chấm công.
- **Cách giải:** client sinh `idempotency_key` (UUID) cho mỗi lần bấm. DB có ràng buộc `UNIQUE` trên cột này. Ghi bằng `INSERT ... ON CONFLICT DO NOTHING`. Request trùng không ghi gì và trả lại kết quả cũ (HTTP 200 với `duplicate: true`, lần đầu là 201).

  SQL tương đương (SQLAlchemy sinh ra câu này):

  ```sql
  INSERT INTO attendance_events (employee_id, event_time, source, idempotency_key)
  VALUES (:employee_id, now(), :source, :key)
  ON CONFLICT (idempotency_key) DO NOTHING
  RETURNING id, event_time
  ```

  Không có dòng trả về nghĩa là key đã tồn tại. Khi đó service đọc lại sự kiện cũ. Nếu key đó thuộc nhân viên khác thì trả 409, không để lộ hay dùng lại sự kiện của người khác.

  Bảng tổng hợp ngày (`attendance_daily`) cập nhật theo kiểu tăng dần, ngay trong câu `INSERT ... ON CONFLICT (employee_id, work_date) DO UPDATE` với `LEAST(first_in, ...)` và `GREATEST(last_out, ...)`. Không tính lại từ ảnh chụp cũ, vì hai lần chấm công chạy đua nhau sẽ ghi đè lẫn nhau và mất dữ liệu.
- **File:** `app/modules/attendance/repository.py` (`insert_event`, `_UPSERT_FOR_EVENT`), `app/modules/attendance/service.py` (`check`), `app/modules/attendance/models.py` (cột `idempotency_key` có `unique=True`).
- **Test:** `tests/test_attendance.py::test_nv1_concurrent_same_key` (20 request cùng key thì đúng 1 dòng trong DB, mọi người nhận cùng `event_id`), `test_check_first_then_duplicate`, `test_check_key_of_another_employee_is_409`, `test_concurrent_checks_keep_daily_consistent`.
- **Vì sao đặt trong DB:** `UNIQUE` là thứ duy nhất đảm bảo đúng một dòng khi 20 request đến cùng lúc ở nhiều process. Kiểu "SELECT xem có chưa rồi INSERT" sẽ cho cả 20 request cùng thấy "chưa có".

## NV-2. Sửa hồ sơ bằng khóa lạc quan (optimistic locking)

- **Vấn đề:** hai người cùng mở hồ sơ một nhân viên, mỗi người sửa một chỗ rồi lưu. Người lưu sau vô tình ghi đè thay đổi của người lưu trước mà không biết.
- **Cách giải:** mỗi hồ sơ có cột `version`. Client gửi kèm `version` đã đọc. `UPDATE` chỉ khớp khi version vẫn là bản client đã thấy, và tăng version lên 1. Khớp 0 dòng nghĩa là có người sửa trước, trả 409 để client tải lại.

  ```sql
  UPDATE employees
  SET full_name = :full_name, version = version + 1
  WHERE id = :id AND version = :version
  RETURNING *
  ```

  (Các cột được cập nhật phụ thuộc vào những trường client gửi, ví dụ `full_name`, `department`.)
- **File:** `app/modules/employees/repository.py` (`update_versioned`), `app/modules/employees/service.py`.
- **Test:** `tests/test_employees.py::test_nv2_concurrent_patch_same_version` (10 PATCH cùng version: đúng một 200, chín 409, version tăng đúng 1, và chỉ một bản ghi audit).
- **Vì sao đặt trong DB:** điều kiện `AND version = :version` được đánh giá nguyên tử cùng lúc với việc ghi. Nếu đọc version ở code rồi so sánh, hai request vẫn có thể cùng thấy version cũ. Thêm `CHECK (version >= 1)` giữ cho cột không bị ghi bậy.

## NV-3. Duyệt đơn bằng conditional update

- **Vấn đề:** nhiều quản lý, hoặc một quản lý bấm hai lần, cùng duyệt một đơn. Chỉ một quyết định được thắng; không được duyệt rồi từ chối cùng lúc.
- **Cách giải:** chuyển trạng thái chỉ khi đơn còn `pending`. Người đến sau khớp 0 dòng và nhận 409.

  ```sql
  UPDATE requests
  SET status = :new_status, approved_by = :actor_id, decided_at = now()
  WHERE id = :id AND status = 'pending'
  RETURNING *
  ```

  Có một ngoại lệ có chủ đích: **cùng người, cùng kết quả** gửi lại (do timeout, không biết lần đầu đã thành công chưa) thì trả 200 chứ không phải 409. Người khác, hoặc kết quả khác, vẫn 409.
- **File:** `app/modules/requests/repository.py` (`decide`), `app/modules/requests/service.py` (`decide_request`, `_replay_or_conflict`).
- **Test:** `tests/test_requests.py::test_nv3_concurrent_approve` (10 người duyệt cùng lúc: một 200, chín 409, `approved_by` là người thắng), `tests/test_safe_writes.py::test_approve_replay_same_manager_200_other_409`, `test_concurrent_resend_of_same_approval`.
- **Vì sao đặt trong DB:** `WHERE status = 'pending'` biến "kiểm tra rồi ghi" thành một thao tác nguyên tử. Postgres khóa dòng khi `UPDATE`, nên đúng một request đổi được trạng thái. Có `CHECK (status IN ('pending','approved','rejected'))` để không bao giờ có trạng thái lạ.

## NV-4. Kỳ lương bị đóng thì không được sửa; mỗi lúc chỉ một job tính lương

- **Vấn đề:** hai chuyện cùng lúc: (a) đang tính lương mà có đơn mới được duyệt vào kỳ đó thì phiếu lương sai so với dữ liệu; (b) hai job tính lương chạy cùng lúc cho cùng kỳ.
- **Cách giải (a):** kỳ có trạng thái `draft → calculating → locked → paid`. Khi tạo hoặc duyệt đơn có `work_date` thuộc kỳ, service đọc kỳ bằng `SELECT ... FOR SHARE` trong **cùng transaction** với việc ghi. Nếu kỳ là `calculating`, `locked` hoặc `paid` thì từ chối bằng 409.

  ```sql
  SELECT status FROM payroll_periods
  WHERE year = :year AND month = :month
  FOR SHARE
  ```

  Lệnh chuyển kỳ sang `calculating` là một `UPDATE`:

  ```sql
  UPDATE payroll_periods
  SET status = 'calculating', calculating_started_at = now()
  WHERE id = :id AND status = 'draft'
  RETURNING *
  ```

  `UPDATE` phải chờ mọi `FOR SHARE` đang giữ dòng này kết thúc. Nghĩa là không có đơn nào "chen" được vào giữa lúc kiểm tra kỳ còn mở và lúc kỳ bị đóng: hoặc đơn được duyệt trước khi kỳ đóng (và được tính vào lương), hoặc bị từ chối với 409.

  Khi `lock` kỳ, service dùng `SELECT ... FOR UPDATE` trên dòng kỳ và từ chối nếu có đơn **approved** được quyết định sau `calculated_at` (phiếu lương đã cũ, cần tính lại).
- **Cách giải (b):** job Arq lấy khóa tư vấn mức session trên một kết nối riêng:

  ```sql
  SELECT pg_advisory_lock(:payroll_lock_key);   -- mặc định 7300001
  -- ... tính lương ...
  SELECT pg_advisory_unlock(:payroll_lock_key);
  ```

  Job thứ hai sẽ chờ ở dòng đầu tiên cho đến khi job thứ nhất nhả khóa. Phiếu lương ghi bằng upsert theo `UNIQUE (period_id, employee_id)` nên chạy lại không sinh bản ghi trùng. Nếu worker chết giữa chừng, lúc khởi động lại `recover_stuck_periods` đưa kỳ kẹt ở `calculating` về `draft`.
- **File:** `app/modules/payroll/guard.py` (`assert_period_open`), `app/modules/payroll/repository.py` (`status_for_share`, `begin_calculation`, `get_period_for_update`, `has_decisions_after`), `app/modules/payroll/service.py` (`start_calculation`, `lock_period`), `app/workers/payroll_job.py` (`calculate_payroll`), `app/workers/settings.py`.
- **Test:** `tests/test_requests.py::test_nv4_closed_period_rejects_create_and_approve`, `tests/test_payroll_job.py::test_nv4_approve_races_calculate` (duyệt và tính lương cùng lúc 8 lần: phiếu lương luôn khớp trạng thái cuối của đơn), `test_advisory_lock_serialises_jobs`, `test_job_waits_for_held_lock`, `test_two_jobs_same_period_do_not_duplicate`, `test_lock_is_released_after_job`, `tests/test_payroll.py::test_concurrent_period_create_and_calculate`, `tests/test_safe_writes.py::test_lock_refused_when_request_decided_after_calculation`, `test_stuck_calculating_period_reset_on_worker_start`.
- **Vì sao đặt trong DB:** khóa dòng và khóa tư vấn do Postgres cấp phát, có hiệu lực qua mọi process và mọi kết nối. Một biến cờ "đang tính" trong bộ nhớ của một process không ngăn được process khác. Lưu ý: khóa tư vấn mức session không dùng được qua PgBouncer ở chế độ transaction (xem `docs/07-kien-truc-va-thiet-ke.md`).

## NV-5. `attendance_events` chỉ INSERT

- **Vấn đề:** dữ liệu chấm công thô là bằng chứng. Nếu có thể sửa hoặc xóa thì không còn gì để đối chiếu khi có tranh chấp, và các bảng tổng hợp có thể bị lệch mà không biết.
- **Cách giải:** không có API nào sửa hoặc xóa sự kiện. Việc "sửa công" đi qua bảng `requests` (đơn điều chỉnh), và được áp vào lương lúc tính. Thêm một lớp phòng thủ ngay trong DB: trigger chặn mọi `UPDATE` và `DELETE`.

  ```sql
  CREATE OR REPLACE FUNCTION attendance_events_append_only() RETURNS trigger AS $$
  BEGIN
      RAISE EXCEPTION 'attendance_events is append-only';
  END;
  $$ LANGUAGE plpgsql;

  CREATE TRIGGER trg_attendance_events_append_only
  BEFORE UPDATE OR DELETE ON attendance_events
  FOR EACH ROW EXECUTE FUNCTION attendance_events_append_only();
  ```
- **File:** `app/modules/attendance/models.py` (`EVENTS_GUARD_FUNCTION`, `EVENTS_GUARD_TRIGGER`), `migrations/versions/0002_business_modules.py`.
- **Test:** `tests/test_constraints.py::test_nv5_trigger_blocks_update_and_delete` (kể cả xóa nhân viên kéo theo xóa sự kiện cũng bị chặn), `test_nv5_no_mutating_endpoint_for_events`, `test_nv5_no_code_updates_or_deletes_events` (quét mã nguồn tìm câu UPDATE/DELETE trên bảng này), `tests/test_requests.py::test_approving_adjustment_never_touches_events`.
- **Vì sao đặt trong DB:** code hôm nay không sửa sự kiện, nhưng code hôm sau, một script bảo trì hay một người cầm `psql` thì có thể. Trigger bảo vệ cả những trường hợp đó. Lưu ý: người có quyền superuser vẫn có thể tắt trigger, nên đây là hàng rào chống sai sót chứ không phải chống kẻ tấn công.

## NV-6. Ràng buộc nằm trong DB

- **Vấn đề:** validation chỉ ở code thì dễ bị bỏ sót khi có đường ghi mới (script, job, import).
- **Cách giải:** khai báo `UNIQUE`, khóa ngoại, `CHECK` ngay trong schema (qua model SQLAlchemy và migration). Một số ràng buộc điển hình:

  | Ràng buộc | Ý nghĩa |
  |---|---|
  | `employees.code` UNIQUE | Mã nhân viên không trùng. |
  | `ck_employees_role` | `role IN ('employee','manager','hr','admin')`. |
  | `ck_employees_version` | `version >= 1`. |
  | `ck_contracts_base_salary` | `base_salary > 0`. |
  | `ck_contracts_allowance` | `allowance >= 0`. |
  | `ck_sensitive_dependents` | `dependents >= 0`. |
  | `uq_schedules_emp_date` | Một nhân viên một ca mỗi ngày. |
  | `attendance_events.idempotency_key` UNIQUE | Nền cho NV-1. |
  | `uq_attendance_daily_emp_date`, `ck_daily_minutes` | Một dòng tổng hợp mỗi ngày, số phút không âm. |
  | `ck_requests_type`, `ck_requests_status`, `ck_requests_range` | Loại và trạng thái đơn hợp lệ, `end_at >= start_at`. |
  | `requests.idempotency_key` UNIQUE | Nền cho idempotency của đơn (mục "Chống mất request"). |
  | `uq_payroll_periods_year_month`, `ck_periods_status`, `ck_periods_month` | Một kỳ mỗi tháng, trạng thái và tháng hợp lệ. |
  | `uq_payslips_period_emp`, `ck_payslips_net` | Một phiếu lương mỗi người mỗi kỳ, lương thực nhận không âm. |

  Khóa ngoại nối các bảng với `employees` để không có bản ghi mồ côi.
- **File:** `app/modules/*/models.py`, `migrations/versions/`.
- **Test:** `tests/test_constraints.py::test_nv6_employees`, `test_nv6_money_and_dependents`, `test_nv6_attendance`, `test_nv6_requests`, `test_nv6_payroll`, `test_nv6_payslip_unique_and_net`. Mỗi test cố tình ghi dữ liệu sai bằng SQL trực tiếp và kỳ vọng DB từ chối.
- **Vì sao đặt trong DB:** DB là chốt cuối cùng mà mọi đường ghi đều phải đi qua. Dữ liệu sai một khi đã vào DB thì mọi báo cáo sau đó đều sai.

## Chống mất request

Mục tiêu: một lệnh ghi mà client đã gửi thì hoặc được ghi đúng một lần, hoặc client biết rõ nó thất bại. Không có trường hợp "client tưởng thành công mà DB không có", cũng không "ghi hai lần".

1. **Idempotency key cho mọi lệnh ghi.**
   - Chấm công: `idempotency_key` trong body (NV-1).
   - Tạo đơn: header `Idempotency-Key` (UUID) trên `POST /api/requests`. Gửi lại cùng key thì trả lại đơn đã tạo (cột `requests.idempotency_key` UNIQUE). Nếu cùng key mà **nội dung khác** thì trả 422: đó là lỗi của client chứ không phải thử lại. Cách này giống Stripe.
   - Duyệt hoặc từ chối đơn: tự nhiên idempotent nhờ `WHERE status = 'pending'`, và cùng người cùng kết quả gửi lại thì 200 (NV-3).
   - Sửa hồ sơ: phiên bản `version` đóng vai trò khóa. Gửi lại sau khi đã thành công sẽ nhận 409, client tải lại và thấy thay đổi đã có.
   - Chuyển kỳ sang `calculating`: `WHERE status = 'draft'` nên chỉ một lần thắng (NV-4).
2. **503 kèm `Retry-After` khi quá tải.** Hết pool kết nối, `statement_timeout`, `lock_timeout`, hết kết nối Postgres, Redis không có, hàng đợi băm mật khẩu đầy đều trả `503` với header `Retry-After: 2` (`RETRY_AFTER_SECONDS`) chứ không phải 500 trần. Với lỗi chuỗi hóa (serialization failure, deadlock), service tự thử lại tối đa 3 lần trước khi trả 503. Rate limit trả `429` kèm `Retry-After` là số giây còn lại của cửa sổ. File: `app/core/errors.py`, `app/core/retry.py`, `app/core/ratelimit.py`.
3. **Retry ở client.** Client đọc `Retry-After`, chờ, rồi **gửi lại với cùng key**. Chính vì key không đổi nên thử lại vô hại. Mẫu trong `WriterUser._write` của `loadtest/locustfile.py`: thử lại khi 502, 503, 504, lỗi kết nối hoặc timeout, tối đa `RETRY_MAX` lần, chờ nhân đôi mỗi lần (`RETRY_BACKOFF_MS`) có nhiễu ngẫu nhiên, và tôn trọng `Retry-After` (tối đa `RETRY_AFTER_CAP_S`).
4. **Outbox.** Khi lệnh ghi còn phải gửi thông báo hoặc đẩy sang hệ thống khác, đừng làm "ghi DB rồi gửi message" ở hai nơi (một trong hai có thể hỏng). Ghi message vào một bảng `outbox` **trong cùng transaction** với dữ liệu, một tiến trình khác đọc bảng đó rồi gửi, và bên nhận phải idempotent. Demo hiện chưa có luồng nào cần outbox. Việc gần nhất là xếp job tính lương vào Arq: nếu xếp job thất bại thì `start_calculation` trả kỳ về `draft` và trả 503 thay vì để kẹt (`app/modules/payroll/service.py`). Mẫu outbox chi tiết xem phần "Mang sang dự án khác" bên dưới và `docs/07-kien-truc-va-thiet-ke.md`.
5. **Duyệt đơn idempotent.** Xem NV-3: gửi lại cùng quyết định bởi cùng người nhận 200 chứ không 409, nên timeout giữa chừng không làm client hiểu nhầm là thất bại.
6. **Đối soát sau load test.** Mỗi lệnh ghi trong `write_storm` và `write_ramp` có một key và được ghi vào `writes.jsonl`. Sau khi chạy, `reconcile.py` so với các key trong DB (`app_reconcile.sql`): lệnh được báo thành công mà không có trong DB là **mất**, key xuất hiện hai lần là **trùng**, cả hai phải bằng 0. Lệnh cuối cùng vẫn lỗi thì được gửi lại với cùng key và kiểm tra lần nữa. Chi tiết ở `docs/02-load-test.md`.

## Mang sang dự án khác

Các mẫu dưới đây không phụ thuộc chấm công hay lương. Mỗi mẫu gồm khi nào dùng, mẫu SQL, và lỗi hay gặp.

### Idempotency key

- **Dùng khi:** thao tác tạo mà client có thể gửi lại (thanh toán, đặt hàng, chấm công).
- **Mẫu:**

  ```sql
  ALTER TABLE orders ADD COLUMN idempotency_key uuid UNIQUE;

  INSERT INTO orders (customer_id, idempotency_key, ...)
  VALUES (:customer, :key, ...)
  ON CONFLICT (idempotency_key) DO NOTHING
  RETURNING id;
  -- không trả dòng nào: đọc lại đơn cũ theo key và trả lại nó
  ```
- **Cần thêm:** kiểm tra key thuộc đúng chủ (nếu không sẽ lộ dữ liệu người khác), và so sánh tham số với lần đầu, khác thì báo lỗi 422 (như Stripe). Nếu cần giữ key có hạn, thêm cột thời gian và dọn định kỳ; ở demo key được giữ vĩnh viễn cùng bản ghi.

### Optimistic locking

- **Dùng khi:** nhiều người sửa cùng một bản ghi, ít xung đột (nếu xung đột thường xuyên thì cân nhắc khóa bi quan).
- **Mẫu:**

  ```sql
  ALTER TABLE profiles ADD COLUMN version int NOT NULL DEFAULT 1 CHECK (version >= 1);

  UPDATE profiles SET name = :name, version = version + 1
  WHERE id = :id AND version = :version;
  -- 0 dòng: trả 409, client tải lại rồi thử lại
  ```
- **Cần thêm:** luôn trả `version` mới cho client; mọi `UPDATE` của bản ghi đó phải tăng version, nếu không khóa bị thủng.

### Conditional update (chuyển trạng thái)

- **Dùng khi:** một bản ghi đi qua các trạng thái và chỉ một bên được chuyển (duyệt, hủy, thanh toán).
- **Mẫu:**

  ```sql
  UPDATE tickets SET status = 'closed', closed_by = :actor
  WHERE id = :id AND status = 'open'
  RETURNING *;
  -- 0 dòng: đã có người xử lý, trả 409
  ```
- **Cần thêm:** `CHECK (status IN (...))` để trạng thái luôn hợp lệ, và quyết định rõ trường hợp "cùng người gửi lại" trả 200 hay 409.

### Advisory lock

- **Dùng khi:** cần mỗi lúc chỉ một tiến trình làm một việc nhưng không có dòng nào để khóa (job định kỳ, tính toán nặng).
- **Mẫu:**

  ```sql
  SELECT pg_advisory_lock(7300001);          -- chờ đến lượt
  -- ... làm việc trên CÙNG kết nối này ...
  SELECT pg_advisory_unlock(7300001);
  ```
  Dạng không chờ: `SELECT pg_try_advisory_lock(7300001);` trả `false` nếu có người đang giữ.
- **Cần thêm:** khóa mức session gắn với kết nối, nên phải dùng đúng một kết nối cho cả khóa và việc làm, và nhả khóa trong `finally`. Nếu không chắc nhả được, hãy hủy kết nối thay vì trả vào pool (demo làm vậy). Không dùng được qua PgBouncer ở chế độ transaction; khi đó dùng `pg_advisory_xact_lock` (nhả khi hết transaction) hoặc kết nối thẳng DB cho job này.

### Bảng chỉ insert (append-only)

- **Dùng khi:** dữ liệu là bằng chứng hoặc sổ cái (chấm công, giao dịch tiền, nhật ký).
- **Mẫu:** trigger chặn `UPDATE` và `DELETE` như NV-5. Sai sót thì không sửa mà ghi một dòng đảo/điều chỉnh mới (ví dụ qua bảng đơn điều chỉnh). Bảng tổng hợp luôn tính lại được từ bảng gốc.
- **Cần thêm:** kế hoạch cho việc xóa dữ liệu theo luật (ví dụ quyền xóa dữ liệu cá nhân): trigger sẽ chặn cả việc đó, nên cần một đường xử lý riêng có kiểm soát.

### Outbox (khi cần gửi thông báo)

- **Dùng khi:** ghi DB xong phải gửi message sang nơi khác (email, hàng đợi, dịch vụ khác) mà không được mất hay gửi sai.
- **Mẫu:**

  ```sql
  BEGIN;
    UPDATE orders SET status = 'paid' WHERE id = :id;
    INSERT INTO outbox (id, topic, payload, created_at) VALUES (gen_random_uuid(), 'order.paid', :json, now());
  COMMIT;
  -- một tiến trình riêng: đọc outbox chưa gửi, gửi, rồi đánh dấu đã gửi
  ```
- **Cần thêm:** bên nhận phải xử lý được message trùng (vì relay có thể gửi lại), nên gắn `id` của outbox làm khóa idempotency ở bên nhận.
