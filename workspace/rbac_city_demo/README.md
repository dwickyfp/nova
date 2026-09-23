# City RBAC acceptance fixture

This fixture tests one shared Nova Studio agent with two users who have the same
role and different row scopes. It uses only synthetic data.

| Object | Name | Purpose |
| --- | --- | --- |
| StarRocks database and table | `rbac_city_demo.city_sales` | Four rows with `city` and `amount` |
| Role | `rbac_city_reader` | SELECT on the table |
| Users | `rbac_jakarta`, `rbac_bandung` | Each has only the reader role |
| Ranger scopes | `city = 'Jakarta'`, `city = 'Bandung'` | Applied per user under the role |
| Semantic model | `rbac_city_sales` | Maps the table and `total_amount` metric |
| Shared agent | `City RBAC Agent` | Bound to the semantic model and granted to the role |

Passwords are generated into the gitignored `workspace/rbac_city_demo/.env` with
mode `0600`. Do not add that file to version control or copy its values into
documentation. The stable backend encryption keys live in the gitignored
`docker/.env` through `NOVA_SECRET_KEY` and `NOVA_FERNET_KEY`; both are required
to preserve saved provider credentials across backend restarts.

## Reproduce locally

Start the StarRocks, Ranger, proxy, and backend stack. With the backend container
running, copy the fixture and credentials into it, then seed the data and policy:

```sh
docker exec nova-backend mkdir -p /opt/nova/workspace/rbac_city_demo
docker cp workspace/rbac_city_demo/city_sales.ossie.yaml nova-backend:/opt/nova/workspace/rbac_city_demo/city_sales.ossie.yaml
docker cp workspace/rbac_city_demo/.env nova-backend:/tmp/nova-rbac-city-demo.env
docker exec -w /opt/nova/backend nova-backend python scripts/seed_city_rbac_demo.py
```

For a new fixture, run the seed once before copying credentials into the
container. Then save the generated credentials locally with mode `0600`:

```sh
docker cp nova-backend:/tmp/nova-rbac-city-demo.env workspace/rbac_city_demo/.env
chmod 600 workspace/rbac_city_demo/.env
```

Preserve that private file for subsequent runs. The seed updates the same
named role, users, policies, model, and agent when rerun.

Run the API, SQL Workspace, Studio stream, and MySQL protocol checks:

```sh
RBAC_DEMO_CREDENTIAL_FILE=workspace/rbac_city_demo/.env \
  backend/.venv/bin/python backend/scripts/verify_city_rbac_demo.py
```

Run the live browser check after starting Vite in `frontend/`:

```sh
cd frontend
npm run dev
node scripts/verify-city-rbac-ui.mjs
```

Set `NOVA_UI_URL` if Vite chooses a port other than 5173. The verifier tests
both users in Workspace and Studio, then asks the Jakarta agent for Bandung and
requires an empty result. The Python verifier also attempts to switch the
MySQL proxy session to `ACCOUNTADMIN` and requires a denial.
