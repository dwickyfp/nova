# Module 11: User & Access Control

> Manage users, roles, privileges, and authentication.

---

## User Management

### Operations

| Action | SQL |
|--------|-----|
| Create user | `CREATE USER 'username' IDENTIFIED BY 'password'` |
| Alter user | `ALTER USER 'username' ...` |
| Drop user | `DROP USER 'username'` |
| Show users | `SHOW USERS` |
| Set password | `SET PASSWORD FOR 'username' = PASSWORD('...')` |
| Grant role | `GRANT role_name TO USER 'username'` |
| Revoke role | `REVOKE role_name FROM USER 'username'` |

---

## Role Management

| Action | SQL |
|--------|-----|
| Create role | `CREATE ROLE <role_name>` |
| Drop role | `DROP ROLE <role_name>` |
| Show roles | `SHOW ROLES` |
| Show grants | `SHOW GRANTS [FOR USER 'username']` / `SHOW GRANTS [FOR ROLE role_name]` |
| Set default role | `SET DEFAULT ROLE <role> TO USER 'username'` |

---

## Privilege Types

### Object Privileges

| Object | Privileges |
|--------|-----------|
| **Catalog** | CREATE EXTERNAL CATALOG, DROP |
| **Database** | CREATE TABLE, CREATE VIEW, CREATE FUNCTION, CREATE MV, ALTER, DROP, USAGE |
| **Table** | SELECT, INSERT, UPDATE, DELETE, ALTER, DROP |
| **View** | SELECT, ALTER, DROP |
| **MV** | SELECT, ALTER, DROP, REFRESH |
| **Function** | USAGE, ALTER, DROP |
| **Pipe** | ALTER, DROP |
| **Resource Group** | ALTER, USAGE |
| **Storage Volume** | CREATE, ALTER, DROP, USAGE |
| **System** | GRANT, NODE, OPERATE, CREATE USER, CREATE ROLE |

---

## Authentication

| Method | Description |
|--------|-------------|
| Native | Username + password |
| LDAP | LDAP directory authentication |
| OAuth 2.0 | OAuth integration |
| Security Integration | External auth providers |

---

## User Management UI

### User List

```
┌─ Users ─────────────────────────────────────────────────┐
│                                                          │
│  [+ Create User]                                         │
│                                                          │
│  Username    Roles              Auth     Last Login      │
│  admin       admin              Native   2 min ago      │
│  analyst     read_only          LDAP     1 hour ago     │
│  etl_user    etl_writer         Native   5 min ago      │
│  readonly    read_only          Native   3 hours ago    │
└──────────────────────────────────────────────────────────┘
```

### Create User

```
┌─ Create User ───────────────────────────────────────────┐
│                                                          │
│  Username: [analyst                           ]          │
│  Password: [••••••••••••                      ] 🔒       │
│  Auth: [Native ▼]                                        │
│                                                          │
│  Roles:                                                  │
│  [✓] read_only                                          │
│  [ ] etl_writer                                         │
│  [ ] admin                                              │
│                                                          │
│  [Create User]                                           │
└──────────────────────────────────────────────────────────┘
```

### Grant Matrix

```
┌─ Privileges: etl_writer ────────────────────────────────┐
│                                                          │
│  Database: DATALAKE                                      │
│  ┌──────────┬────────┬────────┬────────┬────────┐       │
│  │ Object   │ SELECT │ INSERT │ ALTER  │ DROP   │       │
│  ├──────────┼────────┼────────┼────────┼────────┤       │
│  │ orders   │   ✓    │   ✓    │        │        │       │
│  │ payments │   ✓    │   ✓    │   ✓    │        │       │
│  │ staging  │   ✓    │   ✓    │   ✓    │   ✓    │       │
│  └──────────┴────────┴────────┴────────┴────────┘       │
│  [+ Grant Privilege]                                     │
└──────────────────────────────────────────────────────────┘
```
