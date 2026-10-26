-- Postgres probes used by run.sh during a load test.
-- Generic: works for any app on PostgreSQL 13+, keep it unchanged when reusing the kit.
-- Sections start with "-- @name: <section>"; run.sh feeds one section at a time to psql.
-- pg_stat_statements must be in shared_preload_libraries; run.sh creates the extension.

-- @name: reset
-- Forget old per-query stats so the top-queries table only covers this run.
SELECT pg_stat_statements_reset();

-- @name: sample
-- One JSON line per call (run.sh calls it every 2 seconds):
--   waits: active client sessions grouped by wait event ("CPU" = not waiting)
--   states: connections by state
--   xact_commit / xact_rollback: counters of the current database (diff = TPS)
SELECT json_build_object(
  'ts', extract(epoch FROM clock_timestamp()),
  'waits', (SELECT coalesce(json_object_agg(w, n), '{}'::json) FROM (
      SELECT CASE WHEN wait_event_type IS NULL THEN 'CPU'
                  ELSE wait_event_type || '/' || coalesce(wait_event, '-') END AS w,
             count(*) AS n
      FROM pg_stat_activity
      WHERE state = 'active' AND backend_type = 'client backend' AND pid <> pg_backend_pid()
      GROUP BY 1) x),
  'states', (SELECT coalesce(json_object_agg(s, n), '{}'::json) FROM (
      SELECT coalesce(state, 'none') AS s, count(*) AS n
      FROM pg_stat_activity WHERE backend_type = 'client backend' GROUP BY 1) y),
  'xact_commit', d.xact_commit,
  'xact_rollback', d.xact_rollback)
FROM pg_stat_database d WHERE d.datname = current_database();

-- @name: top_queries
-- Top 15 statements by total execution time (own probe queries excluded).
SELECT s.calls,
       round(s.mean_exec_time::numeric, 2) AS mean_ms,
       round(s.total_exec_time::numeric, 1) AS total_ms,
       s.rows,
       left(regexp_replace(s.query, '\s+', ' ', 'g'), 120) AS query
FROM pg_stat_statements s
JOIN pg_database db ON db.oid = s.dbid AND db.datname = current_database()
WHERE s.query NOT ILIKE '%pg_stat_statements%'
  AND s.query NOT ILIKE '%pg_stat_activity%'
  AND s.query NOT ILIKE '%clock_timestamp()%'
ORDER BY s.total_exec_time DESC
LIMIT 15;
