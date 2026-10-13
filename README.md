# Catalog API

Catalog API is the central service for cataloguing organizational units, projects, components, environments, and deployment evidence. R1 setup acceptance is implemented; R2 and R3 remain proposed integrations.

## Current service

- `GET /health` returns `200 OK` with `{"status":"ok"}` and does not require database configuration.
- `POST /api/v1/setups` accepts validated R1 OpenShift setup events after the catalog schema and R1 migrations are applied. It persists setup evidence and one pending synchronization outbox record atomically, supports semantic same-event replay, and does not call InvGate or create deployment state.

## Integration contract

The integration contract is versioned in this repository at [docs/contracts/contratos-integracion-catalogo.html](docs/contracts/contratos-integracion-catalogo.html). Its R1 setup contract is implemented by `POST /api/v1/setups`; R2 and R3 remain analysis proposals rather than active integrations.

- **R1 — OpenShift setup:** implemented as durable initial organizational, project, component, environment, setup-ticket, and pending-synchronization evidence.
- **R2 — DevHub access:** proposes recording DevHub self-management access for authorized users of an organizational unit.
- **R3 — OpenShift deployment:** proposes recording deployment attempts for existing components and environments, including deployment history and current state after success.

Refer to the detailed contract for R1 request and response shapes, idempotency, validation, privacy safeguards, and R2/R3 decisions that still require agreement.

## Configuration

The application recognizes `CATALOGO_DATABASE_URL`.

## Tests

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q
```
