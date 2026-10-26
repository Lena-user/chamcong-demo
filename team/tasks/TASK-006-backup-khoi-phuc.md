# TASK-006: Sao lưu và khôi phục dữ liệu (dump định kỳ + PITR), có diễn tập tự động

- **Giao cho:** coder
- **Trạng thái:** Đang làm
- **Liên quan:** PROJECT.md mục 8. Chạy song song với TASK-003 (coder khác đang làm `app/` và `tests/`).

## Mục tiêu
Sếp yêu cầu phải **đảm bảo khôi phục được dữ liệu khi có sự cố**. Làm hai lớp sao lưu, chạy tự động, đều cấu hình qua `.env`:
1. **Dump logic định kỳ** (`pg_dump -Fc`): dễ hiểu, khôi phục cả DB về thời điểm dump.
2. **PITR** (base backup + lưu trữ WAL): khôi phục về một thời điểm bất kỳ, chỉ mất tối đa khoảng `WAL_ARCHIVE_TIMEOUT` giây dữ liệu.

Kèm script khôi phục và **script diễn tập** tự giả lập sự cố, khôi phục rồi kiểm tra, in PASS/FAIL. Đó là bằng chứng để demo.

## Đầu vào
- `docker-compose.yml`, `.env.example`, `.gitignore` hiện có (TASK-001). `db` là `postgres:16`, data nằm ở volume `pgdata`.
- Migrate tự chạy khi api khởi động. Seed chạy bằng `python -m scripts.seed`.

## Đầu ra mong muốn
1. **`docker-compose.yml`** (task này được sửa):
   - `db`: bật `wal_level=replica`, `archive_mode=on`, `archive_timeout=${WAL_ARCHIVE_TIMEOUT:-300}`.
     - `archive_command` nén gzip vào `/wal_archive`. Ghi file tạm rồi `mv`, và không ghi đè file đã có (`test ! -f`).
     - Giữ nguyên các tham số cũ. **Không** được tắt `fsync`, `synchronous_commit` hay `full_page_writes`.
   - Thư mục sao lưu: `${BACKUP_DIR:-./backups}` bind mount vào db (`/wal_archive` ← `<BACKUP_DIR>/wal`) và vào service `backup`.
   - Service mới **`backup`** (profile mặc định, nên chạy cùng `up`): image `postgres:16`, giới hạn 0.5 CPU / 256M, chỉ chạy khi db healthy. Chạy `scripts/backup/backup-loop.sh`:
     - Lúc khởi động, nếu chưa có base backup thì tạo một bản.
     - Mỗi `BACKUP_INTERVAL_MINUTES` (mặc định 30) thì dump.
     - Mỗi `BASEBACKUP_INTERVAL_HOURS` (mặc định 24) thì làm base backup.
     - Giữ `BACKUP_KEEP_DUMPS` (mặc định 48) bản dump và `BACKUP_KEEP_BASE` (mặc định 3) bản base backup.
     - Dọn WAL cũ hơn base backup cũ nhất còn giữ (`pg_archivecleanup`).
   - `redis`: bật AOF `--appendonly yes --appendfsync everysec`, có volume `redisdata`, để job Arq không mất khi redis khởi động lại.
   - `api`: `stop_grace_period: 30s`. `worker`: `stop_grace_period: 60s`. Cổng publish đổi thành `127.0.0.1:${API_PORT:-8000}:8000`.
   - Ngoài các điểm trên, không đổi gì khác.
2. **`scripts/backup/`** (bash, `set -euo pipefail`, shellcheck sạch, comment tiếng Anh). Mỗi script có `--help`. Script nào phá dữ liệu thì hỏi xác nhận, `--yes` để bỏ qua.
   - `backup-loop.sh`: chạy trong container `backup`.
   - `backup-now.sh`: chạy từ máy, dump ngay một bản. Có tùy chọn `--base` để làm base backup.
     - Dump ghi vào `<BACKUP_DIR>/dump/chamcong-YYYYmmdd-HHMMSS.dump`. Ghi file tạm rồi `mv`, sau đó kiểm bằng `pg_restore --list`, lỗi thì xóa file hỏng và báo lỗi.
     - Base backup ghi vào `<BACKUP_DIR>/base/<ts>/` (`pg_basebackup -Ft -z -X stream`).
   - `list-backups.sh`: liệt kê dump, base backup, khoảng thời gian của WAL đang có và dung lượng.
   - `restore-dump.sh <file.dump> [--yes]`:
     - Trước khi khôi phục, tự dump một bản an toàn của DB hiện tại vào `<BACKUP_DIR>/pre-restore/`.
     - Dừng `api` và `worker`, `pg_restore --clean --if-exists` vào DB, rồi chạy lại.
     - In số dòng các bảng chính trước và sau.
   - `restore-pitr.sh <base_dir> [--target-time "YYYY-MM-DD HH:MM:SS+07"] [--yes]`:
     - Dừng stack, tar data hiện tại vào `<BACKUP_DIR>/pre-restore/` để an toàn.
     - Xóa nội dung volume `pgdata`, giải nén base backup.
     - Ghi `recovery.signal` cùng `restore_command` (gunzip từ `/wal_archive`), `recovery_target_time` (nếu có) và `recovery_target_action=promote`.
     - Khởi động db, chờ recovery xong (`pg_is_in_recovery()` trả false), dọn cấu hình recovery, rồi chạy lại api/worker.
   - `drill.sh`: **diễn tập tự động trên một compose project riêng** (`-p chamcong-drill`, `BACKUP_DIR` riêng, ví dụ `./backups-drill`, chỉ chạy db và backup). Không đụng stack chính. Các bước:
     1. Migrate và seed bằng `run --rm --no-deps api`.
     2. Làm base backup.
     3. Chèn các dòng mốc A, ghi lại thời điểm T, chờ vài giây, chèn các dòng mốc B, ép chuyển WAL (`pg_switch_wal()`).
     4. Giả lập sự cố bằng `TRUNCATE attendance_events CASCADE`.
     5. `restore-pitr.sh` về T. Kiểm tra có A, không có B, và số dòng các bảng khác khớp lúc trước sự cố.
     6. Làm thêm vòng dump: dump, xóa bảng, `restore-dump.sh`, kiểm số dòng khớp.
     7. In bảng PASS/FAIL từng bước, hết thì `down -v` project drill và xóa thư mục drill.
     - Chèn mốc qua SQL trực tiếp bằng psql trong container drill. Nếu trigger chặn UPDATE/DELETE trên `attendance_events` đã có (TASK-003 đang thêm) thì chỉ INSERT là đủ.
3. `.env.example`: thêm các biến mới, có comment: `BACKUP_DIR`, `BACKUP_INTERVAL_MINUTES`, `BASEBACKUP_INTERVAL_HOURS`, `BACKUP_KEEP_DUMPS`, `BACKUP_KEEP_BASE`, `WAL_ARCHIVE_TIMEOUT`, `API_PORT`.
4. `.gitignore`: thêm `backups/` và `backups-drill/`.

## Tiêu chí nghiệm thu
- [ ] `./scripts/backup/drill.sh --yes` chạy xong, mọi bước PASS. Dán bảng kết quả vào báo cáo.
- [ ] `docker compose config` hợp lệ. Các service cũ giữ nguyên hành vi, chỉ thêm `backup`, các cấu hình WAL, AOF, `stop_grace_period` và `API_PORT`.
- [ ] Shellcheck sạch với mọi script (dùng image `koalaman/shellcheck:stable`).
- [ ] Không xóa dữ liệu nào ngoài project drill.

## Không được đụng
- **Không restart, không recreate `db`, `api` hay `worker` của stack chính** (project `chamcong`), vì coder TASK-003 đang chạy test trên đó. Chỉ thử nghiệm trên project `chamcong-drill`. Leader sẽ áp cấu hình mới cho stack chính sau.
- `app/`, `tests/`, `loadtest/`, `team/`, `scripts/seed.py`.
- Không chạy `pkill -f`, không `docker volume rm` volume của project `chamcong`. Không commit, không push, không bật profile `tunnel`.

---
## Kết quả và review của leader
