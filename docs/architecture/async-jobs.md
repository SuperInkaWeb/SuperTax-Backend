# Trabajos asíncronos (cola sobre Postgres + workers)

Las tres automatizaciones son **largas** (minutos): Playwright navegando SUNAT,
OCR de PDFs escaneados, conciliación de miles de filas. No pueden correr dentro
del request HTTP. La plataforma usa una **cola sobre Postgres** con el estado, los
logs y el resultado de cada job.

Hay **dos modelos de ejecución** de esa cola:

- **On-demand (SUNAT, SIRE, Scanner — el modelo actual):** el propio proceso `web`
  ejecuta el job al lanzarlo, en un pool de hilos acotado. No hay proceso que
  sondee → cuando no hay trabajos, no hay actividad contra la base (Neon puede
  suspenderse). Ver [§ On-demand](#ejecución-on-demand-todos-los-módulos).
- **Worker que sondea (legado, opcional):** un proceso worker separado consulta la
  cola cada pocos segundos y procesa. Los tres `workers/*_worker.py` siguen
  existiendo y son seguros (por el claim), pero ya **no hacen falta** y se dejan a
  0 réplicas; mantienen la base activa 24/7.

> Ambos usan la misma tabla de jobs y el mismo `procesar_job`; solo cambia **quién
> lo dispara**. Los tres módulos se migraron al modelo on-demand para no consumir
> horas de cómputo en Neon estando inactivos.

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

## Ejecución on-demand (todos los módulos)

SUNAT, SIRE y Scanner ejecutan el job **dentro del proceso `web`** en el momento en
que el usuario lo lanza, sin un worker que sondee. Objetivo: no mantener a Neon
despierto con un sondeo 24/7 (ahorro de horas de cómputo en el plan gratuito).

```
POST /iniciar
  encolar_job            → INSERT status=en_cola, insumo a storage
  encolar_ejecucion      → submit(_despachar) a un pool de hilos acotado
                              _despachar: claim(en_cola→procesando) → procesar_job
```

Piezas comunes en `platform/tasks/executor.py`; la parte por módulo:
`modules/sunat/infrastructure/job_queue.py`,
`modules/sire/infrastructure/reconciliation/dispatch.py` y
`modules/scanner/application/jobs.py`:

- **Pool acotado por nombre**: `ThreadPoolExecutor(max_workers=<MÓDULO>_MAX_CONCURRENCY)`.
  N jobs en paralelo; los que excedan esperan turno (solo hay "cola" bajo carga
  real). El ejecutor es genérico y no depende de los módulos.
- **Claim atómico** (`repo.claim`): `UPDATE … SET procesando WHERE id=? AND
  status=en_cola`. Solo un proceso gana el job → evita el doble-procesado (incluso
  si además corre un worker de respaldo).
- **Recuperación al arranque** (`recuperar_pendientes`, vía `ModuleDescriptor.on_startup`
  + `lifespan` de FastAPI): marca `error` los `procesando` interrumpidos por un
  redeploy y **re-despacha** los `en_cola` que quedaron sin procesar. Asume una
  sola instancia `web` (misma suposición que el worker original).

Diferencias por módulo:

- **SUNAT**: `procesar_job` es síncrono; cada job levanta Chromium (Playwright), así
  que `SUNAT_MAX_CONCURRENCY` se ajusta a la RAM del `web`.
- **SIRE**: `procesar_job` es **async** (el pool corre `asyncio.run` en su hilo) y el
  motor de conciliación se ejecuta en un **subproceso efímero** (spawn). El job
  espera a SUNAT (~min) **sin** mantener conexión a la BD, así que gasta poca RAM
  salvo en el burst del motor. Se despacha al crear (`POST /jobs`) y al reanudar
  (`POST /jobs/{id}/resume`).
- **Scanner**: `procesar_job` es síncrono (OCR/extracción, CPU-bound). Se despacha
  al subir un documento (`POST /upload/auto`). Ajusta `SCANNER_MAX_CONCURRENCY` a
  los núcleos/RAM del `web`.

**Trade-off:** más simple y barato (sin polling), pero si el `web` se reinicia a
mitad de un job, ese job se corta y se marca `error` (reintentable). Con un worker
dedicado el job estaría aislado del ciclo del `web`.

## Despliegue

Con el modelo on-demand, el **`web` procesa los tres módulos**; ya no hace falta
ningún worker (ver [operations/deploy.md](../operations/deploy.md)):

```
web → uvicorn src.main:app   (comando por defecto)
      · procesa SUNAT, SIRE y Scanner on-demand
```

- **Escala `worker-sunat`, `worker-sire` y `worker-scanner` a 0 réplicas.** Su
  código sigue existiendo y es seguro por el claim (puedes reactivar cualquiera si
  algún día vuelves al modelo de worker), pero hoy son redundantes y solo
  mantendrían a Neon despierto.
- El `web` corre en la misma imagen Docker que traía Chromium (SUNAT) y Tesseract
  (Scanner), así que ya tiene todo lo necesario. Dale RAM/núcleos acordes a la suma
  de `SUNAT_MAX_CONCURRENCY` + `SIRE_MAX_CONCURRENCY` + `SCANNER_MAX_CONCURRENCY`.
