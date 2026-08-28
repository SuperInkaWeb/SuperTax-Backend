# Módulo SUNAT — descarga de comprobantes

Descarga masiva de comprobantes electrónicos (PDF/XML/CDR) desde SUNAT a partir
de una lista de comprobantes (RUC, tipo, serie, número), y los entrega por correo
y/o Google Drive.

## Flujo del usuario

```
1. Sube un Excel/CSV con los comprobantes  ─►  previsualizar (mapeo de columnas)
2. Confirma/corrige el mapeo               ─►  lista de comprobantes a descargar
3. Elige entrega (correo / Drive) y tipos  ─►  iniciar (encola el job)
4. Ve logs/progreso en vivo                ─►  descarga resultados / reintenta faltantes
```

## Entrada flexible con mapeo de columnas (tipo SIRE)

El Excel del usuario puede tener cualquier estructura. `infrastructure/input_parser.py`:

- Normaliza el contenido (xlsx/xls/csv) y **detecta** las columnas
  (RUC, tipo, serie, número) con heurísticas + nivel de confianza.
- Si la confianza es baja, devuelve cabeceras + muestra para que la UI muestre el
  **mapeo manual** (contrato idéntico al de SIRE) y reintente con `mapeo_manual`.
- Produce un **xlsx canónico** que consume el motor. La previsualización guarda
  ese canónico con un `preview_id` (caché en memoria con TTL) para no reprocesar
  al iniciar.

> **Nota de un bug ya resuelto:** el parser debe leer el DataFrame desde el
> contenido *normalizado*, no desde los bytes crudos, o falla con
> `UnicodeDecodeError` al detectar encoding sobre binario xlsx.

## Motor de descarga — híbrido Playwright + API `consultacpe`

La descarga **no** hace scraping frágil del portal para bajar archivos. Es un
enfoque híbrido (ver
[decisions/0003-sunat-consultacpe-hibrido.md](../decisions/0003-sunat-consultacpe-hibrido.md)):

1. **Login con Playwright** (`automation/login.py`): inicia sesión en SOL con la
   clave del usuario y navega al módulo.
2. **Captura del token** (`automation/token.py`): interceptores de red +
   escaneo de storage extraen el **Bearer token** de la API `consultacpe` desde
   la sesión del navegador.
3. **Descarga por HTTP** (`automation/consultacpe.py`): con ese token, pide cada
   archivo a la API oficial:

   ```
   GET https://api-cpe.sunat.gob.pe/v1/contribuyente/consultacpe/comprobantes/
       {rucEmisor}-{tipo}-{serie}-{numero}-{origen}/{cod}
   cod: 01=PDF · 02=XML · 03=CDR    origen=2 (recibido)
   → JSON { nomArchivo, valArchivo }   (valArchivo = base64 de un ZIP con el archivo)
   ```

   Requiere un **User-Agent de navegador** (el WAF descarta lo demás) y reintentos
   ante 500 intermitentes. Un 401 lanza `TokenExpirado`.

Esto es mucho más robusto que descargar clic-a-clic: obtiene los archivos reales
por API en vez de depender del render del portal.

## Entrega

- **Correo** (`automation/correo.py`): individual (un correo por comprobante) o
  agrupado. Usa una contraseña de aplicación de Gmail.
- **Google Drive** (`automation/drive.py`): sube PDF/XML a una **carpeta propia de
  la app** (`SuperTax {ruc}`), con scope acotado `drive.file`. Ver
  [integrations/google-drive.md](../integrations/google-drive.md).

## Credenciales

La clave SOL se puede guardar por empresa **cifrada (Fernet)** o pasarse en el
formulario. Nunca se registra en logs. El worker la descifra solo en memoria para
el login.

## Jobs (ejecución on-demand)

SUNAT usa la cola sobre Postgres, pero en modo **on-demand**: la descarga se
ejecuta **dentro del proceso `web`** al lanzarla (pool de hilos acotado por
`SUNAT_MAX_CONCURRENCY`), sin un worker que sondee — así Neon puede suspenderse
cuando no hay actividad. Detalle y trade-offs en
[architecture/async-jobs.md](../architecture/async-jobs.md#ejecución-on-demand-todos-los-módulos).

El `job_service` arma la config, resuelve el Excel (subido o caché de preview),
encola y **despacha** la ejecución. Endpoints principales: `preview-excel`,
`iniciar`, `forzar-faltantes` (reintenta solo los `Parcial`/`Error`), `cancelar`,
logs SSE.

## Reporte

`report_excel.py` genera un Excel con el resultado del job (estado por
comprobante, qué se descargó), descargable desde el `web`.
