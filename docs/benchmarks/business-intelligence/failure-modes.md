# Failure-mode acceptance map

The tests below provide deterministic evidence unless the row names a live
engine test. Passing one layer does not establish the other layers. The
[delivery matrix](delivery.md) retains the complete acceptance requirements.

| Failure mode | Focused evidence |
| --- | --- |
| Ambiguous metrics | `test_intelligence_autopilot.py`: competing domain definitions remain explicit; `test_intelligence_corpus.py`: the scripted planner refuses ambiguity |
| Contradictory rules | `test_intelligence_knowledge.py`: competing definitions survive correction and reviewed resolution |
| Stale knowledge | `test_intelligence_knowledge.py`: revoked definitions are hidden and stale versions remain explicit |
| Changed semantic definitions | `test_intelligence_engine.py`: old monitors stop; `test_intelligence_publication.py`: recovery cannot replace a newer publication |
| Unsupported causal claims | `test_intelligence_numerical.py`: correlation remains association and unsupported designs remain insufficient; the Studio trajectory and engine API reject outcome-selected causal cohorts before query execution |
| Unrelated deployment | `test_intelligence_engine.py`: timeline evidence remains temporal association |
| Seasonal variation | `test_intelligence_numerical.py`: baseline noise suppresses an incident; `test_intelligence_engine.py`: matched weekday baselines and exact reconciliation |
| Small samples | `test_intelligence_engine.py`: low-volume drops produce no News; numerical tests check the sample floor |
| Business-policy denial | `test_intelligence_decisions.py`: denial and changed policy prevent stale approval |
| Dataset denial | `test_intelligence_access.py`: Ranger outage cannot fall back to native grants; engine tests exercise revocation and different principals |
| Worker interrupted between writes | `test_intelligence_engine.py`: investigation projection recovery; decision and publication tests recover journal write boundaries |
| Duplicate incidents | `test_intelligence_engine.py`: repeated cycles retain incident identity; `test_intelligence_metadata.py`: real-engine cooldown crosses calendar buckets |
| Overlapping decisions | `test_intelligence_numerical.py`: attributed impact stays unavailable |
| Missing or late outcomes | `test_intelligence_engine.py`: empty aggregates retain null observations; `test_intelligence_studio_live.py`: pending, missing, revealed future observations and repeat evaluation |
| Provider outage | `tests/eval/test_intelligence_learning_work.py`: failure retains a pending source and the retry completes once |
| Correction reversal | `test_intelligence_knowledge.py`: evidence and competing definitions remain in revision history |
| Malicious memory | `tests/eval/test_agent_memory.py`: credentials and untrusted speech acts do not enter business memory |
| External claim presented as internal truth | `tests/eval/test_agent_memory.py`: external claims are excluded by extraction admission |

The patched-FE lifecycle test is
`tests/integration/test_intelligence_ranger_engine.py`. It requires an isolated
Ranger installation and `NOVA_INTELLIGENCE_RANGER_ACCEPTANCE=1`; ordinary
integration leaves it skipped. It exercises same-role principals with different
row filters, persisted decisions, shared listings, graph endpoints, role
switching, masking changes and revocation. Its execution result must be recorded
separately from deterministic mocks and from the repository's existing Ranger
proxy/tool acceptance.

Browser tests cover hidden evidence after an authentication epoch change and
switching between context nodes on the same semantic version. Actual browser
flows against a running API remain a separate acceptance requirement.
