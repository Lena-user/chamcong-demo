# Chấm công & tính lương: demo chịu tải

> Dành cho phiên chính (leader). Subagent bỏ qua file này.

- Nguồn sự thật: `team/PROJECT.md`. Bảng task: `team/BOARD.md`. File task: `team/tasks/`.
- Đồng đội đã nạp sẵn bối cảnh ở `.claude/agents/`. Quy trình: `~/.claude/team-templates/README.md`.
- **Quyền quyết định:** leader toàn quyền về kỹ thuật. Phải hỏi sếp trước khi: bật tunnel (public ra internet), push, tạo tài khoản dịch vụ, làm việc tốn tiền, xóa dữ liệu.
- **Mỗi task:** leader viết file task → giao agent → tester kiểm → leader review và ghi kết quả vào file task → cập nhật BOARD → commit local.
- Chỉ dùng dữ liệu giả. `.env` không bao giờ commit.
- Mọi thứ chạy trong Docker (`python:3.12-slim`), vì máy chỉ có Python 3.14. Cổng 8788 và 5173 là của sếp.
- Trả lời sếp và viết docs bằng tiếng Việt. Code, tên biến, comment bằng tiếng Anh.
