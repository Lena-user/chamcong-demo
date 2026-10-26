"""Check that what is stored is right, not only that it was stored.

    python -m scripts.check_integrity                 # whole database
    python -m scripts.check_integrity --since ISO     # only what was written since ISO (e.g. a load test)
    python -m scripts.check_integrity --markdown FILE # also write the result table (Vietnamese) to FILE

Exit code 1 when any check fails. Read-only.
"""
import argparse
import asyncio
import sys
from datetime import datetime

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.db import make_engine
from app.modules.attendance import service as attendance_service
from app.modules.requests import service as requests_service

# Full-table recomputes take longer than the 5 s request timeout of the api.
CHECK_TIMEOUT_MS = "300000"


async def run(since: datetime | None) -> list[tuple[str, int, str]]:
    engine = make_engine(get_settings().database_url, pool_size=1, max_overflow=0,
                         server_settings={"statement_timeout": CHECK_TIMEOUT_MS})
    try:
        async with async_sessionmaker(engine)() as session:
            att = await attendance_service.check_integrity(session, since)
            req = await requests_service.check_rules(session)
    finally:
        await engine.dispose()
    d, e = att["daily"], att["events"]
    sample = f" Ví dụ (nhân viên, ngày): {', '.join(d.sample)}" if d.bad else ""
    return [
        (f"Bảng công theo ngày khớp 100% khi tính lại từ dữ liệu chấm thô ({d.checked} cặp nhân viên-ngày)",
         d.bad, "Sai giờ vào/ra, số phút hoặc OT, hoặc thiếu/thừa dòng." + sample),
        (f"Không có lượt chấm công trùng mã chống trùng ({e.total} lượt)", e.duplicate_keys, ""),
        ("Không có lượt chấm công ở tương lai", e.future_rows, "Giờ chấm lấy từ đồng hồ DB."),
        ("Dữ liệu chấm thô chưa từng bị sửa hay xóa (thống kê Postgres từ lần reset gần nhất)",
         e.updates_deletes or 0, ""),
        ("Trigger chặn sửa/xóa dữ liệu chấm thô đang bật", 0 if e.guard_triggers else 1, ""),
        (f"Đơn chờ duyệt không mang thông tin người duyệt ({req.total} đơn)", req.pending_with_decision, ""),
        ("Đơn đã duyệt/từ chối đều có người duyệt và thời điểm", req.decided_without_actor, ""),
        ("Không ai tự duyệt đơn của mình", req.self_decided, ""),
        ("Không có đơn được quyết định trước lúc tạo", req.decided_before_created, ""),
        ("Không có đơn trùng mã chống trùng", req.duplicate_keys, ""),
    ]


def render(results, since) -> str:
    scope = f"dữ liệu ghi từ {since.isoformat()}" if since else "toàn bộ database"
    lines = ["## Kiểm tra toàn vẹn dữ liệu", "", f"Phạm vi: {scope}.", "",
             "| Kiểm tra | Vi phạm | Kết quả |", "|---|---|---|"]
    for name, bad, note in results:
        lines.append(f"| {name}{'. ' + note if bad and note else ''} | {bad} | {'ĐẠT' if not bad else '**KHÔNG ĐẠT**'} |")
    ok = not any(bad for _, bad, _ in results)
    lines += ["", f"**Kết luận: {'mọi kiểm tra ĐẠT' if ok else 'có kiểm tra KHÔNG ĐẠT'}**", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", type=datetime.fromisoformat, help="only check data written since this time")
    ap.add_argument("--markdown", help="also write the result table to this file")
    args = ap.parse_args()
    results = asyncio.run(run(args.since))
    text = render(results, args.since)
    print(text)
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(text)
    return 1 if any(bad for _, bad, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
