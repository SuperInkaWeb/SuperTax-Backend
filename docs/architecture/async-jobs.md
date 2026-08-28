# Trabajos asíncronos (cola sobre Postgres + workers)

Las tres automatizaciones son **largas** (minutos): Playwright navegando SUNAT,
OCR de PDFs escaneados, conciliación de miles de filas. No pueden correr dentro
del request HTTP. La plataforma usa una **cola sobre Postgres** con el estado, los
logs y el resultado de cada job.

Hay **dos modelos de ejecución** de esa cola:

- **On-demand (SUNAT):** el propio proceso `web` ejecuta el job al lanzarlo, en un
  pool de hilos acotado. No hay proceso que sondee → cuando no hay trabajos, no hay
  actividad contra la base (Neon puede suspenderse). Ver [§ On-demand](#ejecución-on-demand-sunat).
- **Worker que sondea (SIRE, Scanner):** un proceso worker separado consulta la
  cola cada pocos segundos y procesa. Más aislado, pero mantiene la base activa.

> Ambos usan la misma tabla de jobs y el mismo `procesar_job`; solo cambia **quién
> lo dispara**. SUNAT se migró al modelo on-demand para no consumir horas de cómputo
> en Neon estando inactivo.

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

## Ejecución on-demand (SUNAT)

SUNAT no usa un worker que sondea, sino que ejecuta el job **dentro del proceso
`web`** en el momento en que el usuario lo lanza. Objetivo: no mantener a Neon
despierto con un sondeo 24/7 (ahorro de horas de cómputo en el plan gratuito).

```
POST /iniciar
  encolar_job            → INSERT status=en_cola, insumo a storage
  encolar_ejecucion      → submit(_despachar) a un pool de hilos acotado
                              _despachar: claim(en_cola→procesando) → procesar_job
```

Piezas (`platform/tasks/executor.py` + `modules/sunat/infrastructure/job_queue.py`):

- **Pool acotado por nombre**: `ThreadPoolExecutor(max_workers=SUNAT_MAX_CONCURRENCY)`.
  N descargas en paralelo; las que excedan esperan turno (solo hay "cola" bajo
  carga real). Cada una levanta Chromium, así que el tope se ajusta a la RAM del
  `web`. El ejecutor es genérico y no depende de los módulos.
- **Claim atómico** (`SqlSunatJobRepository.claim`): `UPDATE … SET procesando
  WHERE job_id=? AND status=en_cola`. Solo un proceso gana el job → evita el
  doble-procesado (incluso si además corre un worker de respaldo).
- **Recuperación al arranque** (`recuperar_pendientes`, vía `ModuleDescriptor.on_startup`
  + `lifespan` de FastAPI): marca `error` los `procesando` interrumpidos por un
  redeploy y **re-despacha** los `en_cola` que quedaron sin procesar. Asume una
  sola instancia `web` (misma suposición que el worker original).

**Trade-off:** más simple y barato (sin polling), pero si el `web` se reinicia a
mitad de un job, ese job se corta y se marca `error` (reintentable). Con un worker
dedicado el job estaría aislado del ciclo del `web`.

## Despliegue

Web y workers comparten **la misma imagen Docker** pero se corren como servicios
distintos (ver [operations/deploy.md](../operations/deploy.md)):

```
web            → uvicorn src.main:app         (comando por defecto)
                 · procesa las descargas SUNAT on-demand (no necesita worker)
worker-sire    → python -m workers.sire_worker
worker-scanner → python -m workers.scanner_worker
```

- **SUNAT ya no necesita `worker-sunat`**: escala ese servicio a **0 réplicas**
  (su código sigue existiendo y es seguro por el claim, pero es redundante).
- **SIRE y Scanner** siguen siendo workers que sondean y **son obligatorios**: sin
  el worker de ese módulo, sus jobs se quedan `en_cola`. (Pendiente migrarlos al
  modelo on-demand.)
