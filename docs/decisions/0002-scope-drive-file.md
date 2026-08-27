# ADR 0002 — Google Drive con scope `drive.file` (no `drive` amplio)

**Estado:** aceptada · **Fecha:** reimplementación de la integración de Drive

## Contexto

La integración con Google Drive (subir resultados y elegir el Excel de entrada)
puede usar el scope amplio `https://www.googleapis.com/auth/drive` (acceso a todo
el Drive del usuario) o el acotado `drive.file` (solo lo que la app crea o el
usuario le concede explícitamente). El scope amplio dispara el proceso de
**verificación de Google** (CASA security assessment) y da a la app permiso sobre
archivos que no necesita.

## Decisión

Usar **`drive.file`** en ambos flujos. Para la entrada, como la app ya no puede
leer archivos arbitrarios, el usuario los elige con el **Google Picker** del lado
del navegador (token efímero GIS), y el archivo se descarga y se sube como una
carga normal. Para la salida, la app sube a una **carpeta propia** que ella misma
crea (`SuperTax {ruc}`).

## Consecuencias

**A favor**
- **Sin verificación de Google** del scope amplio.
- Menor superficie: la app no puede tocar el resto del Drive del usuario
  (principio de mínimo privilegio, OWASP A01).
- La subida a una carpeta propia evita colisiones: con `drive.file`, `list` solo
  devuelve lo que la app creó.

**En contra / detalles que hay que respetar**
- El Picker **requiere `setAppId(<número de proyecto>)`** para que, bajo
  `drive.file`, el archivo elegido quede accesible al token de la app; sin eso,
  `files.get` devuelve **404**.
- Se necesitan variables públicas en el frontend (`VITE_GOOGLE_*`) además de las
  del backend, y habilitar **Google Picker API** + **Drive API** en el proyecto.

## Alternativa descartada

- **Scope `drive` amplio + lectura por enlace en el backend**: más simple de
  codificar, pero obliga a la verificación de Google y sobre-otorga permisos.
