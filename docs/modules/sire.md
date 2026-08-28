# Módulo SIRE — conciliación de registros

SIRE (Sistema Integrado de Registros Electrónicos de SUNAT) publica una
**propuesta** de los registros de compras y ventas de la empresa. Este módulo
descarga esa propuesta, la **concilia** contra el archivo de registros de la
empresa y genera un reporte de diferencias.

## Origen del motor

El motor de conciliación se **portó byte-idéntico** desde el repo original
`sire-backend`. Para no arriesgar la paridad, esos archivos se mantienen sin
tocar y quedan **excluidos del linter** (ver
[architecture/modularity.md](../architecture/modularity.md)):

```
infrastructure/parser/          → parseo de archivos SIRE (empresa y propuesta SUNAT)
infrastructure/reconciliation/engine.py, worker.py
infrastructure/report/          → generación del Excel de resultados
infrastructure/sunat/           → cliente de la API SIRE de SUNAT
```

El código **propio** que envuelve el motor (orquestador, casos de uso, API,
repositorios) sí se lintea y sigue el estándar del proyecto.

## Estructura

```
domain/         entities.py, ports.py     → entidades y puertos del dominio
application/    use_cases.py              → casos de uso
                credentials.py            → credenciales SOL (cifradas)
                file_mapping.py           → mapeo de columnas del archivo de la empresa
infrastructure/ parser/                   → lee archivos de la empresa y la propuesta SUNAT
                sunat/ (auth, base,        → descarga compras/ventas desde la API SIRE
                        compras, ventas)
                reconciliation/ (engine,   → compara y clasifica diferencias
                        orchestrator, worker)
                report/excel_generator    → Excel de conciliación
                repositories.py, models.py
```

## Flujo

```
1. Configura credenciales SOL (cifradas, por empresa)
2. Sube el archivo de registros de la empresa (con mapeo de columnas)
3. Inicia la conciliación                 ─► encola un job
   worker: descarga la propuesta SUNAT (compras y ventas)
           corre el engine (empresa vs SUNAT)
           genera el Excel de diferencias
4. Descarga el reporte
```

## Jobs, sesiones y Neon

La conciliación corre en modo **on-demand**: el proceso `web` la ejecuta al
crearla (`POST /jobs`) o reanudarla (`POST /jobs/{id}/resume`), en un pool de hilos
acotado por `SIRE_MAX_CONCURRENCY` — sin un worker que sondee, para que Neon pueda
suspenderse. `procesar_job` es asíncrono y el motor corre en un subproceso efímero.
Detalle y trade-offs en
[architecture/async-jobs.md](../architecture/async-jobs.md#ejecución-on-demand-todos-los-módulos).

> **Diferencia respecto al original:** en la plataforma la sesión y la memoria de
> proceso funcionan distinto a `sire-backend` (Neon + workers separados en lugar
> de estado en memoria). El manejo de sesiones breves de la API SIRE se adaptó a
> esa topología; el **motor** de conciliación permanece idéntico.

## Descarga desde SUNAT (API SIRE)

`infrastructure/sunat/` autentica contra la API SIRE (`auth.py`) y descarga la
propuesta de `compras.py` / `ventas.py`. Es la API oficial de registros — no
scraping.
