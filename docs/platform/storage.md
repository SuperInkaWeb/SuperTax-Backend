# Almacenamiento de archivos

Los tres módulos generan y consumen archivos (Excel de insumo, PDF/XML
descargados, reportes). El acceso se abstrae detrás de un **puerto**
(`platform/storage/base.py`) con dos adaptadores.

## El puerto `FileStorage`

```python
class FileStorage(ABC):
    def save(path, content: bytes) -> str      # guarda y devuelve el storage_path
    def open_write(path)                        # context manager de escritura en streaming
    def size(storage_path) -> int               # tamaño sin leer
    def read(storage_path) -> bytes             # lee el contenido
    def delete(storage_path) -> None            # elimina si existe
    def exists(storage_path) -> bool            # existe sin leer
```

`open_write` permite escribir reportes grandes en streaming, sin cargarlos enteros
en RAM.

## Adaptadores

| `STORAGE_BACKEND` | Adaptador | Uso |
|---|---|---|
| `local` | `storage/local.py` (disco, `STORAGE_LOCAL_PATH`) | desarrollo |
| `s3` | `storage/s3.py` (boto3: AWS S3 / Cloudflare R2 / MinIO) | producción |

La elección es **explícita** por variable, no por "adivinar" según qué variables
S3 existan (evita activar S3 por accidente).

## Por qué S3/R2 es obligatorio en producción

El `web` y los `workers` corren como **servicios separados, con discos distintos**,
y el hosting borra el disco en cada redeploy. Un worker genera un archivo y el
`web` debe poder servirlo: **solo un storage compartido (S3/R2) lo hace posible**.
Con disco local, el archivo del worker sería invisible para el `web`.

Cloudflare R2 es la opción recomendada (compatible con S3, sin costo de egreso).
Config: `S3_ENDPOINT_URL`, `S3_REGION` (`auto` en R2), `S3_BUCKET`,
`S3_ACCESS_KEY`, `S3_SECRET_KEY`.

## Convención de rutas

Los módulos usan prefijos por empresa, p. ej. `sunat/uploads/{company_id}/{job_id}.xlsx`.
El insumo de un job se borra en el `finally` de `procesar_job` tras terminar.
