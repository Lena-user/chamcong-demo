# TASK-012: Tra cứu kiến trúc hạ tầng của ngân hàng và sàn giao dịch

- **Giao cho:** scout
- **Trạng thái:** Mới
- **Liên quan:** TASK-005, TASK-006, TASK-007 (docs `07-kien-truc-va-thiet-ke.md`)

## Mục tiêu
Sếp muốn hạ tầng được thiết kế theo cách các hệ thống ngân hàng và sàn giao dịch làm. Cần nguồn uy tín để (1) đối chiếu các quyết định đã chọn và (2) viết docs có trích dẫn. Chỉ tra cứu, không sửa file.

## Câu hỏi (mỗi mục 3–5 dòng, kèm URL nguồn, ưu tiên tài liệu chính thức hoặc blog kỹ thuật của chính công ty)
1. Stripe: idempotency key được thiết kế thế nào (lưu ở đâu, bao lâu, xử lý request đang chạy dở ra sao)? Họ dùng rate limiter và load shedder loại gì?
2. Sổ cái kép (double-entry ledger) cho hệ thống tiền: nguyên tắc cốt lõi (chỉ append, cân bằng nợ/có, không sửa, sai thì ghi bút toán đảo). Nguồn gợi ý: Modern Treasury, Square hoặc Stripe engineering.
3. LMAX (Martin Fowler): vì sao sàn giao dịch dùng một luồng xử lý tuần tự và event sourcing để đạt độ trễ thấp, kèm số liệu họ công bố.
4. Transactional outbox (microservices.io): vấn đề "ghi DB rồi gửi message" và cách giải.
5. PostgreSQL:
   - Các mức `synchronous_commit` và đánh đổi giữa độ bền và độ trễ.
   - Streaming replication đồng bộ/bất đồng bộ.
   - PITR bằng WAL archive (theo docs chính thức).
   - Patroni dùng để làm gì.
6. PgBouncer transaction pooling: lợi ích, và lưu ý khi dùng với asyncpg (prepared statements, `statement_cache_size`).
7. AWS Well-Architected, Financial Services Industry Lens (hoặc tài liệu tương đương): RPO/RTO, cách phân tầng mức độ quan trọng, multi-AZ.
8. Cloudflare Quick Tunnel (`trycloudflare.com`): giới hạn chính thức (số request đang xử lý, SSE, SLA). Xác nhận con số 200.
9. Tinh chỉnh Postgres cho tải ghi nặng: `max_wal_size`, `checkpoint_timeout`, `checkpoint_completion_target`, `wal_compression`, group commit (`commit_delay`).
10. Timeout phía DB (`statement_timeout`, `lock_timeout`, `idle_in_transaction_session_timeout`) và vì sao hệ thống tài chính chọn lỗi nhanh (fail fast) thay vì để request xếp hàng.

## Đầu ra mong muốn
Báo cáo gửi leader bằng tiếng Việt. Đánh số theo 10 câu, mỗi câu gồm: tóm tắt, URL, và "Áp dụng cho demo chấm công" (1 dòng). Tổng không quá khoảng 120 dòng.

## Tiêu chí nghiệm thu
- [ ] Mỗi câu có ít nhất 1 URL thật, đã mở và đọc. Không bịa link.
- [ ] Số liệu nào cũng kèm nguồn.

## Không được đụng
- Không sửa file nào.

---
## Kết quả và review của leader

### Lần 1 (2026-10-26): kết quả scout (đã soát, dùng cho docs 07)
- Đánh giá: Đạt một phần. Đã mở được 15 URL thật. Mục 2 (ledger) và mục 7 (RPO/RTO của AWS) chưa có nguồn đọc được.
- Nguồn đã xác minh:
  1. Stripe idempotency (https://docs.stripe.com/api/idempotent_requests):
     - Lưu status code và body của lần đầu, kể cả khi lần đầu trả 500.
     - Key tối đa 255 ký tự, khuyến nghị UUIDv4, giữ ít nhất 24 giờ.
     - **Tham số khác lần gốc thì báo lỗi.**
     - Request trùng đang chạy song song thì không lưu kết quả.
  2. Stripe rate limiters (https://stripe.com/blog/rate-limiters): bốn lớp.
     - Request rate limiter: token bucket trên Redis, fail-open.
     - Concurrent requests limiter.
     - Fleet usage load shedder: giữ phần dành cho request quan trọng, trả 503.
     - Worker utilization load shedder.
  3. LMAX (https://martinfowler.com/articles/lmax.html):
     - Logic chạy một luồng trong bộ nhớ, event sourcing, khôi phục bằng replay journal.
     - Đạt khoảng 6 triệu order/giây trên một luồng (Nehalem 3GHz). Restart dưới 1 phút.
  4. Transactional outbox (https://microservices.io/patterns/data/transactional-outbox.html): ghi message cùng transaction với dữ liệu, relay gửi sau, consumer phải idempotent.
  5. Postgres:
     - `synchronous_commit` (https://www.postgresql.org/docs/current/runtime-config-wal.html): đặt `off` thì có thể mất giao dịch đã báo commit nhưng không làm hỏng nhất quán. Đặt được riêng cho từng transaction.
     - Replication mặc định bất đồng bộ. Muốn đồng bộ thì dùng `synchronous_standby_names` (https://www.postgresql.org/docs/current/warm-standby.html).
     - Patroni (https://patroni.readthedocs.io/en/latest/) dùng để tự động failover qua DCS.
  6. PgBouncer (https://www.pgbouncer.org/features.html):
     - Ở transaction pooling không dùng được session advisory lock, `SET` và `PREPARE` ở mức SQL.
     - asyncpg đặt `statement_cache_size=0` (https://magicstack.github.io/asyncpg/current/api/index.html).
     - **Lưu ý: job lương dùng `pg_advisory_lock` mức session, nên phải kết nối thẳng DB và không đi qua PgBouncer transaction mode.**
  7. Cloudflare Quick Tunnel (https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/): tối đa 200 request in-flight, vượt thì trả 429. Không có SSE, không có SLA, chỉ dùng để thử nghiệm.
  8. Mặc định của WAL và checkpoint (runtime-config-wal):
     - `max_wal_size` 1GB, `checkpoint_timeout` 5min, `checkpoint_completion_target` 0.9, `wal_compression` off.
     - `commit_delay` 0, chỉ có tác dụng khi số transaction đang chạy ≥ `commit_siblings` (5).
  9. Timeout (https://www.postgresql.org/docs/current/runtime-config-client.html):
     - Mặc định là 0 (tắt).
     - Không nên đặt trong `postgresql.conf` cho mọi session, nên đặt theo từng kết nối của app. Demo đã làm đúng như vậy.
     - Transaction idle lâu sẽ chặn VACUUM.
  10. AWS FSI Lens (https://docs.aws.amazon.com/wellarchitected/latest/financial-services-industry-lens/financial-services-industry-lens.html): chỉ xác nhận tài liệu có tồn tại, chưa đọc được nội dung.
- Việc rút ra:
  - Áp kiểm tra "cùng key nhưng tham số khác thì báo lỗi" của Stripe cho `POST /api/requests`, leader làm.
  - Docs 07 chỉ trích những nguồn đã xác minh ở trên. Ledger và RPO/RTO của AWS thì trình bày như kiến thức chung, không gắn nguồn.
