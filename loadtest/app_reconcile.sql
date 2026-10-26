-- App-specific: replace this file when reusing the load-test kit for another app.
-- Lists every idempotency key found in the DB since the test started, so reconcile.py
-- can compare it with what the clients were told (writes.jsonl).
-- psql variable :since = DB timestamp taken just before the test (set by run.sh).
-- Output columns: kind,key,id,created_at  (kind = attendance | request).
-- A row "meta:request_has_key" says whether requests.idempotency_key exists yet;
-- to_jsonb(r) lets this query run even before that column was added.
SELECT 'attendance' AS kind, idempotency_key::text AS key, id, created_at
FROM attendance_events
WHERE created_at >= :'since'::timestamptz
UNION ALL
SELECT 'request', coalesce(to_jsonb(r)->>'idempotency_key', ''), id, created_at
FROM requests r
WHERE created_at >= :'since'::timestamptz
UNION ALL
SELECT 'meta:request_has_key',
       exists (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = 'requests'
                 AND column_name = 'idempotency_key')::text,
       NULL, NULL
ORDER BY 1, 4;
