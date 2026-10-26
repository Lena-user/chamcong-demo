## Đối soát ghi (không mất, không trùng)

| Chỉ số | Số lượng | Kết quả |
|---|---|---|
| Tổng lệnh ghi đã gửi (attendance: 21205, request: 7143) | 28348 | |
| Được xác nhận (2xx) | 28348 | |
| **Đã xác nhận nhưng không có trong DB** | 0 | ĐẠT |
| Key bị trùng trong DB | 0 | ĐẠT |
| Lỗi sau khi hết retry | 0 | |
| Trong số lỗi, thực ra đã vào DB (commit nhưng client không nhận được trả lời) | 0 | |
| Lệnh phải retry ít nhất một lần | 0 | |
| Key trong DB không có trong writes.jsonl (thường là lệnh đang bay lúc test dừng, hoặc traffic khác) | 74 | |
