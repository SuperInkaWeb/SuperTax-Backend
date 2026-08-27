# Manual de Usuario — SuperTax

Guía para usar la plataforma en tu día a día: descargar comprobantes de SUNAT,
conciliar registros con SIRE y digitalizar documentos con el Escáner.

> Este manual es para quien **usa** la aplicación. Las tareas de administración
> (invitar personas, permisos) están en el *Manual de Administrador*.

---

## 1. Primeros pasos

### 1.1 Iniciar sesión

1. Abre la dirección de la plataforma en tu navegador.
2. Pulsa **Iniciar sesión**. Se abre la pantalla de acceso.
3. Ingresa tu correo y contraseña. Si es la primera vez, revisa tu correo: debiste
   recibir una invitación para **establecer tu contraseña**.
4. Al entrar verás la pantalla de **Inicio**.

Si no tienes acceso todavía, usa la opción **Solicitar acceso** desde la misma
pantalla de login.

### 1.2 Elegir la empresa activa

Si trabajas con más de una empresa, la plataforma siempre actúa sobre **una
empresa a la vez**.

1. Arriba, busca el **selector de empresa**.
2. Elige la empresa con la que vas a trabajar.
3. Todo lo que hagas (descargas, conciliaciones, documentos) quedará asociado a
   esa empresa. Puedes cambiarla cuando quieras.

### 1.3 Navegar por la aplicación

En el menú lateral izquierdo verás las secciones disponibles según lo que tu
empresa tenga contratado:

| Sección | Para qué sirve |
|---|---|
| **Inicio** | Portada y resumen |
| **SIRE** | Conciliación de registros de compras y ventas |
| **SUNAT** | Descarga de comprobantes electrónicos |
| **Escaneo** | Digitalización y extracción de datos de documentos |
| **Soporte** | Enviar y consultar tickets de soporte |
| **Administración** | Gestión de empresa, equipo y accesos (solo administradores) |

> Si no ves una sección, es porque tu empresa no tiene ese módulo activo o tu
> usuario no tiene permiso. Consulta con tu administrador.

### 1.4 Notificaciones y soporte

- La **campana** (arriba a la derecha) muestra avisos, por ejemplo cuando termina
  una descarga o una conciliación.
- En **Soporte** puedes crear un ticket si tienes un problema.

---

## 2. SUNAT — Descargar comprobantes

El módulo **SUNAT** descarga tus comprobantes electrónicos (PDF y XML) a partir de
una lista, y te los entrega por correo y/o Google Drive.

Al entrar a **SUNAT** verás cuatro pestañas: **Descargar**, **Credenciales**,
**Google Drive** e **Historial**.

### 2.1 Guardar tu clave SOL (una sola vez)

Para que la plataforma pueda ingresar a SUNAT por ti:

1. Entra a la pestaña **Credenciales**.
2. Ingresa tu **usuario y clave SOL**.
3. Guarda. La clave queda **cifrada**; no se muestra ni se comparte.

> También puedes escribir la clave en el momento de cada descarga, sin guardarla.

### 2.2 (Opcional) Conectar Google Drive para guardar resultados

Si quieres que los comprobantes descargados se guarden en tu Google Drive:

1. Entra a la pestaña **Google Drive**.
2. Pulsa **Conectar Drive** y autoriza con tu cuenta de Google.
3. Listo: los archivos se guardarán en una **carpeta propia de la aplicación** en
   tu Drive. Solo hay que hacerlo una vez.

### 2.3 Descargar comprobantes (paso a paso)

Entra a la pestaña **Descargar**.

**Paso 1 — Indica de dónde sale la lista de comprobantes.**
Tienes dos opciones:

- **Subir un Excel** (RUC, tipo, serie, número), o
- **Elegir de Google Drive**: pulsa el botón y selecciona el archivo desde tu
  Drive (se descarga solo).

**Paso 2 — Previsualiza y confirma el mapeo de columnas.**

1. Pulsa **Previsualizar**.
2. La plataforma detecta automáticamente qué columna es RUC, tipo, serie y número.
3. Si algo no cuadra, **corrige el mapeo** eligiendo la columna correcta para cada
   campo y vuelve a previsualizar.
4. Verás la lista de comprobantes que se van a descargar.

**Paso 3 — Elige qué descargar y cómo entregarlo.**

1. Marca los comprobantes que quieres (o todos).
2. Elige el **tipo de archivo**: PDF, XML o ambos.
3. Elige la **entrega**:
   - **Correo**: activa "Enviar por correo", indica el Gmail remitente, su
     contraseña de aplicación y el correo destino. Puedes enviar un correo por
     comprobante o uno solo agrupado.
   - **Google Drive**: activa "Subir a Google Drive" (requiere haberlo conectado
     antes, ver 2.2).

**Paso 4 — Inicia y sigue el progreso.**

1. Pulsa **Iniciar descarga**.
2. Verás el **progreso y los mensajes en vivo** mientras trabaja.
3. Al terminar, descarga los resultados o el reporte.

**Paso 5 — Reintentar los que fallaron (si aplica).**

Si algún comprobante quedó **Parcial** o con **Error**, usa la opción de
**reintentar faltantes**: vuelve a intentar solo esos, sin repetir los que ya
salieron bien.

### 2.4 Historial

En la pestaña **Historial** encuentras tus descargas anteriores, su estado y sus
resultados.

---

## 3. SIRE — Conciliación de registros

El módulo **SIRE** compara los registros de tu empresa contra la **propuesta** de
compras y ventas de SUNAT, y te entrega un reporte con las diferencias.

Pestañas: **Conciliaciones**, **Nueva**, **Credenciales**, **Formato de archivo**.

### 3.1 Configurar credenciales

1. Entra a **Credenciales** e ingresa tu **clave SOL** (queda cifrada).

### 3.2 Revisar el formato del archivo

1. Entra a **Formato de archivo** para ver qué columnas debe tener el archivo de
   registros de tu empresa.

### 3.3 Crear una conciliación

1. Entra a **Nueva**.
2. **Sube el archivo** de registros de tu empresa.
3. Confirma el **mapeo de columnas** (igual que en SUNAT: la plataforma detecta las
   columnas y tú corriges si hace falta).
4. Inicia la conciliación. Se procesa en segundo plano.

### 3.4 Ver resultados

1. Entra a **Conciliaciones**: verás la lista con el **estado** de cada una.
2. Abre una para ver el **detalle** y **descargar el reporte** de diferencias.

---

## 4. Escaneo — Digitalizar documentos

El módulo **Escaneo** lee documentos (PDF o foto) y extrae sus datos a una tabla
que puedes exportar a Excel.

Pestañas: **Subir documento** y **Documentos**.

### 4.1 Subir un documento

1. Entra a **Subir documento**.
2. Arrastra el archivo a la zona de carga (o pulsa para elegirlo).
3. Si quieres, **elige el tipo** de documento; si no, la plataforma intenta
   detectarlo.
4. Espera a que lo procese: verás los **datos extraídos**.

Tipos soportados: comprobantes (factura/boleta), recibos de servicios (agua, luz,
gas, telefonía) y documentos laborales (asistencia, boleta de pago).

### 4.2 Ver y exportar

1. Entra a **Documentos** para ver todo lo procesado en una tabla (con la columna
   *Archivo* para saber de dónde viene cada fila).
2. Exporta a Excel de dos formas:
   - **Todo junto**: una sola hoja con todo.
   - **Por documento**: una hoja por archivo.

---

## 5. Seguridad de tu clave SOL

- Tu clave SOL se guarda **cifrada**; nadie la ve en texto plano, ni siquiera en
  los registros del sistema.
- Se usa **solo** para ingresar a SUNAT/SIRE cuando tú inicias una descarga o
  conciliación.
- Si prefieres, no la guardes y escríbela en cada operación.

---

## Preguntas frecuentes

**No veo el módulo SUNAT/SIRE/Escaneo.**
Tu empresa no lo tiene activo o tu usuario no tiene permiso. Consulta con tu
administrador.

**La descarga se quedó en "Parcial".**
Usa **reintentar faltantes** para volver a intentar solo los que no salieron.

**¿Puedo trabajar con varias empresas?**
Sí. Cambia la **empresa activa** en el selector de arriba; cada operación queda
asociada a la empresa seleccionada.

**Cambié de empresa y ya no veo mis descargas.**
Los datos son por empresa. Vuelve a seleccionar la empresa correspondiente.
