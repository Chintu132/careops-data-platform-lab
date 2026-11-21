-- Example Athena table over the exported daily summary. Not run against AWS yet.
-- Replace YOUR_BUCKET first. Athena charges per data scanned.

CREATE EXTERNAL TABLE IF NOT EXISTS careops_daily_facility (
  calendar_date STRING,
  facility_id STRING,
  channel STRING,
  messages_sent STRING,
  messages_delivered STRING,
  messages_responded STRING,
  delivery_pct STRING,
  response_pct STRING,
  avg_response_minutes STRING,
  on_shift_messages STRING
)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.OpenCSVSerde'
WITH SERDEPROPERTIES ('separatorChar' = ',', 'quoteChar' = '"', 'escapeChar' = '\\')
STORED AS TEXTFILE
LOCATION 's3://YOUR_BUCKET/careops/gold/daily_facility/'
TBLPROPERTIES ('skip.header.line.count' = '1');

-- Convert CSV strings explicitly; production design should prefer typed Parquet.
SELECT facility_id,
       SUM(TRY_CAST(messages_sent AS INTEGER)) AS sent,
       SUM(TRY_CAST(messages_responded AS INTEGER)) AS responded
FROM careops_daily_facility
GROUP BY facility_id
ORDER BY sent DESC;
