# TASK-003: Module nghiệp vụ, test đồng thời, frontend tối giản

- **Giao cho:** coder
- **Trạng thái:** Đang làm
- **Liên quan:** TASK-001 (nền móng đã xong), PROJECT.md mục 2, 4, 5, 6

## Mục tiêu
Hoàn thiện toàn bộ endpoint trong hợp đồng API, kèm test đồng thời cho NV-1 đến NV-6 và một frontend tối giản để demo bằng tay.

## Đầu vào
- Code của TASK-001 trong `~/projects/chamcong-demo`: core, models, migration, auth và seed. Đọc kỹ `app/core/` và `app/modules/employees/` để theo đúng style.
- Hợp đồng API ở PROJECT.md mục 6. Quy tắc ở mục 2.

## Đầu ra mong muốn
1. `employees`:
   - `GET /api/employees`: phân trang, lọc theo `department`, chỉ manager trở lên.
   - `PATCH /api/employees/{id}`: optimistic locking theo NV-2, ghi audit.
   - `GET /api/employees/{id}/sensitive`: chỉ hr và admin, ghi audit.
2. `attendance`:
   - `POST /api/attendance/check`: theo NV-1, chỉ dùng 1–2 query.
     - Lần đầu trả 201. Gửi trùng key thì trả 200 với `duplicate=true` và đúng event cũ.
     - Key trùng nhưng là của **nhân viên khác** thì trả 409.
     - Sau khi insert, cập nhật `attendance_daily` của ngày đó bằng một câu upsert (`ON CONFLICT (employee_id, work_date) DO UPDATE`) tính từ events, trong cùng transaction ngắn.
   - `GET /api/attendance/me`.
   - Có hàm service `rebuild_daily(employee_id, work_date)` để tính lại một ngày từ events.
   - Ca đêm qua 0h tính vào ngày bắt đầu ca. Dùng múi giờ `Asia/Ho_Chi_Minh`.
3. `requests`:
   - Tạo đơn, `GET /api/requests/me`, `GET /api/requests` (manager chỉ thấy đơn của phòng mình).
   - Approve và reject theo NV-3.
   - Khi tạo và khi duyệt, gọi `payroll` service để kiểm tra kỳ theo NV-4 (`SELECT ... FOR SHARE` trên dòng kỳ trong cùng transaction). Không truy vấn thẳng bảng của payroll.
   - Duyệt đơn `adjustment` không được sửa `attendance_events`. Thông tin công đã duyệt phải được dùng khi tính lương.
4. `payroll`:
   - Tạo kỳ.
   - `calculate`: `UPDATE ... SET status='calculating' WHERE id=:id AND status='draft'`, 0 dòng thì 409. Sau đó enqueue job Arq và trả 202 kèm `job_id`.
   - `GET /api/payroll/jobs/{job_id}`.
   - `lock`.
   - `GET /api/payroll/periods/{id}/payslips/me`: ghi audit.
   - Tạo kỳ hoặc gọi `calculate` hai lần đồng thời thì chỉ một lần thành công.
5. `app/workers/`:
   - Job `calculate_payroll(period_id)`:
     - Lấy `pg_advisory_lock` (hoặc `pg_try_advisory_lock` rồi retry) theo một khóa cố định.
     - Tính theo quy tắc mẫu ở PROJECT.md mục 2, dùng `payroll_rules` hiệu lực của kỳ, tiền là số nguyên.
     - Ghi `payslips` bằng `ON CONFLICT (period_id, employee_id) DO UPDATE`, kèm `rule_version`.
     - Xong thì kỳ quay về `draft`, đánh dấu là đã có payslip (thêm cột `calculated_at` nếu cần, kèm migration mới). Hoặc giữ ở `calculating` cho đến khi gọi `lock`. Chọn cách đơn giản, ghi rõ trong báo cáo.
     - Lỗi thì trả kỳ về `draft`.
   - Hàm tính thuế và bảo hiểm phải là hàm thuần, có unit test.
6. Test đồng thời trong `tests/`, mỗi quy tắc ít nhất một test, dùng `asyncio.gather` rồi kiểm tra DB:
   - NV-1: 20 request cùng key → đúng 1 dòng event, mọi response trả cùng `event_id`.
   - NV-2: 10 PATCH cùng version → đúng 1 cái 200, 9 cái 409, `version` trong DB tăng đúng 1.
   - NV-3: 10 approve cùng đơn → 1 cái 200, 9 cái 409, `approved_by` là người thắng.
   - NV-4: kỳ đang `calculating` thì tạo và duyệt đơn trong kỳ đều 409. Approve chạy đua với calculate thì kết quả cuối phải nhất quán. Hai job tính lương chạy cùng lúc thì không nhân đôi payslip, và advisory lock thật sự tuần tự hóa (gọi thẳng hàm job).
   - NV-5: không có endpoint hay service nào UPDATE/DELETE `attendance_events`. Thêm trigger DB chặn UPDATE/DELETE trên bảng này (migration mới) và viết test xác nhận trigger chặn được.
   - NV-6: insert vi phạm CHECK/UNIQUE ở tầng DB bị từ chối.
   - Thêm test RBAC: employee gọi endpoint của hr thì nhận 403, manager không thấy đơn của phòng khác.
   - Lưu ý: test đồng thời cần mỗi request một session riêng, nên dùng engine thật với pool, đừng dùng một connection chung.
7. `app/static/`: một trang `index.html` cùng `app.js` và `style.css`, JS thuần, chạy được ở khổ 360px.
   - Màn đăng nhập.
   - Nút "Chấm công" (tạo UUID phía client bằng `crypto.randomUUID()`) và lịch sử công của tôi.
   - Tạo đơn.
   - Nếu là manager trở lên: danh sách đơn chờ duyệt, có nút duyệt/từ chối. Nhận 409 thì báo "Đơn đã được người khác xử lý".
   - Không hiện tài khoản demo trên trang. JWT lưu trong `sessionStorage`.

## Tiêu chí nghiệm thu
- [ ] Mọi endpoint ở mục 6 của PROJECT.md chạy đúng mã trạng thái.
- [ ] `docker compose run --rm api pytest -q` pass toàn bộ, gồm test cho NV-1 đến NV-6 và RBAC. Chạy 3 lần liền đều pass (không chập chờn).
- [ ] Sau seed: login `E0004` (manager), thấy đơn pending của phòng mình, duyệt được.
- [ ] HR gọi `calculate` cho kỳ draft → 202, sau vài giây job xong và có payslip cho mọi nhân viên đang làm.
- [ ] Mỗi endpoint chạy tối đa 3 query (bật echo SQL thử một vòng để kiểm). Không có N+1.
- [ ] Không có code blocking trong async.
- [ ] Code, tên biến và comment viết bằng tiếng Anh. Chữ hiển thị trên giao diện bằng tiếng Việt.

- Bộ load test gửi đơn OT dạng `{"type":"ot","work_date":"YYYY-MM-DD","start_at":"<ISO +07:00>","end_at":"<ISO +07:00>","reason":"..."}`, nên API phải nhận đúng định dạng này. Xem `loadtest/locustfile.py` để khớp payload của mọi endpoint.

## Không được đụng
- `team/`, `loadtest/`, `docs/`.
- Không đổi hợp đồng API (path, field, mã trạng thái), vì bộ load test viết theo đúng hợp đồng đó. Nếu buộc phải đổi thì ghi vào báo cáo.
- Không chạy `pkill -f`. Không commit, không push, không bật profile `tunnel`.

## Mục PROJECT.md cần đọc
Mục 2, 5, 6.

---
## Kết quả và review của leader
