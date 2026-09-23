# Authentication, access, governance, and settings

Keywords: user, users, role, roles, ACCOUNTADMIN, grant, permission, authentication, Ranger, data access, row access, masking, tag, lineage, governance, variables, admin settings, audit, audit trail.

Nova authenticates users against StarRocks, then applies their StarRocks privileges and active role to data operations. Users and Roles appear under Access Control. `ACCOUNTADMIN` is Nova's protected super-user role; Nova blocks dropping or revoking it. A granted role must be activated for its privileges to apply to a session. Nove may inspect role access with its typed tool; granting access follows the tool's consent and authorization checks. Do not infer a caller's grants from a product description.

Data Access connects Nova to Ranger policy administration. Ranger can enforce table, row, and column policies; a source read from AI Search, Semantic Views, or Feature Store must still use the caller's governed identity. Data Governance also includes masking, tags, and lineage concepts. Audit records actions and query history. Credentials must not appear in UI, API responses, metadata tables, logs, or assistant answers.

Admin Settings exposes configuration and variables appropriate to the user's role. Session variables affect a connection; global variables affect the cluster and need sufficient privilege. Password and network policy changes are administrative operations, not ordinary read-only queries.

Implementation references: docs/11-user-access-control.md, docs/18-authentication.md, docs/20-data-governance.md, docs/22-variables-settings.md, docs/29-ranger-access-control.md; backend/app/modules/access_control/ and backend/app/modules/assistant/tools/role_access.py. This guidance does not verify current grants or Ranger policy propagation.
