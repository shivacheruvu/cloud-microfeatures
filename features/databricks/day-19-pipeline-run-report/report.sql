-- Day 19 Databricks: pipeline run report from the Lakeflow pipelines system tables (GA 2026-10-21).
-- One row per pipeline name for updates that started at or after :since, for pipelines whose name is LIKE :name_like.
-- A daily run creates a fresh pipeline under the same name and deletes it, so a name has several pipeline IDs.
WITH names AS (                       -- SCD2: latest named row per pipeline (a delete row may carry no name)
  SELECT workspace_id, pipeline_id, name
  FROM system.lakeflow.pipelines
  WHERE name IS NOT NULL
  QUALIFY row_number() OVER (PARTITION BY workspace_id, pipeline_id ORDER BY change_time DESC) = 1
),
deleted AS (
  SELECT workspace_id, pipeline_id, max(delete_time) AS deleted_at
  FROM system.lakeflow.pipelines
  GROUP BY workspace_id, pipeline_id
),
updates AS (                          -- an update longer than an hour is split into rows; only the last has a result
  SELECT workspace_id, pipeline_id, update_id,
         max(update_type)                                                  AS update_type,
         max(result_state)                                                 AS result_state,
         sum(timestampdiff(SECOND, period_start_time, period_end_time))    AS run_seconds,
         max(period_end_time)                                              AS ended_at
  FROM system.lakeflow.pipeline_update_timeline
  WHERE period_start_time >= :since
  GROUP BY workspace_id, pipeline_id, update_id
)
SELECT n.name                                                    AS pipeline,
       count(DISTINCT u.pipeline_id)                             AS pipeline_ids,
       count(DISTINCT CASE WHEN d.deleted_at IS NOT NULL THEN u.pipeline_id END) AS deleted_ids,
       count(*)                                                  AS updates,
       count_if(u.result_state = 'COMPLETED')                    AS completed,
       count_if(u.result_state = 'FAILED')                       AS failed,
       count_if(u.result_state = 'CANCELED')                     AS canceled,
       count_if(u.result_state IS NULL)                          AS running,
       count_if(u.update_type = 'FULL_REFRESH')                  AS full_refreshes,
       CAST(round(avg(u.run_seconds)) AS BIGINT)                 AS avg_seconds,
       max(u.run_seconds)                                        AS max_seconds,
       max_by(coalesce(u.result_state, 'RUNNING'), u.ended_at) AS last_result
FROM updates u
JOIN names n   ON n.workspace_id = u.workspace_id AND n.pipeline_id = u.pipeline_id
JOIN deleted d ON d.workspace_id = u.workspace_id AND d.pipeline_id = u.pipeline_id
WHERE n.name LIKE :name_like
GROUP BY n.name
ORDER BY n.name
