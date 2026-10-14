# API de Catálogo

API central para registrar reparticiones, proyectos, componentes, entornos, accesos DevHub y evidencia de despliegues.

## Estado actual

Los tres contratos de escritura están implementados, verificados y publicados:

| Contrato | Endpoint | Propósito |
| --- | --- | --- |
| R1 · Setup OpenShift | `POST /api/v1/setups` | Crea o completa el registro inicial de proyecto, componente y entornos. |
| R2 · Accesos DevHub | `POST /api/v1/devhub-accesses` | Registra accesos de autogestión y las personas autorizadas. |
| R3 · Deployment OpenShift | `POST /api/v1/deployments` | Registra intentos de despliegue sobre componentes y entornos existentes. |

Además, `GET /health` responde `200 OK` con `{"status":"ok"}` y no requiere configuración de base de datos.

## Comportamientos principales

- Los tres endpoints aceptan reintentos semánticamente idénticos mediante `event_id`, sin duplicar registros.
- R1 persiste evidencia de setup y una sincronización pendiente; no crea historial de despliegues.
- R2 crea un acceso por evento, deduplica CUIL autorizados y no llama a InvGate ni genera outbox.
- R3 registra historial de despliegues; sólo un resultado exitoso puede actualizar el estado vigente del componente y entorno.
- R1 y R3 dejan una sincronización pendiente para el flujo posterior con InvGate. La integración remota con InvGate todavía no está implementada.

## Ejecutar localmente

Los endpoints de escritura requieren una base de datos con el esquema legado disponible y las migraciones aplicadas.

```bash
cd /ruta/a/catalogo-api

export CATALOGO_DATABASE_URL='mysql+pymysql://USUARIO:PASSWORD@HOST:PUERTO/BASE?charset=utf8mb4'

PYTHONPATH=. .venv/bin/alembic upgrade head
PYTHONPATH=. .venv/bin/uvicorn app.main:app --reload
```

Con el servicio levantado:

```bash
curl -i http://127.0.0.1:8000/health
```

La documentación interactiva de FastAPI está disponible en [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

## Contrato de integración

El contrato funcional de referencia está versionado en [docs/contracts/contratos-integracion-catalogo.html](docs/contracts/contratos-integracion-catalogo.html).

El documento incluye también contratos futuros de sincronización con InvGate y consultas de lectura. Esos contratos no forman parte de la implementación actual.

## Pruebas

Ejecutar la suite completa:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q
```

Para pruebas manuales, usar una base no productiva. R1 debe ejecutarse antes de R3 si se utiliza el mismo componente y entorno; R2 es independiente.
