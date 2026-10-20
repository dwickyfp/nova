# Katalog statement penelitian Snowflake

Tanggal 13 Oktober 2026. 224 statement connector, termasuk setup, observasi, negative test, dan cleanup. Status mentah dipertahankan. Penjelasan koreksi ada di laporan utama.

`PASS` mengindikasikan statement berhasil dan memenuhi assertion jika assertion tersedia. DDL tanpa assertion hanya membuktikan eksekusi. `OBSERVED` merekam hasil tanpa assertion. `EXPECTED_ERROR` cocok dengan pola error yang diharapkan. `ERROR` dan `FAIL` memerlukan interpretasi dalam laporan.

| ID | Status | Query ID | Hasil ringkas |
| --- | --- | --- | --- |
| M01_CONTEXT | OBSERVED | `01c7b3f7-0001-afaf-0000-0000d231c0ed` | [['10.37.100', 'AWS_AP_SOUTHEAST_3', 'DWICKYFERI', 'ACCOUNTADMIN', 'COMPUTE_WH', None, None]] |
| M02_WAREHOUSES | OBSERVED | `01c7b3f7-0001-afaf-0000-0000d231c0f1` | [['COMPUTE_WH', 'SUSPENDED', 'STANDARD', 'X-Small', 1, 1, 0, 0, 0, 'Y', 'Y', 600, 'true', '', '', '', '', '2026-10-13T05:49:28.510000-07:00', '2026-10-13T05:49:28.518000-07:00', '2026-10-13T05:49:28.543000-07:00', 'ACCOU ... |
| M03_DATABASES | OBSERVED | `01c7b3f7-0001-afab-0000-0000d231a0f5` | [['2026-10-13T05:47:28.933000-07:00', 'SNOWFLAKE', 'N', 'N', 'SFCOGSOPS.SNOWHOUSE_LOCAL_AWS_AP_SOUTHEAST_3.ACCOUNT_USAGE', '', '', '', '0', 'APPLICATION', '', None, None, None], ['2026-10-13T05:47:32.756000-07:00', 'SNOW ... |
| M04_SCHEMA | OBSERVED | `01c7b3f7-0001-afab-0000-0000d231a0f9` | [['2026-10-13T06:11:18.088000-07:00', 'INFORMATION_SCHEMA', 'N', 'N', 'USER$DWICKYFERI', '', 'Views describing the contents of schemas in this database', '', '1', '', None, None, None, None, 'false'], ['2026-10-13T05:49: ... |
| S00_USE_DATABASE | PASS | `01c7b3f9-0001-afaf-0000-0000d231c0f9` | [['Statement executed successfully.']] |
| S01_USE_SCHEMA | PASS | `01c7b3f9-0001-afaf-0000-0000d231c0fd` | [['Statement executed successfully.']] |
| S02_USE_WAREHOUSE | PASS | `01c7b3f9-0001-afaf-0000-0000d231c101` | [['Statement executed successfully.']] |
| S03_TIMEZONE | PASS | `01c7b3f9-0001-afab-0000-0000d231a10d` | [['Statement executed successfully.']] |
| Q01_VALUES_CTE | PASS | `01c7b3f9-0001-afaf-0000-0000d231c105` | [['Bandung', 30], ['Jakarta', 30]] |
| Q02_QUALIFY | PASS | `01c7b3f9-0001-afab-0000-0000d231a111` | [[2, 'Jakarta', 20], [3, 'Bandung', 30]] |
| Q03_GROUP_BY_ALL | PASS | `01c7b3f9-0001-afab-0000-0000d231a115` | [['Bandung', 30], ['Jakarta', 30]] |
| Q04_SELECT_MODIFIERS | PASS | `01c7b3f9-0001-afab-0000-0000d231a119` | [[1, 20]] |
| Q05_SELECT_ILIKE | PASS | `01c7b3f9-0001-afab-0000-0000d231a11d` | [[1]] |
| Q06_TRAILING_COMMA | PASS | `01c7b3f9-0001-afaf-0000-0000d231c109` | [[1, 2]] |
| Q07_ALIAS_REUSE | PASS | `01c7b3f9-0001-afab-0000-0000d231a121` | [[4, 8]] |
| Q08_NULL_SAFE_EQUALITY | PASS | `01c7b3f9-0001-afaf-0000-0000d231c10d` | [[None, True, True, 'fallback']] |
| Q09_TRY_CAST | PASS | `01c7b3f9-0001-afab-0000-0000d231a125` | [[None, 42, 'yes', 0]] |
| Q10_JSON_PATH | PASS | `01c7b3f9-0001-afaf-0000-0000d231c111` | [['Jakarta', 20]] |
| Q11_JSON_NULLS | PASS | `01c7b3f9-0001-afaf-0000-0000d231c115` | [[False, True, True]] |
| Q12_FLATTEN | PASS | `01c7b3f9-0001-afab-0000-0000d231a129` | [[0, 10], [1, 20], [2, 30]] |
| Q13_LATERAL_FLATTEN | PASS | `01c7b3f9-0001-afaf-0000-0000d231c119` | [[7, 1], [7, 2]] |
| Q14_OBJECT_ARRAY | PASS | `01c7b3f9-0001-afab-0000-0000d231a12d` | [[3, 'Jakarta', 'ARRAY']] |
| Q15_DATE_TIME | PASS | `01c7b3f9-0001-afaf-0000-0000d231c11d` | [['2026-10-15', 12, '2026-10-13 07:00:00']] |
| Q16_REGEX | PASS | `01c7b3f9-0001-afab-0000-0000d231a131` | [[True, 'b', True]] |
| Q17_RECURSIVE_CTE | PASS | `01c7b3f9-0001-afaf-0000-0000d231c121` | [[15]] |
| Q18_PIVOT | PASS | `01c7b3f9-0001-afab-0000-0000d231a135` | [['Jakarta', 10, 20]] |
| Q19_UNPIVOT | PASS | `01c7b3f9-0001-afaf-0000-0000d231c125` | [['COSTS', 20], ['SALES', 10]] |
| Q20_GENERATOR | PASS | `01c7b3f9-0001-afab-0000-0000d231a139` | [[10]] |
| Q21_UNION_BY_NAME | PASS | `01c7b3f9-0001-afaf-0000-0000d231c129` | [[1, 2], [10, 20]] |
| Q22_PIPE_OPERATOR | PASS | `01c7b3f9-0001-afab-0000-0000d231a13d` | [[6]] |
| Q23_ASOF_JOIN | PASS | `01c7b3f9-0001-afaf-0000-0000d231c12d` | [[1, 100]] |
| Q24_MATCH_RECOGNIZE | ERROR | `01c7b3f9-0001-afab-0000-0000d231a149` | 002139 (02000): SQL compilation error: Unknown function PREV. |
| Q25_ROLLUP | PASS | `01c7b3f9-0001-afaf-0000-0000d231c131` | [['Bandung', 20, 0], ['Jakarta', 10, 0], ['TOTAL', 30, 1]] |
| Q26_WINDOW_FRAME | PASS | `01c7b3f9-0001-afab-0000-0000d231a14d` | [[1, 10], [2, 30], [3, 60]] |
| Q27_IDENTIFIERS | PASS | `01c7b3f9-0001-afab-0000-0000d231a151` | [[7]] |
| E01_IDENTIFIER_CASE | EXPECTED_ERROR | `01c7b3f9-0001-afaf-0000-0000d231c135` | 000904 (42000): SQL compilation error: error line 1 at position 7 invalid identifier 'MIXEDCASE' |
| E02_INVALID_CAST | EXPECTED_ERROR | `01c7b3f9-0001-afab-0000-0000d231a155` | 100038 (22018): Numeric value 'not_a_number' is not recognized |
| E03_DIVIDE_BY_ZERO | EXPECTED_ERROR | `01c7b3f9-0001-afaf-0000-0000d231c139` | 100051 (22012): Division by zero |
| E04_INVALID_STAR_ORDER | EXPECTED_ERROR | `01c7b3f9-0001-afab-0000-0000d231a159` | 001003 (42000): SQL compilation error: syntax error line 1 at position 51 unexpected 'EXCLUDE'. |
| D01_CREATE_TEMP | PASS | `01c7b3f9-0001-afaf-0000-0000d231c13d` | [['Table SF_RESEARCH_20261013_ORDERS successfully created.']] |
| D02_INSERT | PASS | `01c7b3f9-0001-afab-0000-0000d231a15d` | [[2]] |
| D03_READ_TABLE | PASS | `01c7b3f9-0001-afaf-0000-0000d231c141` | [[1, 'Jakarta', '10.00', 'web'], [2, 'Bandung', '20.00', 'store']] |
| D04_MERGE | PASS | `01c7b3f9-0001-afaf-0000-0000d231c145` | [[1, 1]] |
| D05_MERGE_CHECK | PASS | `01c7b3f9-0001-afab-0000-0000d231a161` | [[1, '10.00'], [2, '25.00'], [3, '30.00']] |
| D06_BEGIN | PASS | `01c7b3f9-0001-afaf-0000-0000d231c149` | [['Statement executed successfully.']] |
| D07_TX_UPDATE | PASS | `01c7b3f9-0001-afaf-0000-0000d231c14d` | [[1, 0]] |
| D08_TX_ROLLBACK | PASS | `01c7b3f9-0001-afab-0000-0000d231a165` | [['Statement executed successfully.']] |
| D09_ROLLBACK_CHECK | PASS | `01c7b3f9-0001-afab-0000-0000d231a169` | [['10.00']] |
| D10_UPDATE_HISTORY | PASS | `01c7b3f9-0001-afab-0000-0000d231a16d` | [[1, 0]] |
| D11_SAVE_UPDATE_ID | PASS | `01c7b3f9-0001-afaf-0000-0000d231c151` | [['Statement executed successfully.']] |
| D12_TIME_TRAVEL | PASS | `01c7b3f9-0001-afab-0000-0000d231a171` | [['10.00']] |
| D13_TEMP_CLONE | PASS | `01c7b3f9-0001-afaf-0000-0000d231c159` | [['Table SF_RESEARCH_20261013_CLONE successfully created.']] |
| D14_UPDATE_CLONE | PASS | `01c7b3f9-0001-afaf-0000-0000d231c15d` | [[1, 0]] |
| D15_CLONE_ISOLATION | PASS | `01c7b3f9-0001-afaf-0000-0000d231c161` | [['11.00', '50.00']] |
| E05_NOT_NULL | EXPECTED_ERROR | `01c7b3f9-0001-afaf-0000-0000d231c165` | 100072 (22000): DML operation to table SF_RESEARCH_20261013_ORDERS failed on column ID with error: NULL result in a non-nullable column |
| D16_CREATE_PK | PASS | `01c7b3f9-0001-afab-0000-0000d231a175` | [['Table SF_RESEARCH_20261013_PK successfully created.']] |
| D17_PK_DUPLICATES | PASS | `01c7b3f9-0001-afab-0000-0000d231a179` | [[2]] |
| D18_PK_NOT_ENFORCED | PASS | `01c7b3f9-0001-afaf-0000-0000d231c16d` | [[2]] |
| D19_CREATE_CHECK | OBSERVED | `01c7b3f9-0001-afaf-0000-0000d231c171` | [['Table SF_RESEARCH_20261013_CHECK successfully created.']] |
| D20_CHECK_NEGATIVE | ERROR | `01c7b3f9-0001-afaf-0000-0000d231c175` | 001185 (23514): Operation on table USER$DWICKYFERI.PUBLIC.SF_RESEARCH_20261013_CHECK failed because CHECK constraint "SYS_CONSTRAINT_489b1ad8-6bad-4983-a62b-2b13811730d5", which requires that n>0, was violated |
| D21_RESULT_SCAN_SEED | PASS | `01c7b3fa-0001-afab-0000-0000d231a17d` | [[8]] |
| D22_RESULT_SCAN | PASS | `01c7b3fa-0001-afaf-0000-0000d231c179` | [[16]] |
| D23_DELETE | PASS | `01c7b3fa-0001-afab-0000-0000d231a181` | [[1]] |
| D24_DELETE_CHECK | PASS | `01c7b3fa-0001-afab-0000-0000d231a185` | [[2]] |
| O01_EXPLAIN | OBSERVED | `01c7b3fa-0001-afab-0000-0000d231a189` | [['GlobalStats:\n    partitionsTotal=1\n    partitionsAssigned=1\n    bytesAssigned=1536\nOperations:\n1:0     ->Result  SUM(SF_RESEARCH_20261013_ORDERS.AMOUNT)  \n1:1          ->Aggregate  aggExprs: [SUM(SF_RESEARCH_202 ... |
| O02_QUERY_HISTORY | OBSERVED | `01c7b3fa-0001-afab-0000-0000d231a18d` | [['SELECT', 'RUNNING'], ['EXPLAIN', 'SUCCESS'], ['SELECT', 'SUCCESS'], ['DELETE', 'SUCCESS'], ['SELECT', 'SUCCESS']] |
| O03_QUERY_OPERATORS | OBSERVED | `01c7b3fa-0001-afab-0000-0000d231a191` | [['Metadata operation'], ['Result'], ['Sort'], ['InternalObject']] |
| O04_INFORMATION_SCHEMA | OBSERVED | `01c7b3fa-0001-afaf-0000-0000d231c17d` | [['SF_RESEARCH_20261013_CHECK', 'LOCAL TEMPORARY'], ['SF_RESEARCH_20261013_CLONE', 'LOCAL TEMPORARY'], ['SF_RESEARCH_20261013_ORDERS', 'LOCAL TEMPORARY'], ['SF_RESEARCH_20261013_PK', 'LOCAL TEMPORARY']] |
| C01_DROP_CLONE | PASS | `01c7b3fa-0001-afaf-0000-0000d231c181` | [['SF_RESEARCH_20261013_CLONE successfully dropped.']] |
| C02_DROP_PK | PASS | `01c7b3fa-0001-afab-0000-0000d231a195` | [['SF_RESEARCH_20261013_PK successfully dropped.']] |
| C03_DROP_CHECK | PASS | `01c7b3fa-0001-afab-0000-0000d231a199` | [['SF_RESEARCH_20261013_CHECK successfully dropped.']] |
| C04_DROP_ORDERS | PASS | `01c7b3fa-0001-afab-0000-0000d231a19d` | [['SF_RESEARCH_20261013_ORDERS successfully dropped.']] |
| C05_TEMP_CLEANUP_CHECK | PASS | `01c7b3fa-0001-afaf-0000-0000d231c185` | [[0]] |
| C06_WAREHOUSE_STATE | OBSERVED | `01c7b3fa-0001-afaf-0000-0000d231c189` | [['COMPUTE_WH', 'STARTED', 'STANDARD', 'X-Small', 1, 1, 1, 0, 0, 'Y', 'Y', 600, 'true', ' 100', '0', '0', '0', '2026-10-13T19:49:28.510000+07:00', '2026-10-13T20:13:34.085000+07:00', '2026-10-13T20:13:34.085000+07:00', ' ... |
| C07_SUSPEND_WAREHOUSE | PASS | `01c7b3fa-0001-afab-0000-0000d231a1a1` | [['Statement executed successfully.']] |
| R01_MATCH_RECOGNIZE_CORRECTED | PASS | `01c7b3fd-0001-afab-0000-0000d231a1b9` | [[1, 3]] |
| R02_CHECK_CREATE | PASS | `01c7b3fd-0001-afab-0000-0000d231a1bd` | [['Table SF_RESEARCH_20261013_CHECK2 successfully created.']] |
| R03_CHECK_VALID | PASS | `01c7b3fd-0001-afaf-0000-0000d231c1ad` | [[2]] |
| R04_CHECK_REJECT | EXPECTED_ERROR | `01c7b3fd-0001-afaf-0000-0000d231c1b1` | 001185 (23514): Operation on table USER$DWICKYFERI.PUBLIC.SF_RESEARCH_20261013_CHECK2 failed because CHECK constraint "SYS_CONSTRAINT_d3d17074-d60e-4a0c-ba31-3ef25b09a138", which requires that n>0, was violated |
| R05_CHECK_ATOMIC | PASS | `01c7b3fd-0001-afab-0000-0000d231a1c1` | [[2]] |
| R06_CHECK_UPDATE | EXPECTED_ERROR | `01c7b3fd-0001-afab-0000-0000d231a1c5` | 001185 (23514): Operation on table USER$DWICKYFERI.PUBLIC.SF_RESEARCH_20261013_CHECK2 failed because CHECK constraint "SYS_CONSTRAINT_d3d17074-d60e-4a0c-ba31-3ef25b09a138", which requires that n>0, was violated |
| A01_SCRIPTING | PASS | `01c7b3fd-0001-afab-0000-0000d231a1c9` | [[15]] |
| A02_TEMP_UDF | PASS | `01c7b3fd-0001-afaf-0000-0000d231c1b5` | [['Function SF_RESEARCH_20261013_DOUBLE successfully created.']] |
| A03_UDF_RESULT | PASS | `01c7b3fd-0001-afaf-0000-0000d231c1b9` | [[14]] |
| A04_TEMP_FORMAT | PASS | `01c7b3fd-0001-afab-0000-0000d231a1cd` | [['File format SF_RESEARCH_20261013_CSV successfully created.']] |
| A05_TEMP_STAGE | PASS | `01c7b3fd-0001-afab-0000-0000d231a1d1` | [['Stage area SF_RESEARCH_20261013_STAGE successfully created.']] |
| A06_PUT_SYNTHETIC | OBSERVED | `01c7b3fd-0001-afaf-0000-0000d231c1bd` | [['synthetic.csv', 'synthetic.csv', 41, 48, 'NONE', 'NONE', 'UPLOADED', '']] |
| A07_LIST_STAGE | OBSERVED | `01c7b3fd-0001-afaf-0000-0000d231c1c1` | [['sf_research_20261013_stage/synthetic.csv', 48, '9b74d98bcdb7679cef4d68f15d3fb1d2', 'Tue, 13 Oct 2026 13:17:37 GMT']] |
| A08_QUERY_STAGE | PASS | `01c7b3fd-0001-afaf-0000-0000d231c1c5` | [[1, 'Jakarta', 10], [2, 'Bandung', 20]] |
| A09_LOAD_TABLE | PASS | `01c7b3fd-0001-afab-0000-0000d231a1d5` | [['Table SF_RESEARCH_20261013_LOAD successfully created.']] |
| A10_COPY_LOAD | OBSERVED | `01c7b3fd-0001-afaf-0000-0000d231c1c9` | [['sf_research_20261013_stage/synthetic.csv', 'LOADED', 2, 2, 1, 0, None, None, None, None]] |
| A11_COPY_CHECK | PASS | `01c7b3fd-0001-afab-0000-0000d231a1d9` | [[2, 30]] |
| A12_COPY_REPEAT | OBSERVED | `01c7b3fd-0001-afaf-0000-0000d231c1cd` | [['Copy executed with 0 files processed.']] |
| A13_COPY_DEDUP_CHECK | PASS | `01c7b3fd-0001-afaf-0000-0000d231c1d1` | [[2]] |
| A14_UNLOAD | OBSERVED | `01c7b3fd-0001-afaf-0000-0000d231c1d5` | [[2, 51, 51]] |
| A15_GET_EXPORT | ERROR | `01c7b3fd-0001-afaf-0000-0000d231c1d9` | 253005: The local path is not a directory: /tmp/snowflake-research-20261013/download/ |
| A16_TEMP_VIEW | PASS | `01c7b3fd-0001-afab-0000-0000d231a1dd` | [['View SF_RESEARCH_20261013_VIEW successfully created.']] |
| A17_VIEW_CHECK | PASS | `01c7b3fd-0001-afaf-0000-0000d231c1dd` | [['Bandung', 20], ['Jakarta', 10]] |
| A18_STREAM_SOURCE | ERROR | `01c7b3fd-0001-afab-0000-0000d231a1e1` | 060119 (0A000): Tables cannot currently be created in a personal database. |
| A19_STREAM_CREATE | ERROR | `01c7b3fd-0001-afab-0000-0000d231a1e5` | 060119 (0A000): Streams cannot currently be created in a personal database. |
| A20_STREAM_INSERT | ERROR | `01c7b3fd-0001-afab-0000-0000d231a1e9` | 002003 (42S02): SQL compilation error: Table 'SF_RESEARCH_20261013_STREAM_SOURCE' does not exist or not authorized. Your primary role ACCOUNTADMIN or one of your secondary roles must have at least one privilege granted o ... |
| A21_STREAM_FIRST_READ | ERROR | `01c7b3fd-0001-afab-0000-0000d231a1ed` | 002003 (42S02): SQL compilation error: Object 'SF_RESEARCH_20261013_STREAM' does not exist or not authorized. Your primary role ACCOUNTADMIN or one of your secondary roles must have at least one privilege granted on TABL ... |
| A22_STREAM_SECOND_READ | ERROR | `01c7b3fd-0001-afab-0000-0000d231a1f1` | 002003 (42S02): SQL compilation error: Object 'SF_RESEARCH_20261013_STREAM' does not exist or not authorized. Your primary role ACCOUNTADMIN or one of your secondary roles must have at least one privilege granted on TABL ... |
| A23_STREAM_SINK | PASS | `01c7b3fd-0001-afab-0000-0000d231a1f5` | [['Table SF_RESEARCH_20261013_STREAM_SINK successfully created.']] |
| A24_STREAM_CONSUME | ERROR | `01c7b3fd-0001-afaf-0000-0000d231c1e1` | 002003 (42S02): SQL compilation error: Object 'SF_RESEARCH_20261013_STREAM' does not exist or not authorized. Your primary role ACCOUNTADMIN or one of your secondary roles must have at least one privilege granted on TABL ... |
| A25_STREAM_OFFSET_ADVANCED | ERROR | `01c7b3fd-0001-afab-0000-0000d231a1f9` | 002003 (42S02): SQL compilation error: Object 'SF_RESEARCH_20261013_STREAM' does not exist or not authorized. Your primary role ACCOUNTADMIN or one of your secondary roles must have at least one privilege granted on TABL ... |
| A26_STREAM_UPDATE | ERROR | `01c7b3fd-0001-afaf-0000-0000d231c1e5` | 002003 (42S02): SQL compilation error: Object 'SF_RESEARCH_20261013_STREAM_SOURCE' does not exist or not authorized. Your primary role ACCOUNTADMIN or one of your secondary roles must have at least one privilege granted  ... |
| A27_STREAM_UPDATE_PAIR | ERROR | `01c7b3fd-0001-afaf-0000-0000d231c1e9` | 002003 (42S02): SQL compilation error: Object 'SF_RESEARCH_20261013_STREAM' does not exist or not authorized. Your primary role ACCOUNTADMIN or one of your secondary roles must have at least one privilege granted on TABL ... |
| A28_SEMANTIC_CREATE | ERROR | `01c7b3fd-0001-afab-0000-0000d231a1fd` | 060119 (0A000): Semantic Views cannot currently be created in a personal database. |
| A29_SEMANTIC_QUERY | ERROR | `01c7b3fd-0001-afaf-0000-0000d231c1ed` | 002003 (02000): SQL compilation error: Semantic View 'USER$DWICKYFERI.PUBLIC.SF_RESEARCH_20261013_SEMANTIC' does not exist or not authorized. Your primary role ACCOUNTADMIN or one of your secondary roles must have at lea ... |
| A30_SEMANTIC_METADATA | ERROR | `01c7b3fd-0001-afab-0000-0000d231a201` | 001003 (42000): SQL compilation error: syntax error line 1 at position 34 unexpected 'VIEW'. |
| A31_UNDROP_CREATE | ERROR | `01c7b3fd-0001-afab-0000-0000d231a205` | 060119 (0A000): Tables cannot currently be created in a personal database. |
| A32_UNDROP_INSERT | ERROR | `01c7b3fd-0001-afaf-0000-0000d231c1f1` | 002003 (42S02): SQL compilation error: Table 'SF_RESEARCH_20261013_RECOVERY' does not exist or not authorized. Your primary role ACCOUNTADMIN or one of your secondary roles must have at least one privilege granted on TAB ... |
| A33_RECOVERY_DROP | ERROR | `01c7b3fd-0001-afaf-0000-0000d231c1f5` | 002003 (42S02): SQL compilation error: Table 'USER$DWICKYFERI.PUBLIC.SF_RESEARCH_20261013_RECOVERY' does not exist or not authorized. Your primary role ACCOUNTADMIN or one of your secondary roles must have at least one p ... |
| A34_RECOVERY_UNDROP | ERROR | `01c7b3fd-0001-afab-0000-0000d231a209` | 060119 (0A000): Tables cannot currently be created in a personal database. |
| A35_RECOVERY_CHECK | ERROR | `01c7b3fd-0001-afaf-0000-0000d231c1f9` | 002003 (42S02): SQL compilation error: Object 'SF_RESEARCH_20261013_RECOVERY' does not exist or not authorized. Your primary role ACCOUNTADMIN or one of your secondary roles must have at least one privilege granted on TA ... |
| A36_PUBLIC_ROLE | PASS | `01c7b3fd-0001-afab-0000-0000d231a20d` | [['Statement executed successfully.']] |
| A37_PUBLIC_PRIVILEGE_DENIED | FAIL | `01c7b3fd-0001-afab-0000-0000d231a211` | [[1]] |
| A38_RESTORE_ROLE | PASS | `01c7b3fd-0001-afaf-0000-0000d231c1fd` | [['Statement executed successfully.']] |
| A39_ACCOUNT_EDITION | OBSERVED | `01c7b3fd-0001-afab-0000-0000d231a215` | [] |
| A40_QAS_AUDIT | OBSERVED | `01c7b3fd-0001-afaf-0000-0000d231c201` | [[123, 0, 0]] |
| AC01_DROP_SEMANTIC | PASS | `01c7b3fd-0001-afab-0000-0000d231a219` | [['Drop statement executed successfully (SF_RESEARCH_20261013_SEMANTIC already dropped).']] |
| AC02_DROP_STREAM | PASS | `01c7b3fd-0001-afab-0000-0000d231a21d` | [['Drop statement executed successfully (SF_RESEARCH_20261013_STREAM already dropped).']] |
| AC03_DROP_SOURCE | PASS | `01c7b3fd-0001-afaf-0000-0000d231c205` | [['Drop statement executed successfully (SF_RESEARCH_20261013_STREAM_SOURCE already dropped).']] |
| AC04_DROP_RECOVERY | PASS | `01c7b3fd-0001-afaf-0000-0000d231c209` | [['Drop statement executed successfully (SF_RESEARCH_20261013_RECOVERY already dropped).']] |
| AC05_DROP_VIEW | PASS | `01c7b3fd-0001-afab-0000-0000d231a221` | [['SF_RESEARCH_20261013_VIEW successfully dropped.']] |
| AC06_DROP_LOAD | PASS | `01c7b3fd-0001-afaf-0000-0000d231c20d` | [['SF_RESEARCH_20261013_LOAD successfully dropped.']] |
| AC07_DROP_SINK | PASS | `01c7b3fd-0001-afaf-0000-0000d231c211` | [['SF_RESEARCH_20261013_STREAM_SINK successfully dropped.']] |
| AC08_DROP_STAGE | PASS | `01c7b3fd-0001-afaf-0000-0000d231c215` | [['SF_RESEARCH_20261013_STAGE successfully dropped.']] |
| AC09_DROP_FORMAT | PASS | `01c7b3fd-0001-afab-0000-0000d231a225` | [['SF_RESEARCH_20261013_CSV successfully dropped.']] |
| AC10_DROP_UDF | PASS | `01c7b3fd-0001-afab-0000-0000d231a229` | [['SF_RESEARCH_20261013_DOUBLE successfully dropped.']] |
| AC11_DROP_CHECK | PASS | `01c7b3fd-0001-afaf-0000-0000d231c219` | [['SF_RESEARCH_20261013_CHECK2 successfully dropped.']] |
| AC12_NO_TABLES | PASS | `01c7b3fd-0001-afab-0000-0000d231a22d` | [[0]] |
| AC13_SUSPEND_WAREHOUSE | PASS | `01c7b3fd-0001-afaf-0000-0000d231c21d` | [['Statement executed successfully.']] |
| N00_CONTEXT | OBSERVED | `01c7b402-0001-afaf-0000-0000d231c22d` | [['ACCOUNTADMIN', '{"roles":"ORGADMIN","value":"ALL"}', 'USER$DWICKYFERI', 'PUBLIC']] |
| N01_DATABASE | PASS | `01c7b402-0001-afaf-0000-0000d231c231` | [['Statement executed successfully.']] |
| N02_SCHEMA | PASS | `01c7b402-0001-afaf-0000-0000d231c235` | [['Schema SF_RESEARCH_20261013 successfully created.']] |
| N03_USE_SCHEMA | PASS | `01c7b402-0001-afab-0000-0000d231a251` | [['Statement executed successfully.']] |
| N04_CREATE_SOURCE | PASS | `01c7b402-0001-afaf-0000-0000d231c239` | [['Table SRC successfully created.']] |
| N05_STREAM_CREATE | PASS | `01c7b403-0001-afaf-0000-0000d231c261` | [['Stream DELTA successfully created.']] |
| N06_SOURCE_INSERT | PASS | `01c7b403-0001-afab-0000-0000d231a279` | [[2]] |
| N07_STREAM_READ | PASS | `01c7b403-0001-afab-0000-0000d231a27d` | [[1, 'INSERT', False], [2, 'INSERT', False]] |
| N08_STREAM_REREAD | PASS | `01c7b403-0001-afab-0000-0000d231a281` | [[2]] |
| N09_SINK_CREATE | PASS | `01c7b403-0001-afaf-0000-0000d231c265` | [['Table SINK successfully created.']] |
| N10_CONSUME | PASS | `01c7b403-0001-afab-0000-0000d231a285` | [[2]] |
| N11_OFFSET | PASS | `01c7b403-0001-afaf-0000-0000d231c269` | [[0]] |
| N12_SOURCE_UPDATE | FAIL | `01c7b403-0001-afab-0000-0000d231a289` | [[1, 0]] |
| N13_UPDATE_PAIR | PASS | `01c7b403-0001-afab-0000-0000d231a28d` | [[10, 'DELETE', True], [11, 'INSERT', True]] |
| N14_BEGIN | PASS | `01c7b403-0001-afab-0000-0000d231a291` | [['Statement executed successfully.']] |
| N15_CONSUME_TX | PASS | `01c7b403-0001-afab-0000-0000d231a295` | [[2]] |
| N16_ROLLBACK | PASS | `01c7b403-0001-afaf-0000-0000d231c26d` | [['Statement executed successfully.']] |
| N17_ROLLBACK_OFFSET | PASS | `01c7b403-0001-afaf-0000-0000d231c271` | [[2]] |
| N18_SEMANTIC_CREATE | PASS | `01c7b403-0001-afaf-0000-0000d231c275` | [['Semantic view SALES successfully created.']] |
| N19_SEMANTIC_QUERY | PASS | `01c7b403-0001-afab-0000-0000d231a299` | [['Bandung', 20], ['Jakarta', 11]] |
| N20_SEMANTIC_METADATA | OBSERVED | `01c7b403-0001-afab-0000-0000d231a29d` | [['SNOWFLAKE_LEARNING_DB', 'SF_RESEARCH_20261013', 'SALES', 'ORDERS', 'TOTAL_AMOUNT', 'NUMBER(38,0)', None, None, None]] |
| N21_RECOVERY_CREATE | PASS | `01c7b403-0001-afaf-0000-0000d231c279` | [['Table RECOVERY successfully created.']] |
| N22_RECOVERY_INSERT | PASS | `01c7b403-0001-afaf-0000-0000d231c27d` | [[1]] |
| N23_RECOVERY_DROP | PASS | `01c7b403-0001-afab-0000-0000d231a2a1` | [['RECOVERY successfully dropped.']] |
| N24_UNDROP | PASS | `01c7b403-0001-afab-0000-0000d231a2a5` | [['Table RECOVERY successfully restored.']] |
| N25_RECOVERY_VERIFY | PASS | `01c7b403-0001-afab-0000-0000d231a2a9` | [[7]] |
| N26_DISABLE_SECONDARY | PASS | `01c7b403-0001-afaf-0000-0000d231c281` | [['Statement executed successfully.']] |
| N27_PUBLIC | PASS | `01c7b403-0001-afab-0000-0000d231a2ad` | [['Statement executed successfully.']] |
| N28_PUBLIC_CONTEXT | OBSERVED | `01c7b403-0001-afab-0000-0000d231a2b1` | [['PUBLIC', '{"roles":"","value":""}']] |
| N29_PUBLIC_ACCOUNT_DENIED | EXPECTED_ERROR | `01c7b403-0001-afaf-0000-0000d231c285` | 002003 (02000): SQL compilation error: Schema 'SNOWFLAKE.ACCOUNT_USAGE' does not exist or not authorized. Your primary role PUBLIC must have USAGE or any other privilege granted on SCHEMA SNOWFLAKE.ACCOUNT_USAGE. |
| N30_PUBLIC_TABLE_DENIED | EXPECTED_ERROR | `01c7b403-0001-afaf-0000-0000d231c289` | 002003 (02000): SQL compilation error: Schema 'SNOWFLAKE_LEARNING_DB.SF_RESEARCH_20261013' does not exist or not authorized. Your primary role PUBLIC must have USAGE or any other privilege granted on SCHEMA SNOWFLAKE_LEA ... |
| N31_RESTORE_ROLE | PASS | `01c7b403-0001-afab-0000-0000d231a2b5` | [['Statement executed successfully.']] |
| N32_RESTORE_SECONDARY | PASS | `01c7b403-0001-afab-0000-0000d231a2b9` | [['Statement executed successfully.']] |
| N33_ROLE_VERIFY | OBSERVED | `01c7b403-0001-afaf-0000-0000d231c28d` | [['ACCOUNTADMIN', '{"roles":"ORGADMIN","value":"ALL"}']] |
| N34_FILEFORMAT | PASS | `01c7b403-0001-afaf-0000-0000d231c291` | [['File format CSV successfully created.']] |
| N35_STAGE | PASS | `01c7b403-0001-afaf-0000-0000d231c295` | [['Stage area FILES successfully created.']] |
| N36_PUT | OBSERVED | `01c7b403-0001-afab-0000-0000d231a2bd` | [['synthetic.csv', 'synthetic.csv', 41, 48, 'NONE', 'NONE', 'UPLOADED', '']] |
| N37_LOAD_CREATE | PASS | `01c7b403-0001-afaf-0000-0000d231c299` | [['Table LOADED successfully created.']] |
| N38_COPY | OBSERVED | `01c7b403-0001-afab-0000-0000d231a2c1` | [['files/synthetic.csv', 'LOADED', 2, 2, 1, 0, None, None, None, None]] |
| N39_LOAD_VERIFY | PASS | `01c7b403-0001-afab-0000-0000d231a2c5` | [[2, '30']] |
| N40_UNLOAD | OBSERVED | `01c7b403-0001-afab-0000-0000d231a2c9` | [[2, 41, 41]] |
| N41_GET | OBSERVED | `01c7b403-0001-afaf-0000-0000d231c29d` | [['out.csv', 41, 'DOWNLOADED', '']] |
| N42_PROCEDURE | PASS | `01c7b403-0001-afaf-0000-0000d231c2a1` | [['Function ADD_NUM successfully created.']] |
| N43_CALL | PASS | `01c7b403-0001-afab-0000-0000d231a2cd` | [[9]] |
| G01_ROW_POLICY | PASS | `01c7b404-0001-afaf-0000-0000d231c2a9` | [["Row access policy 'CITY_FILTER' is successfully created."]] |
| G02_MASK_POLICY | PASS | `01c7b404-0001-afab-0000-0000d231a2d9` | [['Masking policy CITY_MASK successfully created.']] |
| G03_POLICY_TABLE | PASS | `01c7b404-0001-afab-0000-0000d231a2dd` | [['Table POLICY_DATA successfully created.']] |
| G04_POLICY_INSERT | PASS | `01c7b404-0001-afaf-0000-0000d231c2b1` | [[2]] |
| G05_ROW_ATTACH | PASS | `01c7b404-0001-afaf-0000-0000d231c2b5` | [['Statement executed successfully.']] |
| G06_MASK_ATTACH | PASS | `01c7b404-0001-afab-0000-0000d231a2e1` | [['Statement executed successfully.']] |
| G07_POLICY_VERIFY | PASS | `01c7b404-0001-afaf-0000-0000d231c2b9` | [['Jakarta', '***']] |
| G08_ROW_DETACH | PASS | `01c7b404-0001-afaf-0000-0000d231c2bd` | [['Statement executed successfully.']] |
| G09_MASK_DETACH | PASS | `01c7b404-0001-afaf-0000-0000d231c2c1` | [['Statement executed successfully.']] |
| G10_RESTORED | PASS | `01c7b404-0001-afaf-0000-0000d231c2c5` | [['Bandung', 'beta'], ['Jakarta', 'alpha']] |
| F01_UPDATE_RESULT | PASS | `01c7b404-0001-afaf-0000-0000d231c2c9` | [[1, 0]] |
| F02_UPDATE_VERIFY | PASS | `01c7b404-0001-afaf-0000-0000d231c2cd` | [[12]] |
| T01_TASK_TABLE | PASS | `01c7b404-0001-afab-0000-0000d231a2e5` | [['Table TASK_RESULT successfully created.']] |
| T02_TASK_CREATE | PASS | `01c7b404-0001-afaf-0000-0000d231c2d1` | [['Task INSERT_ONCE successfully created.']] |
| T03_TASK_STATE | OBSERVED | `01c7b404-0001-afaf-0000-0000d231c2d5` | [['2026-10-13T20:24:37.940000+07:00', 'INSERT_ONCE', '01c7b404-529f-2f02-0000-000000000041', 'SNOWFLAKE_LEARNING_DB', 'SF_RESEARCH_20261013', 'ACCOUNTADMIN', '', 'COMPUTE_WH', None, '[]', 'suspended', 'INSERT INTO RECOVE ... |
| T04_TASK_EXECUTE | OBSERVED | `01c7b404-0001-afab-0000-0000d231a2e9` | [['Task INSERT_ONCE is scheduled to run immediately.']] |
| T05_TASK_HISTORY | OBSERVED | `01c7b405-0001-afaf-0000-0000d231c2f5` | [['SUCCEEDED', None, None, '01c7b404-0001-afbc-0000-d2310001501e']] |
| T06_TASK_VERIFY | PASS | `01c7b405-0001-afaf-0000-0000d231c2f9` | [[7], [8]] |
| D01_DT_CREATE | PASS | `01c7b405-0001-afab-0000-0000d231a311` | [['Dynamic table CITY_TOTALS successfully created.']] |
| D02_DT_QUERY | PASS | `01c7b406-0001-afaf-0000-0000d231c301` | [['Bandung', 20], ['Jakarta', 12]] |
| D03_DT_STATE | OBSERVED | `01c7b406-0001-afaf-0000-0000d231c305` | [['2026-10-13T20:25:09.179000+07:00', 'CITY_TOTALS', 'SNOWFLAKE_LEARNING_DB', 'SF_RESEARCH_20261013', '', 2, 1536, 'ACCOUNTADMIN', '', 'INCREMENTAL', None, 'COMPUTE_WH', '', "CREATE TRANSIENT DYNAMIC TABLE CITY_TOTALS SC ... |
| D04_DT_UPDATE | PASS | `01c7b406-0001-afab-0000-0000d231a31d` | [[1, 0]] |
| D05_DT_STALE | PASS | `01c7b406-0001-afab-0000-0000d231a321` | [['Bandung', 20], ['Jakarta', 12]] |
| D06_DT_REFRESH | OBSERVED | `01c7b406-0001-afaf-0000-0000d231c309` | [['SNOWFLAKE_LEARNING_DB.SF_RESEARCH_20261013.CITY_TOTALS', '{"insertedRows":1,"copiedRows":1,"deletedRows":1}', 1, '2026-10-13T20:26:07.051000+07:00']] |
| D07_DT_FRESH | PASS | `01c7b406-0001-afaf-0000-0000d231c311` | [['Bandung', 20], ['Jakarta', 13]] |
| D08_DT_DML_DENIED | ERROR | `01c7b406-0001-afab-0000-0000d231a325` | 002706 (23001): SQL Compilation error: Invalid Operation INSERT on Dynamic Table |
| D09_DT_HISTORY | OBSERVED | `01c7b406-0001-afab-0000-0000d231a329` | [['SUCCEEDED', 'INCREMENTAL', 'CREATION'], ['SUCCEEDED', 'INCREMENTAL', 'MANUAL']] |
| P01_PYTHON_UDF | PASS | `01c7b406-0001-afab-0000-0000d231a32d` | [['Temporary function PY_DOUBLE successfully created, but not validated.']] |
| P02_PYTHON_UDF_CALL | PASS | `01c7b406-0001-afaf-0000-0000d231c315` | [[12]] |
| P03_SNOWPARK_SP | PASS | `01c7b406-0001-afaf-0000-0000d231c319` | [['Function SNOWPARK_TOTAL successfully created.']] |
| P04_SNOWPARK_CALL | PASS | `01c7b406-0001-afab-0000-0000d231a331` | [['33']] |
| D10_DT_DML_RECHECK | EXPECTED_ERROR | `01c7b407-0001-afab-0000-0000d231a395` | 002706 (23001): SQL Compilation error: Invalid Operation INSERT on Dynamic Table |
| Z01_QAS_FINAL | OBSERVED | `01c7b407-0001-afaf-0000-0000d231c361` | [[216, 0, 0]] |
| Z02_METERING | OBSERVED | `01c7b407-0001-afaf-0000-0000d231c365` | [['2026-10-13T19:27:25+07:00', '2026-10-13T20:27:25+07:00', '0.045000000', '0.003515000']] |
| Z03_DROP_DT | PASS | `01c7b407-0001-afaf-0000-0000d231c369` | [['CITY_TOTALS successfully dropped.']] |
| Z04_DROP_TASK | PASS | `01c7b407-0001-afaf-0000-0000d231c36d` | [['INSERT_ONCE successfully dropped.']] |
| Z05_DROP_SCHEMA | PASS | `01c7b407-0001-afaf-0000-0000d231c371` | [['SF_RESEARCH_20261013 successfully dropped.']] |
| Z06_NO_SCHEMA | PASS | `01c7b407-0001-afab-0000-0000d231a399` | [[0]] |
| Z07_NO_TASK | PASS | `01c7b407-0001-afab-0000-0000d231a39d` | [] |
| Z08_NO_DT | PASS | `01c7b407-0001-afaf-0000-0000d231c375` | [] |
| Z09_SUSPEND | PASS | `01c7b407-0001-afab-0000-0000d231a3a1` | [['Statement executed successfully.']] |
| Z10_WAREHOUSE_VERIFY | OBSERVED | `01c7b407-0001-afaf-0000-0000d231c379` | [['COMPUTE_WH', 'SUSPENDED', 'STANDARD', 'X-Small', 1, 1, 0, 0, 0, 'Y', 'Y', 600, 'true', '', '', '', '', '2026-10-13T19:49:28.510000+07:00', '2026-10-13T20:23:06.499000+07:00', '2026-10-13T20:23:06.499000+07:00', 'ACCOU ... |
| U01_UI_QUERY_HISTORY | OBSERVED | `01c7b40b-0001-afab-0000-0000d231a3fd` | [['01c7b409-0001-afab-0000-0000d231a3dd', 'SELECT CURRENT_VERSION() AS engine_version,\n       CURRENT_REGION() AS region,\n       CURRENT_USER() AS user_name,\n       CURRENT_ROLE() AS primary_role,\n       CURRENT_WARE ... |
| U02_WAREHOUSE_STATE | OBSERVED | `01c7b40b-0001-afaf-0000-0000d231c3b5` | [['COMPUTE_WH', 'STARTED', 'STANDARD', 'X-Small', 1, 1, 1, 0, 0, 'Y', 'Y', 600, 'true', ' 100', '0', '0', '0', '2026-10-13T19:49:28.510000+07:00', '2026-10-13T20:31:24.592000+07:00', '2026-10-13T20:31:24.592000+07:00', ' ... |
| U03_FINAL_SUSPEND | PASS | `01c7b40b-0001-afaf-0000-0000d231c3b9` | [['Statement executed successfully.']] |
| U04_FINAL_STATE | OBSERVED | `01c7b40b-0001-afab-0000-0000d231a405` | [['COMPUTE_WH', 'SUSPENDED', 'STANDARD', 'X-Small', 1, 1, 0, 0, 0, 'Y', 'Y', 600, 'true', '', '', '', '', '2026-10-13T19:49:28.510000+07:00', '2026-10-13T20:31:24.592000+07:00', '2026-10-13T20:31:24.592000+07:00', 'ACCOU ... |

SQL lengkap dan hasil terstruktur tersedia di [events.jsonl](events.jsonl). Query browser dan task anak dicatat terpisah di [summary.json](summary.json).
