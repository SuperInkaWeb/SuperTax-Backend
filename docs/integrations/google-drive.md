# Integración con Google Drive

El módulo SUNAT usa Google Drive de **dos formas independientes**, con direcciones
opuestas. No son redundantes: cubren *salida* y *entrada*.

| | Conectar Drive (sección) | Elegir de Google Drive (Picker) |
|---|---|---|
| **Para qué** | **subir** los resultados (PDF/XML) | **elegir** el Excel de entrada |
| **Dirección** | app → Drive (salida) | Drive → app (entrada) |
| **Quién** | el `web`, al procesar el job del usuario | el usuario, en el navegador |
| **Token** | persistente (refresh, cifrado **por usuario**) | efímero, en el navegador, por archivo |
| **Frecuencia** | una vez | cada vez que elige un archivo |
| **Dónde vive** | backend (`drive_service` + `automation/drive.py`) | frontend (Picker GIS) |

> **Por usuario, no por empresa.** Cada usuario conecta **su propio** Google Drive
> (una vez); las descargas que **él** lanza suben a **su** Drive, en carpetas
> `SuperTax {RUC}`. El token se guarda con `user_id` (ver `DriveTokenModel`), y el
> job usa el token de **su creador** (`created_by_id`).

## Scope acotado: `drive.file`

Ambos flujos usan **`https://www.googleapis.com/auth/drive.file`**, no el scope
amplio `drive`. Ventajas (ver
[../decisions/0002-scope-drive-file.md](../decisions/0002-scope-drive-file.md)):

- **No requiere** la verificación de Google del scope amplio.
- La app **solo ve/gestiona lo que ella crea** o lo que el usuario le concede
  explícitamente por el Picker. No puede tocar el resto del Drive del usuario.

## Flujo 1 — Subida de resultados (OAuth backend)

`application/drive_service.py` + `infrastructure/automation/drive.py`:

1. El usuario pulsa **Conectar Drive** → `/api/sunat/drive/auth` devuelve la URL
   de autorización de Google (popup).
2. Google redirige a `/api/sunat/drive/callback` con un `code`. El `state` es un
   token **Fernet** con empresa+usuario: sirve de protección **CSRF** y de vínculo
   con el **usuario** que inició la conexión (el callback no lleva token Auth0).
3. Se intercambia el `code` por tokens (`access` + `refresh`) y se guardan
   **cifrados** por usuario (`DriveTokenModel.user_id`).
4. En un job con "Subir a Google Drive", el `web` crea un `DriveClient` con el token
   del **creador del job** que:
   - asegura una **carpeta propia de la app** (`SuperTax {ruc}`) — con `drive.file`,
     `list` solo devuelve lo que la app creó, sin colisión con carpetas homónimas;
   - sube cada PDF/XML a esa carpeta;
   - si el access token se renueva, lo **persiste** vía callback (`_persist_drive_token`).

Endurecimientos: `urlopen` con `timeout`; se captura `URLError` (red/timeout, no
solo HTTP); un `invalid_grant` (refresh revocado) se reporta como "reconecta tu
cuenta" y cancela la subida.

## Flujo 2 — Elegir el Excel de entrada (Google Picker)

Vive en el **frontend** (`features/sunat/drivePicker.ts`). Con `drive.file` el
backend no puede leer archivos arbitrarios, así que el usuario los elige en el
navegador:

1. GIS pide un **access token** del lado del cliente (`initTokenClient`), con
   `error_callback` para popup cerrado/bloqueado.
2. Se abre el **Picker** (vista de hojas de cálculo). Clave: se llama
   `setAppId(<número de proyecto>)` — sin eso, bajo `drive.file` el Picker **no
   concede** acceso al archivo elegido y `files.get` devuelve **404**. El número de
   proyecto se deriva del prefijo del `client_id`.
3. Se descarga el archivo elegido (exportando Google Sheets a xlsx) y se sube al
   backend como una carga normal.

## Variables

| Variable | Dónde | Secreto | Uso |
|---|---|---|---|
| `GOOGLE_CLIENT_ID` | Railway (backend) | no | OAuth backend (subida) |
| `GOOGLE_CLIENT_SECRET` | Railway (backend) | **sí** | OAuth backend (subida) |
| `DRIVE_REDIRECT_URI` | Railway (backend) | no | `https://<web>/api/sunat/drive/callback` |
| `VITE_GOOGLE_CLIENT_ID` | Vercel (frontend) | no (público) | Picker (entrada) |
| `VITE_GOOGLE_API_KEY` | Vercel (frontend) | no (público) | Picker (entrada) |

En Google Cloud, además: habilitar **Google Picker API** y **Google Drive API**;
en la API key, permitir esas APIs y el dominio del frontend como referrer; en el
OAuth client, el **Authorized JavaScript origin** del frontend y el **redirect
URI** = `DRIVE_REDIRECT_URI`.
