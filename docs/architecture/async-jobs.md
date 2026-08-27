# Trabajos asíncronos (cola sobre Postgres + workers)

Las tres automatizaciones son **largas** (minutos): Playwright navegando SUNAT,
OCR de PDFs escaneados, conciliación de miles de filas. No pueden correr dentro
del request HTTP. La plataforma usa una **cola sobre Postgres** consumida por
**procesos worker separados** — sin Redis/Celery.

## Por qué Postgres y no una cola dedicada

- Ya hay Postgres; una tabla de jobs + `FOR UPDATE SKIP LOCKED` da exclusión
  mutua real sin infraestructura extra (KISS/YAGNI).
- El estado del job, sus logs y su resultado viven en la misma transacción y base
  que el resto del dominio.

## Anatomía

Cada módulo tiene su propia cola (independencia entre módulos), pero todos siguen
el mismo patrón. Ejemplo con SUNAT:

| Pieza | Archivo | Rol |
|---|---|---|
| Tabla de jobs | `…/infrastructure/models.py` | estado (`en_cola`/`procesando`/`completado`/`error`/`cancelado`), config cifrada, ruta del insumo |
| Encolar | `job_queue.encolar_job` | cifra la config, sube el insumo a storage, crea la fila `en_cola` |
| Procesar | `job_queue.procesar_job` | descarga el insumo, corre el motor, marca el estado final |
| Worker | `workers/<m>_worker.py` | bucle que reclama y procesa jobs |

## Ciclo de vida de un job

```
web (API)                         worker (proceso aparte)
─────────                         ───────────────────────
POST /iniciar
  └─ encolar_job
       cifra config (Fernet)
       storage.save(insumo)
       INSERT job status=en_cola ──►  bucle cada 5 s:
       devuelve job_id                  claim_next_job()
                                          SELECT … status=en_cola
                                          ORDER BY created_at LIMIT 1
                                          FOR UPDATE SKIP LOCKED
                                          UPDATE status=procesando
                                        procesar_job(storage, job_id)
                                          storage.read(insumo)
                                          corre el motor
                                          guarda logs/resultado en Postgres
                                          UPDATE status=completado|error|cancelado
```

### Reclamo concurrente

`FOR UPDATE SKIP LOCKED` permite **correr varios workers del mismo tipo en
paralelo** sin doble-procesar: cada uno bloquea una fila distinta y salta las ya
bloqueadas. La concurrencia se ajusta con el número de procesos, no con un
parámetro de código.

### Logs y progreso en vivo

El motor escribe líneas mediante *shims* (`_Sink.put`) que insertan en una tabla
de logs (`SunatJobLogModel`, etc.). El frontend las lee por streaming (SSE). Así
el **motor no se modifica**: recibe objetos con la interfaz que ya esperaba
(`.put()`, `.is_set()`), pero por detrás escriben/consultan Postgres.

### Cancelación

Cooperativa: `POST /cancelar` marca `cancel_requested=true`; el motor consulta un
`_PgCancelFlag.is_set()` (cacheado ~2 s) entre pasos y se detiene.

### Recuperación tras reinicio

Al arrancar, el worker marca como `error` los jobs que quedaron en `procesando`
(un redeploy los interrumpió y nadie los retoma, porque solo se reclaman los
`en_cola`). El detalle queda en la tabla de logs.

### Limpieza

`procesar_job` usa un `try/finally`: pase lo que pase, borra el directorio
temporal del job y el insumo en storage, y persiste el estado final. Un fallo de
un job **nunca** tumba al worker (el bucle captura, registra y sigue).

## Despliegue

Web y workers comparten **la misma imagen Docker** pero se corren como servicios
distintos (ver [operations/deploy.md](../operations/deploy.md)):

```
web            → uvicorn src.main:app         (comando por defecto)
worker-sunat   → python -m workers.sunat_worker
worker-sire    → python -m workers.sire_worker
worker-scanner → python -m workers.scanner_worker
```

> Los workers son **obligatorios**: sin el worker de un módulo, sus jobs se
> quedan `en_cola` para siempre.
