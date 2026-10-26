# 06. Sao lưu và khôi phục

Mọi lệnh chạy từ thư mục gốc của repo, trong terminal WSL, khi stack đã chạy (`docker compose up -d`). Dữ liệu trong demo là dữ liệu giả, nhưng quy trình được dựng như cho dữ liệu thật để dùng lại cho dự án khác.

## 1. Hai lớp sao lưu và khi nào dùng lớp nào

Service `backup` chạy nền cùng stack và tự làm cả hai lớp:

| Lớp | Là gì | Tần suất mặc định | Giữ lại | Dùng khi |
|---|---|---|---|---|
| 1. Dump logic | `pg_dump -Fc` toàn bộ database, mỗi bản là một file `.dump` | mỗi 30 phút (`BACKUP_INTERVAL_MINUTES`) | 48 bản (`BACKUP_KEEP_DUMPS`) | Khôi phục nguyên trạng một thời điểm đã dump: lỡ xóa dữ liệu mà biết rõ đã xóa lúc nào, hoặc chuyển dữ liệu sang máy khác. Đơn giản và dễ kiểm tra. |
| 2. Base backup và lưu trữ WAL (PITR) | Bản sao vật lý toàn bộ cụm Postgres (`pg_basebackup`), cộng mọi file WAL đã lưu sau đó | base mỗi 24 giờ (`BASEBACKUP_INTERVAL_HOURS`), WAL lưu liên tục và ép chuyển file ít nhất mỗi 300 giây (`WAL_ARCHIVE_TIMEOUT`) | 3 base (`BACKUP_KEEP_BASE`), WAL cũ hơn base cũ nhất bị xóa | Khôi phục về **một thời điểm bất kỳ** (PITR, point-in-time recovery), ví dụ ngay trước câu lệnh xóa nhầm. |

Chọn nhanh: biết giờ xảy ra sự cố và muốn quay về đúng trước đó thì dùng PITR. Chỉ cần quay về bản gần nhất, hoặc PITR không dùng được, thì dùng dump.

### RPO và RTO thực tế

- **RPO** (mất tối đa bao nhiêu dữ liệu): với dump là tối đa khoảng 30 phút. Với PITR là tối đa khoảng 5 phút (`archive_timeout=300`: Postgres ép đóng file WAL ít nhất mỗi 300 giây, và chỉ file WAL đã đóng và đã lưu mới dùng được). Nếu chỉ còn dữ liệu trong volume mà chưa lưu WAL thì phần chưa lưu mất. Muốn RPO thấp hơn thì giảm `WAL_ARCHIVE_TIMEOUT` (tốn nhiều file WAL hơn) hoặc dùng bản sao đồng bộ (xem `docs/07-kien-truc-va-thiet-ke.md`).
- **RTO** (mất bao lâu để chạy lại): đo bằng `drill.sh` ngày 2026-10-26 với dữ liệu seed (300 nhân viên, 45 nghìn lượt chấm công, dump 1,8 MB): PITR khoảng 18 giây (gồm cả nén bản dữ liệu hiện tại để an toàn), khôi phục từ dump khoảng 3 giây. Toàn bộ diễn tập mất 73 giây. Thời gian này tăng theo kích thước dữ liệu và lượng WAL phải phát lại, nên đo lại bằng diễn tập (mục 4) mỗi khi dữ liệu lớn lên đáng kể.

## 2. Nơi lưu file và quy tắc 3-2-1

Mọi file sao lưu nằm ở thư mục `BACKUP_DIR` (mặc định `./backups`, đã gitignore):

```
backups/
  dump/          chamcong-YYYYmmdd-HHMMSS.dump   (giờ theo TIMEZONE, mặc định Asia/Ho_Chi_Minh)
  base/          <YYYYmmdd-HHMMSS>/base.tar.gz, pg_wal.tar.gz, START_WAL
  wal/           file WAL đã nén gzip (*.gz)
  pre-restore/   bản chụp an toàn tự tạo ngay trước mỗi lần khôi phục
```

**Cảnh báo:** `backups/` nằm **cùng máy** với database. Máy hỏng, ổ cứng hỏng, bị mã hóa tống tiền hay xóa nhầm cả thư mục thì mất luôn cả dữ liệu lẫn bản sao lưu. Vì vậy cần theo quy tắc **3-2-1**: ít nhất 3 bản dữ liệu, trên 2 loại phương tiện khác nhau, trong đó 1 bản ở nơi khác (ổ ngoài, máy khác, hoặc cloud). Thực tế với demo: định kỳ chép thư mục `backups/` sang ổ ngoài hoặc dịch vụ lưu trữ cloud. Ví dụ chép sang ổ ngoài đã gắn ở `/mnt/e`:

```bash
rsync -a --delete backups/ /mnt/e/chamcong-backups/
```

(Hãy thay đường dẫn đích bằng nơi của bạn. Chép sang cloud thì dùng công cụ tương ứng, và mã hóa file trước nếu dữ liệu là dữ liệu thật.) Một bản sao lưu chưa từng thử khôi phục thì chưa thể coi là sao lưu: hãy chạy diễn tập (mục 4).

## 3. Các lệnh

### Sao lưu ngay

```bash
scripts/backup/backup-now.sh           # một bản pg_dump vào backups/dump/
scripts/backup/backup-now.sh --base    # một base backup vào backups/base/ (điểm xuất phát cho PITR)
```

Nên chạy `backup-now.sh` ngay trước các thao tác rủi ro (migrate lớn, seed lại, thử khôi phục).

### Liệt kê bản sao lưu

```bash
scripts/backup/list-backups.sh
```

In ra các bản dump, các base backup (kèm file WAL bắt đầu), khoảng thời gian WAL đã lưu (cũ nhất và mới nhất), và dung lượng. Chọn `--target-time` của PITR nằm trong khoảng này.

### Khôi phục từ dump

```bash
scripts/backup/restore-dump.sh backups/dump/chamcong-YYYYmmdd-HHMMSS.dump
```

Script: hỏi xác nhận (gõ `yes`; thêm `--yes` để bỏ qua), chụp một bản dump an toàn của dữ liệu hiện tại vào `backups/pre-restore/`, dừng `api` và `worker`, chạy `pg_restore --clean --if-exists --single-transaction`, bật lại những service đã chạy, và in số dòng của từng bảng trước và sau. **Dữ liệu hiện tại bị thay bằng nội dung bản dump.**

### Khôi phục về một thời điểm (PITR)

```bash
# 1. chọn base backup (thư mục) cũ hơn thời điểm muốn quay về, xem bằng list-backups.sh
scripts/backup/restore-pitr.sh backups/base/<YYYYmmdd-HHMMSS> --target-time "2026-10-27 09:15:00+07"
```

`--target-time` có dạng `"YYYY-MM-DD HH:MM:SS+07"` (kèm múi giờ). Bỏ `--target-time` thì phát lại toàn bộ WAL đã lưu. Script: hỏi xác nhận, dừng stack, nén dữ liệu hiện tại vào `backups/pre-restore/pgdata-<giờ>.tar.gz`, thay volume `pgdata` bằng base backup, cho Postgres phát lại WAL đến thời điểm đích rồi tự chuyển sang chế độ ghi, và bật lại các service. Chọn thời điểm đích **ngay trước** sự cố vài chục giây, vì chọn đúng thời điểm sự cố thì sự cố cũng được phát lại.

Lưu ý: WAL chỉ có sau khi file được đóng và lưu. Muốn quay về một thời điểm rất gần hiện tại, trước hết chạy `docker compose exec db psql -U chamcong -d chamcong -c "select pg_switch_wal()"` (đổi tên user và database nếu `.env` của bạn khác) để ép đóng file WAL hiện tại.

### Chạy diễn tập

```bash
scripts/backup/drill.sh --yes
```

Diễn tập chạy trên một dự án compose **riêng** (`chamcong-drill`) với thư mục `backups-drill/`, không đụng stack chính. Các bước: dựng DB, migrate và seed; sao lưu và base backup; ghi các dòng đánh dấu A, ghi lại thời điểm T, rồi ghi các dòng B; giả lập sự cố (`TRUNCATE attendance_events`); PITR về T và kiểm tra có A mà không có B, số dòng mọi bảng khớp trạng thái trước sự cố; cuối cùng dump, xóa một bảng, khôi phục từ dump và kiểm tra lại. Kết quả in `PASS` hoặc `FAIL` từng bước và `OVERALL`. Thêm `--keep` để giữ lại container và thư mục sau khi chạy. Diễn tập chiếm thêm RAM của một stack Postgres, nên đừng chạy cùng lúc với load test.

## 4. Tình huống sự cố mẫu

Trước mọi thao tác khôi phục: **dừng việc ghi** vào hệ thống nếu có thể (`docker compose stop api worker`), và **đừng** xóa gì trước khi biết mình cần gì. Các script khôi phục đều tự chụp bản an toàn vào `backups/pre-restore/`, nhưng một bước dừng sớm vẫn rẻ hơn.

### Tình huống A. Lỡ xóa hoặc sửa nhầm dữ liệu

Ví dụ ai đó chạy `DELETE` hoặc `TRUNCATE` nhầm lúc 09:15.

1. Dừng ghi: `docker compose stop api worker`.
2. Ghi lại **giờ chính xác** trước khi sự cố xảy ra (xem log: `docker compose logs db`, hoặc hỏi người thao tác).
3. `scripts/backup/list-backups.sh`: xác nhận có base backup cũ hơn giờ đó và WAL phủ đến giờ đó.
4. Ép đóng WAL hiện tại để mọi thứ đến sát hiện tại đều được lưu: `docker compose exec db psql -U chamcong -d chamcong -c "select pg_switch_wal()"`. (Bước này cần db còn chạy.)
5. `scripts/backup/restore-pitr.sh backups/base/<base gần nhất trước giờ đó> --target-time "<ngày> 09:14:30+07"`.
6. Kiểm tra dữ liệu bằng số dòng mà script in ra và bằng cách mở web. Dữ liệu ghi **sau** thời điểm đích sẽ mất, nên báo cho người dùng nhập lại.
7. Nếu không có PITR phù hợp: `restore-dump.sh` với bản dump gần nhất trước giờ sự cố (mất tối đa 30 phút dữ liệu).
8. Bật lại: `docker compose start api worker` (script đã tự bật lại những service từng chạy).

Cách an toàn hơn khi chỉ mất một bảng: khôi phục bản dump vào **một database khác**, rồi chép riêng bảng cần thiết sang database chính. Cách này nằm ngoài các script của demo và cần làm thủ công bằng `pg_restore -t <bảng>`.

### Tình huống B. Hỏng volume Postgres

Triệu chứng: container `db` không lên (lỗi đọc file, `PANIC`, `invalid checkpoint record`), nhưng `backups/` còn nguyên.

1. Dừng ghi: `docker compose stop api worker`. Xem log: `docker compose logs db`.
2. Kiểm tra `backups/` còn nguyên: `scripts/backup/list-backups.sh`.
3. Cần một cụm Postgres đang chạy để script khôi phục dùng. Với PITR, script tự dừng, thay volume và khởi động lại db nên có thể chạy ngay: `scripts/backup/restore-pitr.sh backups/base/<base mới nhất>` (không có `--target-time` nghĩa là phát lại toàn bộ WAL, tức quay về thời điểm mới nhất còn lưu).
4. Nếu PITR không chạy được, tạo lại một volume trống rồi khôi phục dump:
   ```bash
   docker compose down            # KHÔNG dùng -v ở bước này
   docker volume ls               # tìm volume pgdata của dự án, tên dạng chamcong_pgdata
   docker volume rm chamcong_pgdata
   docker compose up -d db
   docker compose up -d
   scripts/backup/restore-dump.sh backups/dump/<bản dump mới nhất>
   ```
   Chỉ xóa volume sau khi chắc chắn `backups/` đã có dump đọc được và đã chép nó sang nơi khác.
5. Kiểm tra bằng `docker compose ps`, mở web, và đăng nhập thử.

### Tình huống C. Máy hỏng hoặc mất máy

Nếu `backups/` chỉ nằm trên máy hỏng thì **không có gì để khôi phục**. Đây là lý do của quy tắc 3-2-1 (mục 2). Nếu đã chép `backups/` ra nơi khác:

1. Chuẩn bị máy mới theo `docs/01-cai-dat-va-chay.md` (WSL, Docker, clone repo). Dùng cùng phiên bản repo (cùng cấu hình compose).
2. Tạo `.env` giống `.env` cũ (cùng `POSTGRES_USER`, `POSTGRES_DB`; mật khẩu và `JWT_SECRET` có thể đặt mới nhưng token cũ sẽ không còn hiệu lực). `.env` không nằm trong `backups/`, nên hãy cất `.env` ở nơi an toàn riêng (không phải trong git).
3. Chép `backups/` từ nơi lưu về thư mục `backups/` của repo mới.
4. `docker compose up -d --build` (api tự migrate vào database trống), rồi `docker compose stop api worker`.
5. Khôi phục: `scripts/backup/restore-pitr.sh backups/base/<base mới nhất>` để về thời điểm mới nhất, hoặc `scripts/backup/restore-dump.sh backups/dump/<bản mới nhất>`.
6. `docker compose up -d`, đăng nhập kiểm tra. Chạy `scripts/backup/backup-now.sh` ngay để có bản đầu tiên trên máy mới.

## 5. Ghi chú quan trọng

- **Tuyệt đối không tắt `fsync` hoặc `synchronous_commit` để lấy hiệu năng.** `fsync = off` có thể làm hỏng cả cụm database khi mất điện. `synchronous_commit = off` có thể mất các giao dịch cuối đã báo thành công cho client (không hỏng dữ liệu, nhưng vi phạm lời hứa "đã ghi là đã bền"). Demo cố ý để cả hai ở mặc định, cùng `full_page_writes`; việc tăng tốc ghi chỉ được làm bằng các tinh chỉnh không đổi độ bền (`max_wal_size`, `checkpoint_timeout`, `wal_compression`, xem `docs/07-kien-truc-va-thiet-ke.md`). Nguồn: https://www.postgresql.org/docs/current/runtime-config-wal.html.
- **Redis** dùng để xếp hàng job tính lương và rate limit, không chứa dữ liệu nghiệp vụ gốc. Redis có bật AOF (`appendonly yes`, `appendfsync everysec`) để job chưa chạy sống qua việc khởi động lại, nhưng nó **không nằm trong** sao lưu. Mất Redis thì chỉ phải bấm tính lương lại; kỳ kẹt ở `calculating` sẽ được tự đưa về `draft` khi worker khởi động.
- Lớp WAL của Postgres chỉ được lưu khi `archive_command` thành công. Kiểm tra định kỳ: `docker compose exec db psql -U chamcong -d chamcong -c "select last_archived_wal, last_archived_time, failed_count from pg_stat_archiver"`. `failed_count` tăng nghĩa là lưu WAL đang lỗi (thường do đầy ổ hoặc sai quyền).
- Bản sao lưu chứa toàn bộ dữ liệu, kể cả cột nhạy cảm (`employee_sensitive`). Với dữ liệu thật, thư mục sao lưu phải được bảo vệ và mã hóa như chính database.
- Dọn dẹp sau khôi phục: `backups/pre-restore/` giữ bản chụp trước mỗi lần khôi phục và không tự xóa. Xóa tay khi chắc chắn không cần.
