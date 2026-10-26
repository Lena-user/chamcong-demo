# 05. Kịch bản demo (khoảng 15 phút)

Dành cho người trình bày trước thầy hướng dẫn. Mục tiêu của buổi demo: chứng minh hệ thống chịu được khoảng 100 request đồng thời, chỉ ra giới hạn nằm ở đâu bằng số đo, và cho thấy các lệnh ghi không bị mất hay trùng.

## 0. Chuẩn bị trước buổi demo

Làm trước ít nhất 30 phút, không làm trước mặt thầy.

1. Cắm sạc, tắt các ứng dụng nặng, đóng bớt tab trình duyệt.
2. Bật Docker Desktop, rồi trong WSL: `docker compose up -d --build` và chờ `docker compose ps` báo `api`, `db`, `redis` healthy.
3. Seed dữ liệu mới: `docker compose exec api python -m scripts.seed`.
4. Kiểm tra `.env`: `RATE_LIMIT_ENABLED=false` nếu sẽ chạy load test trực tiếp. Nếu cho thầy dùng web qua tunnel thì phải là `true` (xem mục 3 bước 7).
5. **Chạy sẵn** các lượt đo `steady`, `ramp`, `login_storm`, `write_storm` và mở sẵn các `report.md` cùng biểu đồ trong thư mục `loadtest/results/`. Load test thật chạy vài phút, không nên chờ trước mặt thầy. Trong buổi demo chỉ chạy trực tiếp một lượt ngắn (ví dụ 60 giây) để chứng minh nó thật.
6. Mở sẵn trong trình duyệt: web (http://localhost:8000), các biểu đồ `chart_*.png` và `report.md` đã chạy. Mở sẵn trong editor: `app/modules/attendance/repository.py`, `app/modules/requests/repository.py`, `tests/test_requests.py`.
7. Nếu dự định demo qua tunnel: bật tunnel **sau cùng** và chỉ khi cần (xem `docs/02-load-test.md` mục 7). Mặc định thì không bật.
8. Số đo sẽ trình bày: bảng ở mục 2 của `docs/08-ket-qua-do.md`. Đo lại một lượt trên máy demo trước buổi trình bày.

## 1. Kịch bản theo thời gian

| Phút | Nội dung | Mở gì / lệnh gì |
|---|---|---|
| 0 đến 2 | Giới thiệu bài toán và mục tiêu | `README.md` (phần giới thiệu, sơ đồ kiến trúc) |
| 2 đến 4 | Kiến trúc và giới hạn tài nguyên | `docs/07-kien-truc-va-thiet-ke.md` (sơ đồ), `docker-compose.yml` (`deploy.resources.limits`) |
| 4 đến 6 | Chạy web thật | http://localhost:8000, đăng nhập `E0001`, chấm công, xem công |
| 6 đến 9 | Load test: chạy ngắn và đọc báo cáo | `./loadtest/run.sh custom`, rồi `report.md` và 3 biểu đồ đã chạy sẵn |
| 9 đến 11 | Điểm gãy và điểm nghẽn | Biểu đồ `ramp`, bảng "Điểm gãy" và "CPU và RAM cao nhất" |
| 11 đến 13 | Ghi đúng, đủ, không mất | `write_storm` và `reconcile.md`; mở `app/modules/attendance/repository.py` |
| 13 đến 14 | Chống tranh chấp và sao lưu | `tests/test_requests.py::test_nv3_concurrent_approve`, `scripts/backup/drill.sh` |
| 14 đến 15 | Kết luận, đường lên production, hỏi đáp | `docs/07-kien-truc-va-thiet-ke.md` mục 4 |

### Phút 0 đến 2. Giới thiệu

Nói: "Đây là web chấm công và tính lương, nhưng trọng tâm là khả năng chịu tải và chống sai dữ liệu khi nhiều người cùng ghi. Mục tiêu là 100 request đồng thời với p95 dưới 500 ms và lỗi dưới 1%. Toàn bộ dữ liệu là dữ liệu giả."

Chỉ vào sơ đồ ở `README.md`: tunnel (tùy chọn) vào api (3 worker), api nói chuyện với PostgreSQL và Redis, worker nền tính lương.

### Phút 2 đến 4. Kiến trúc và giới hạn

Mở `docker-compose.yml`, chỉ vào `deploy.resources.limits`: api 2 CPU, db 1 CPU, worker 1 CPU, redis 0,5 CPU. Nói: "Giới hạn được đặt cố định để số đo có ý nghĩa và lặp lại được, và để biết thành phần nào nghẽn trước."

Mở sơ đồ luồng ghi ở `docs/07-kien-truc-va-thiet-ke.md` mục 1. Nói: "Lệnh ghi được commit vào PostgreSQL rồi mới trả lời client. Em không đặt hàng đợi phía trước để không có chuyện báo thành công nhưng chưa ghi."

### Phút 4 đến 6. Web thật

Đăng nhập `E0001` bằng `SEED_PASSWORD`. Bấm chấm công, xem bảng công của mình. Nói: "Mỗi lần bấm sinh một mã duy nhất. Nếu mạng chập chờn và web gửi lại, hệ thống chỉ ghi một lần." (Có thể bấm hai lần liên tiếp và chỉ cho thầy trường `duplicate` trong phản hồi nếu mở công cụ mạng của trình duyệt.)

### Phút 6 đến 9. Load test

Chạy một lượt ngắn trước mặt thầy (khoảng 60 giây):

```bash
USERS=100 SPAWN_RATE=50 DURATION=60 ./loadtest/run.sh custom
```

Trong lúc chờ, giải thích: "100 người dùng giả đăng nhập rồi chấm công, xem công, tạo đơn, quản lý duyệt đơn. Hai bộ lấy mẫu chạy song song: CPU và RAM của từng container, và hoạt động của Postgres."

Khi xong, mở `report.md` vừa tạo, đọc theo thứ tự: bảng "Mục tiêu so với thực tế" (đạt hay không), bảng theo endpoint, rồi "CPU và RAM cao nhất". Khi chỉ vào biểu đồ:

- **`chart_latency.png`:** "Đường xanh là p50, đường đỏ là p95, nét đứt là mục tiêu 500 ms. Khoảng cách giữa hai đường cho biết có hàng đợi hay không. Hai đường sát nhau là hệ thống thoải mái."
- **`chart_rps.png`:** "Đây là số request xử lý được mỗi giây, đường đỏ là lỗi mỗi giây. Khi đường xanh đi ngang mà số user còn tăng thì đã bão hòa."
- **`chart_cpu.png`:** "Mỗi đường là một container so với giới hạn của nó. Container nào chạm trần là điểm nghẽn."

Nếu lượt đo trực tiếp không đạt mục tiêu: đừng né tránh. Nói thẳng con số, rồi chỉ vào container đang chạm trần để giải thích nguyên nhân. Điều thầy cần thấy là em hiểu được vì sao.

### Phút 9 đến 11. Điểm gãy và điểm nghẽn

Mở biểu đồ và báo cáo của `ramp` (chạy sẵn). Chỉ bảng "Điểm gãy": "Tăng dần 50, 100, 150, 200, 300 người dùng. Bậc nào đầu tiên vi phạm mục tiêu thì đó là điểm gãy. Sức chịu tải an toàn là bậc ngay trước đó." Con số đã đo: tới 300 người dùng p95 vẫn chỉ 170 ms, gãy ở 400 người dùng (p95 1,7 giây). Ở 600 người dùng thì 1,6% request bị trả 503 có kiểm soát. Container nghẽn đầu tiên là api (216% trên 200% CPU), sau đó là db (96% trên 100%).

Chỉ vào dòng "Điểm nghẽn" và nói container nào. Ví dụ nếu là api: "CPU của api chạm trần 200% trong khi db còn dư, vậy muốn chịu thêm tải thì tăng CPU cho api hoặc giảm việc tốn CPU trong request, chưa cần đụng đến database." Nếu dòng `locust` chạm trần thì nói rõ: "máy bắn tải cũng đang nghẽn, nên số đo ở bậc đó chỉ là cận dưới."

Nói thêm về `login_storm`: "Đăng nhập là thao tác tốn CPU nhất vì băm mật khẩu có chủ đích chậm. Em đo riêng để thấy giới hạn của nó." Số đo: 29 lần đăng nhập mỗi giây, p95 5,1 giây, 0 lỗi. Trước khi sửa là 48% lỗi 500 và RAM api 2.043/2.048 MiB.

### Phút 11 đến 13. Ghi đúng, đủ, không mất

Mở `report.md` hoặc `reconcile.md` của `write_storm`. Nói: "Trong kịch bản này 100 người chỉ ghi, không nghỉ. Mỗi lệnh ghi có một mã duy nhất. Sau khi chạy, em đối chiếu những lệnh mà client được báo thành công với những gì nằm trong database. Kết quả: mất bao nhiêu, trùng bao nhiêu." Con số đã đo: 46.119 lệnh ghi trong 2 phút, tất cả được xác nhận, 0 mất, 0 trùng. Nếu quá tải thì hệ thống trả 503 kèm `Retry-After`, client chờ rồi gửi lại cùng mã, nên không mất cũng không trùng.

**Cho thầy thấy 100 user đang làm gì.** Trước khi chạy `write_storm`, mở `http://localhost:8000/monitor.html` (đăng nhập bằng tài khoản HR `E0002` ở trang chính trước). Trang tự làm mới mỗi giây và hiện:
- số lượt chấm công mỗi giây;
- số nhân viên **khác nhau** đã chấm trong 60 giây: khi chạy 100 user thì con số này lên đúng 100;
- biểu đồ từng giây;
- danh sách tên người vừa chấm.

Nói: "Mỗi user giả lập đăng nhập bằng một tài khoản thật, E0014 trở đi, và ghi dữ liệu thật vào database. Đây là những gì database đang nhận được." Đo thật: 100 nhân viên, khoảng 216 lượt chấm công mỗi giây, hơn 12 nghìn lượt mỗi phút.

Mở `app/modules/attendance/repository.py`, chỉ vào `insert_event` và câu `ON CONFLICT DO NOTHING`. Nói: "Ràng buộc UNIQUE nằm trong database. Kể cả khi 20 request cùng mã tới cùng lúc ở nhiều process, đúng một cái được ghi."

### Phút 13 đến 14. Tranh chấp và sao lưu

Mở `tests/test_requests.py`, đến `test_nv3_concurrent_approve`. Nói: "Mười quản lý cùng bấm duyệt một đơn. Test kiểm tra: đúng một người nhận 200, chín người nhận 409, và trong database chỉ có một người duyệt." Có thể chạy riêng test này nếu muốn: `docker compose run --rm api pytest -q tests/test_requests.py -k nv3`.

Nói về sao lưu: "Có hai lớp: dump mỗi 30 phút và lưu WAL liên tục để quay về thời điểm bất kỳ. Em có script diễn tập tự động." Nếu đã chạy sẵn `scripts/backup/drill.sh`, đưa kết quả PASS ra. **Đừng chạy diễn tập trước mặt thầy** vì nó tốn vài phút và RAM.

### Phút 14 đến 15. Kết luận

Nói ba ý: (1) đạt hay chưa đạt mục tiêu 100 request đồng thời, kèm con số; (2) giới hạn thật nằm ở đâu; (3) những gì làm tiếp khi lên production (bản sao, PgBouncer, giới hạn đồng thời kiểu Stripe, multi-AZ), xem `docs/07-kien-truc-va-thiet-ke.md` mục 4.

## 2. Câu hỏi thầy có thể hỏi và gợi ý trả lời

**1. Vì sao kịch bản đăng nhập ồ ạt không đạt (hoặc chậm)?**
Vì băm mật khẩu (argon2id) cố ý tốn CPU và bộ nhớ để kẻ tấn công không thử được hàng triệu mật khẩu mỗi giây. Với 2 CPU, mỗi lần xác minh khoảng 38 ms (đã đo: 150 ms giảm còn 38 ms khi dùng mức tối thiểu của OWASP), nên giới hạn lý thuyết là vài chục lần đăng nhập mỗi giây. 100 người đăng nhập đồng thời phải xếp hàng. Em không hạ độ mạnh của hash để lấy số đẹp. Thay vào đó em giới hạn số hash đồng thời để không hết RAM, giới hạn hàng chờ (đầy thì trả 503 kèm Retry-After), và cấp token sống 12 giờ để người dùng hiếm khi phải đăng nhập lại. Trong thực tế, đăng nhập ồ ạt chỉ xảy ra đầu ca, và có thể rải ra bằng cách khác. Số đo: 29 lần đăng nhập mỗi giây, p95 5,1 giây, 0 lỗi, RAM api 367 MiB (trước khi sửa: 48% lỗi 500, RAM 2.043 MiB).

**2. Muốn chịu 1000 người dùng thì làm gì?**
Làm theo thứ tự do số đo chỉ ra, không đoán. (a) Xác định container nghẽn trước: nếu là api thì thêm CPU hoặc thêm bản api chạy song song sau một bộ cân bằng tải; nếu là db thì xem truy vấn chậm (`pg_top_queries.csv`), thêm index, rồi mới tăng CPU. (b) Thêm bộ gộp kết nối (PgBouncer) vì nhiều process api sẽ vượt `max_connections`; lưu ý khóa tư vấn mức session không chạy ở chế độ transaction. (c) Thêm bản sao (replica) cho truy vấn đọc và báo cáo. (d) Cache dữ liệu đọc nhiều. (e) Giới hạn đồng thời và load shedder để quá tải thì từ chối có kiểm soát. (f) Đo lại bằng chính bộ load test này với `RAMP_STEPS` lớn hơn. Chi tiết ở `docs/07-kien-truc-va-thiet-ke.md` mục 4 và `docs/03-ap-dung-cho-du-an-khac.md` mục 4.

**3. Vì sao tách bảng dữ liệu nhạy cảm (`employee_sensitive`)?**
Để giảm rủi ro và phân quyền dễ hơn. Số CCCD, tài khoản ngân hàng và mã số thuế nằm ở bảng riêng, chỉ `hr` và `admin` đọc được, và mỗi lần đọc đều ghi vào `audit_logs`. Các truy vấn thông thường (danh sách nhân viên, chấm công) không bao giờ chạm vào bảng này, nên không thể vô tình lộ ra qua một API khác. Bảng riêng cũng cho phép mã hóa cột, sao lưu hay giới hạn quyền truy cập khác với dữ liệu thường.

**4. Nếu hai người cùng sửa một hồ sơ thì sao?**
Dùng khóa lạc quan: mỗi hồ sơ có số phiên bản. Người lưu sau mang phiên bản cũ nên bị từ chối với 409 và phải tải lại. Test `test_nv2_concurrent_patch_same_version` bắn 10 request cùng phiên bản, kết quả đúng một 200 và chín 409.

**5. Nếu máy chủ chết giữa chừng thì có mất dữ liệu không?**
Giao dịch đã commit thì đã được ghi xuống đĩa (em không tắt `fsync` hay `synchronous_commit`), và khi báo thành công cho client là đã commit. Request đang xử lý dở thì client không nhận được trả lời, nên sẽ thử lại với cùng mã, không bị ghi trùng. Nếu hỏng ổ đĩa thì khôi phục từ sao lưu: mất tối đa khoảng 5 phút với PITR hoặc 30 phút với dump. Nếu mất cả máy thì chỉ khôi phục được khi đã chép `backups/` ra nơi khác (quy tắc 3-2-1). Xem `docs/06-sao-luu-khoi-phuc.md`.

**6. Vì sao không dùng hàng đợi (Redis, Kafka) trước database để chịu tải tốt hơn?**
Vì hàng đợi trước database tạo khoảng trống "đã báo thành công nhưng chưa ghi". Nếu hàng đợi mất dữ liệu thì mất request đã được xác nhận, trái với mục tiêu không mất request. Em chọn ghi đồng bộ vào database rồi mới trả lời. Quá tải thì từ chối có kiểm soát bằng 503 và client thử lại. Nếu cần hấp thụ đỉnh tải thì dùng hàng đợi bền kèm outbox và trả `202`. Xem ADR-8 ở `docs/07-kien-truc-va-thiet-ke.md`.

**7. Số đo của em có đáng tin không? Máy bắn tải có làm sai kết quả?**
Em kiểm tra điều đó. Máy bắn tải cũng bị giới hạn tài nguyên và được lấy mẫu CPU như các container khác. Báo cáo nêu rõ nếu máy bắn tải chạm trần, và khi đó số đo chỉ là cận dưới. Em chạy nhiều lần, dùng cùng dữ liệu seed, và ghi cấu hình, commit và giới hạn tài nguyên vào `meta.json` của mỗi lần chạy. Số đo chính trong mạng Docker; qua tunnel chỉ để tham khảo vì Quick Tunnel giới hạn 200 request đang xử lý và độ trễ phụ thuộc mạng nhà.

**8. Vì sao đo thấy chậm mà CPU thấp?**
Request đang chờ chứ không tính toán: chờ kết nối từ pool, chờ khóa dòng, hoặc chờ I/O. Em xem biểu đồ hoạt động của Postgres (`chart_db.png`) để thấy các phiên đang chờ cái gì, và log api có `503 db pool timeout` không. Bảng "Triệu chứng, nguyên nhân" ở `docs/03-ap-dung-cho-du-an-khac.md` mục 4 liệt kê các trường hợp.

**9. Quy tắc tính lương này có đúng luật không?**
Không. Đây là giá trị mẫu để demo (lương theo công chia 26 ngày, OT 150%, BHXH 8%, BHYT 1,5%, BHTN 1%, giảm trừ gia cảnh và thuế lũy tiến 5 bậc), được ghi chú rõ là cần kế toán xác nhận. Quan trọng là các tham số nằm trong bảng `payroll_rules` chứ không cứng trong code, nên đổi quy tắc không cần sửa chương trình và mỗi phiếu lương ghi rõ phiên bản quy tắc đã dùng.

**10. Điều gì xảy ra nếu duyệt đơn đúng lúc đang tính lương?**
Khi duyệt đơn, hệ thống khóa dòng của kỳ lương ở chế độ chia sẻ (`SELECT ... FOR SHARE`) trong cùng giao dịch, còn lệnh chuyển kỳ sang "đang tính" là một `UPDATE` nên phải chờ giao dịch duyệt xong. Kết quả: hoặc đơn được duyệt trước rồi được tính vào lương, hoặc bị từ chối với 409 vì kỳ đã đóng. Không có trường hợp đơn "lọt" giữa chừng. Test `test_nv4_approve_races_calculate` chạy 8 vòng và kiểm tra phiếu lương luôn khớp trạng thái cuối của đơn.

## 3. Lưu ý trong lúc demo

1. Đừng chạy hai thứ nặng cùng lúc (load test và diễn tập sao lưu, hoặc load test và pytest). Số đo sẽ sai.
2. Đừng seed lại giữa chừng khi đang chạy `write_storm` (đối soát sẽ báo mất giả).
3. Nếu có sự cố khi demo trực tiếp, có sẵn kết quả đã chạy trước: mở `loadtest/results/` của lần chạy tốt nhất.
4. Nếu thầy muốn thử web qua tunnel, tunnel đưa hệ thống ra internet công khai. Đặt `RATE_LIMIT_ENABLED=true` trong `.env`, chạy `docker compose up -d --no-deps api`, rồi mới bật tunnel (`docker compose --profile tunnel up -d tunnel`). Xong thì tắt ngay (`docker compose --profile tunnel stop tunnel`). Sau khi tắt, nếu cần chạy lại load test thì đặt lại `RATE_LIMIT_ENABLED=false`.
5. Chỉ dùng dữ liệu giả. Không nhập thông tin thật vào web.
