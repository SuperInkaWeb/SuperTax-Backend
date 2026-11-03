# Informe — Consumo de memoria de DataFilter en Railway

> **Objetivo:** explicar por qué el proceso web consume RAM creciente al navegar
> archivos grandes en DataFilter, por qué esa memoria **no se libera** aunque se deje
> de usar, qué cuesta eso en Railway, y la solución recomendada.
>
> *Fuente de precios: https://railway.com/pricing (consultado 2026-11-02).*

---

## 1. Resumen ejecutivo

- La **subida** de archivos grandes (varios GB) es **directa a R2** y **no** carga el
  archivo en la RAM del servidor → por eso ves un **pico** y luego baja a MB. ✅
- El problema está en la **navegación/manipulación** (preview, valores, filtros): cada
  consulta usa **DuckDB** leyendo el Parquet desde R2. La RAM **sube con cada
  navegación y se queda alta** aunque dejes de usar DataFilter.
- **Causa raíz principal:** el proceso **web no tiene fijado un tope de memoria de
  DuckDB** en producción (variable `DUCKDB_WEB_MEMORY_LIMIT` vacía), así que DuckDB usa
  su límite por defecto (**~80 % de la RAM del host**). Sumado a cómo glibc **retiene**
  la memoria liberada, la RSS del web **sube en escalón y se queda en meseta**.
- Como el web es **always-on** y Railway **cobra por uso real por segundo**, esa RAM
  retenida es **costo continuo** (≈ **$10 por GB al mes** sostenido).
- **Solución inmediata (sin tocar código, solo variables de entorno en Railway):** fijar
  `DUCKDB_WEB_MEMORY_LIMIT` y afinar el asignador de memoria (`MALLOC_ARENA_MAX`). Con
  eso la RAM del web queda acotada y vuelve a bajar en inactividad.

---

## 2. Cómo funciona DataFilter (verificado en el código)

| Etapa | Qué hace | Dónde | RAM |
|---|---|---|---|
| **Subida** | El navegador sube el archivo **por partes, directo a R2** (multipart con URLs firmadas). El backend solo orquesta. | `infrastructure/storage_access.py`, `api/routes.py` | **Baja** (solo pasa metadatos) → el pico que ves es de red, no de RAM retenida. |
| **Ingesta** | Convierte el original a **Parquet** con DuckDB (streaming, `COPY`). Corre en un **subproceso efímero** que **muere al terminar** y devuelve la RAM al SO. | `infrastructure/ingest_dispatch.py`, `duckdb_engine.convert_to_parquet` | Acotada por `DUCKDB_MEMORY_LIMIT`; se recupera al morir el subproceso. |
| **Navegación / preview / valores / filtros** | Consultas DuckDB sobre el Parquet (`read_parquet('s3://…')`) con `LIMIT/OFFSET` y, si ordenas, `ORDER BY`. **Corre en el PROCESO WEB** (no en subproceso). | `duckdb_engine.preview()`, `distinct_values()`, `query.py` | **Aquí está el problema.** |
| **Análisis / descargas** (pivot, dedupe, reemplazo, export) | Operaciones pesadas, **serializadas** por un semáforo global (`DATAFILTER_MAX_CONCURRENCY`). | `infrastructure/heavy.py` (`gate()`), subprocesos | Acotada por el gate + límite de memoria. |

**Detalle importante:** cada consulta abre una conexión DuckDB nueva y, al terminar,
la **cierra** y llama a `malloc_trim(0)` para devolver RAM al SO
(`duckdb_engine._session()`). O sea, **el diseño sí intenta liberar** — pero no alcanza
en producción por lo que se explica abajo.

---

## 3. El problema observado (reportado y reproducido en la captura)

- Al **navegar** las filas de un archivo grande (p. ej. varios GB), la **RAM sube cada
  vez** que se navega/filtra.
- La memoria **no vuelve a bajar** cuando se deja de usar: se queda en **meseta alta**
  aunque la automatización esté inactiva.
- En **uso simultáneo** (varios usuarios navegando a la vez) el consumo se **multiplica**.
- En la gráfica de Railway se ve la RAM subiendo en **escalones** hasta ~1.62 GB y
  manteniéndose, en vez de regresar a la línea base (~300–500 MB).

> Aclaración técnica a tu intuición (“solo se muestran unas líneas de muestra pero se
> carga todo”): la preview pide **solo una página** (`LIMIT/OFFSET`), **no** trae todas
> las filas al resultado. Pero para **servir esa página** DuckDB sí **escanea** el
> Parquet —y si hay `ORDER BY` lo **ordena**, y si el `OFFSET` es profundo recorre hasta
> ahí—, leyendo bloques desde R2. Ese trabajo **temporal** es el que infla la RAM; el
> problema real es que **no se devuelve** al SO.

---

## 4. Causa raíz (del código)

**(A) El web no tiene tope de memoria de DuckDB en producción.**
Al arrancar, el web ejecuta
`set_memory_limit(DUCKDB_WEB_MEMORY_LIMIT or DUCKDB_MEMORY_LIMIT)`
(`ingest_dispatch.recuperar_pendientes`). **Ambas variables están vacías por defecto**,
así que no se fija ningún `memory_limit` y **DuckDB usa su default (~80 % de la RAM del
host)**. En un contenedor con 32 GB de límite, una sola consulta puede crecer muchísimo
antes de volcar a disco. → **Es casi seguro el factor dominante.**

**(B) glibc retiene la memoria liberada (meseta de RSS).**
DuckDB libera sus buffers al cerrar la conexión y el código llama `malloc_trim(0)`, pero
glibc mantiene **múltiples arenas** y **fragmenta**; `malloc_trim` no siempre devuelve
todo al SO. Resultado: la RSS **sube en escalón y se queda** en el pico, que es justo lo
que ves.

**(C) La preview NO está serializada (no pasa por el gate).**
Por diseño, el preview paginado se deja fuera del semáforo para que el scroll sea fluido
(`heavy.py`). La contra: **varias navegaciones simultáneas** corren en paralelo y **suman
RAM** sin tope de concurrencia.

**(D) `OFFSET` profundo + `ORDER BY`.**
Navegar “lejos” (OFFSET alto) obliga a recorrer todas las filas previas; si además hay
orden, DuckDB **ordena** el conjunto filtrado antes de paginar. En archivos grandes, eso
es mucho trabajo (y RAM) **por cada página**.

**(E) Lectura desde R2 por consulta (`s3://…`).**
Cada consulta lee bloques del Parquet desde R2 vía httpfs. Es correcto (no se guarda el
archivo local), pero cada navegación implica transferencia + buffers temporales.

---

## 5. Impacto económico en Railway

Railway **cobra por uso real, por segundo**, no por el límite del plan:

| Recurso | Precio por segundo | Equivalente mensual |
|---|---|---|
| **RAM** | $0.00000386 / GB·s | **≈ $10 / GB al mes** |
| **vCPU** | $0.00000772 / vCPU·s | **≈ $20 / vCPU al mes** |
| Egreso de red | — | $0.05 / GB |

Planes: **Hobby** $5/mes ($5 de uso incluido, hasta 8 GB·48 vCPU por servicio); **Pro**
$20/mes ($20 incluido, hasta 1 TB·1000 vCPU). El uso por encima del crédito se cobra a
las tarifas de arriba.

**Por qué la “RAM que no se limpia” cuesta dinero:** el web es **always-on**, así que se
paga por el **promedio de RSS sostenido 24/7**. Como la memoria se queda en meseta en
vez de volver a la base, se paga esa meseta **todo el tiempo**, aunque nadie esté usando
DataFilter.

**Ejemplos (una réplica, sostenido todo el mes):**
- RSS retenida **1.62 GB** → **≈ $16.2 / mes** solo por esa memoria.
- Si tras el fix baja a **~0.4 GB** en inactividad → **≈ $4 / mes** → **ahorro ≈ $12 /
  mes por réplica**.
- Uso simultáneo que empuja a **8 GB** sostenidos → **≈ $80 / mes** (y riesgo de OOM).

*(Cálculo: GB × $10/GB-mes. Son estimaciones sobre el promedio mensual; el cobro real es
la integral del consumo en el tiempo.)*

---

## 6. Solución

### 6.1 Inmediato — solo variables de entorno en Railway (sin desplegar código)

En el servicio **web** de Railway, agregar:

| Variable | Valor sugerido | Efecto |
|---|---|---|
| `DUCKDB_WEB_MEMORY_LIMIT` | `512MB` (o `768MB`) | **Tope de RAM de DuckDB en el web.** Si una consulta lo supera, **vuelca a disco** en vez de inflar la RAM. Es el fix principal. |
| `MALLOC_ARENA_MAX` | `2` | Reduce la fragmentación de glibc (menos arenas) → `malloc_trim` **sí** devuelve la RAM al SO y la RSS baja en inactividad. |
| `MALLOC_TRIM_THRESHOLD_` | `131072` | Hace que glibc devuelva bloques al SO más agresivamente. |
| `DUCKDB_TEMP_DIR` | (opcional) una ruta escribible | Carpeta de volcado a disco cuando se supera el límite. Por defecto usa el temporal del SO (sirve). |

> Estas cuatro son **configuración**, no requieren cambios de código ni nuevo deploy
> (Railway reinicia el servicio al guardarlas). Con `DUCKDB_WEB_MEMORY_LIMIT` + 
> `MALLOC_ARENA_MAX=2` se ataca directamente lo que reportas: el web deja de acumular y
> vuelve a la base al quedar inactivo.

### 6.2 Corto plazo — cambios de código pequeños

1. **Acotar la concurrencia del preview** (semáforo liviano, p. ej. 3–4 simultáneos):
   evita que muchas navegaciones en paralelo multipliquen la RAM, sin frenar el scroll
   de un usuario.
2. **Página con tope sensato** y evitar `OFFSET` muy profundo: para saltos grandes,
   paginación **por clave** (keyset: `WHERE orden > último`) en vez de `OFFSET`, que
   recorre todo lo anterior.
3. **Ordenar solo si el usuario lo pide**: no aplicar `ORDER BY` por defecto en la
   preview (ordenar un archivo enorme por cada página es caro).

### 6.3 Arquitectura — si el problema persiste bajo carga

4. **Preview en subproceso efímero o worker reciclado**: igual que la ingesta, para que
   la RAM se devuelva al SO al terminar. Tiene costo de latencia por navegación, así que
   solo si 6.1 + 6.2 no bastan.
5. **Escalar con límite**: con el tope de memoria puesto, el web se mantiene chico y el
   plan (Hobby/Pro) rinde más; si crece la carga, mover DataFilter a su propio servicio.

### 6.4 Cómo medir el antes/después

El código ya registra la RSS (`heavy.log_rss`). Para validar: navegar un archivo grande,
mirar la gráfica de Memory en Railway **antes** (sube y se queda) y **después** de las
variables (sube en el pico y **vuelve a bajar**).

---

## 7. Qué adjuntar al informe (Railway)

### 7.1 Capturas de Railway
1. **Metrics → Memory** del servicio web (la gráfica que ya tienes, con el escalón hasta
   ~1.62 GB) → evidencia del **antes**. Idealmente con el rango de tiempo donde se ve el
   crecimiento al navegar.
2. **Metrics → CPU** del mismo servicio en el mismo rango (para mostrar el trabajo de las
   consultas).
3. **Metrics → Memory** **después** de aplicar `DUCKDB_WEB_MEMORY_LIMIT` + 
   `MALLOC_ARENA_MAX` (debe verse plano/bajo al dejar de usar) → evidencia del **después**.
4. **Usage** (Settings → Usage del workspace, o la pestaña *Usage* del proyecto):
   el consumo de **RAM (GB)** y **vCPU** acumulado y su **costo**, para cuantificar el
   gasto.
5. **Billing / Plan**: el plan actual (Hobby/Pro) y el crédito incluido, para contextar
   cuánto del gasto ya cubre el plan y cuánto es excedente.

### 7.2 Precios a citar (Railway, 2026-11-02)
- RAM: **$0.00000386 / GB·s ≈ $10 / GB al mes**.
- vCPU: **$0.00000772 / vCPU·s ≈ $20 / vCPU al mes**.
- Egreso: **$0.05 / GB**. Object Storage: **$0.015 / GB-mes**.
- Planes: **Hobby $5/mes** ($5 incluido, 8 GB/48 vCPU máx. por servicio) · **Pro $20/mes**
  ($20 incluido, 1 TB/1000 vCPU).

---

## 8. Conclusión

El consumo **no viene de la subida** (esa es directa a R2 y liviana), sino de las
**consultas de navegación** que corren en el web **sin tope de memoria** y cuya RAM
**glibc no devuelve** al SO. Es un problema **mayormente de configuración**: fijar
`DUCKDB_WEB_MEMORY_LIMIT` y afinar el asignador (`MALLOC_ARENA_MAX`) lo resuelve en lo
inmediato y sin deploy, con un ahorro directo (≈ $10 por GB-mes que hoy se paga
“retenido”). Los ajustes de código (concurrencia del preview, keyset, orden opcional)
son refuerzos para uso simultáneo y archivos muy grandes.

---

*Informe técnico — DataFilter / Railway. Preparado 2026-11-02.*
