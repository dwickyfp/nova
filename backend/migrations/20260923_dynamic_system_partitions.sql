ALTER TABLE NOVA_SYSTEM.AUDIT_LOG SET (
  "dynamic_partition.enable"="true",
  "dynamic_partition.time_unit"="MONTH",
  "dynamic_partition.end"="6",
  "dynamic_partition.prefix"="p",
  "dynamic_partition.buckets"="8"
);

ALTER TABLE NOVA_SYSTEM.LINEAGE_LOAD_HISTORY SET (
  "dynamic_partition.enable"="true",
  "dynamic_partition.time_unit"="MONTH",
  "dynamic_partition.end"="6",
  "dynamic_partition.prefix"="p",
  "dynamic_partition.buckets"="4"
);

ALTER TABLE NOVA_SYSTEM.USAGE_QUERY_STATS SET (
  "dynamic_partition.enable"="true",
  "dynamic_partition.time_unit"="MONTH",
  "dynamic_partition.end"="6",
  "dynamic_partition.prefix"="p",
  "dynamic_partition.buckets"="8"
);
