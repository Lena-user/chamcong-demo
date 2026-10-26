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
