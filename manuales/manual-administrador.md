# Manual de Administrador — SuperTax

Guía para **administradores de estudio** y para el **administrador de plataforma
(SuperAdmin)**. Complementa el *Manual de Usuario*, que cubre el uso diario de los
módulos.

> La sección **Administración** aparece en el menú solo si eres administrador de
> estudio o de plataforma.

---

## 1. Conceptos del modelo de estudios

- **Estudio** — tu grupo contable (el inquilino). Dueño de la **cartera de empresas**.
- **Empresa** — un cliente, con **RUC único**. Pertenece a **un solo** estudio.
- **Usuario** — una persona con su correo.

Regla central: **un RUC = un estudio dueño**. Nadie puede “reclamar” un RUC ajeno; a
una empresa existente se entra por **invitación** o **solicitud aprobada** por su
administrador.

## 2. Dos niveles de administración

| Nivel | Quién | Alcance |
|---|---|---|
| **Admin de estudio** | Tú y tus co-admins | Todo **tu** estudio: cartera, equipo y accesos. |
| **SuperAdmin de plataforma** | El operador de SuperTax | Aprobar estudios nuevos, crear empresas, activar módulos, traspasos. |

**Pestañas de Administración según tu rol:**
- Admin de estudio: **Miembros**, **Equipo**, **Administradores**, **Solicitudes de acceso**.
- SuperAdmin: además **Empresas** y **Solicitudes** (altas de estudio).

## 3. Roles y qué hace cada uno

| Rol | Alcance | Qué puede hacer |
|---|---|---|
| **Admin de estudio** | **Todas** las empresas del estudio | Todo: operar módulos, registrar empresas, invitar/quitar co-admins, asignar clientes y roles, aprobar solicitudes. |
| **Contador / Operador** | Solo clientes asignados | Operar todos los módulos de sus clientes (incluye borrar y gestionar credenciales). No gestiona equipo ni registra empresas. |
| **Asistente** | Solo asignados | Trabajo diario **sin** borrar ni cambiar credenciales. |
| **Consulta** | Solo asignados | Solo ver y descargar (ideal para el dueño del negocio o un auditor externo). |

> El rol **Admin es de estudio, no de empresa**: no se asigna “Admin” por empresa. Los
> administradores se gestionan en la pestaña **Administradores**.

## 4. Administradores del estudio (pestaña **Administradores**)

Para que el estudio **no dependa de una sola persona**, ten **varios admins**. Todos
ven y operan toda la cartera.

- **Ver** los administradores actuales.
- **Agregar co-admin**: ingresa correo y nombre → recibe un correo para fijar su
  contraseña y queda como admin del estudio.
- **Quitar co-admin**: deja de ver/operar las empresas. (No puedes quitarte a ti mismo.)

## 5. Equipo y Miembros (staff acotado)

Para colaboradores con acceso **solo a ciertos clientes**:

- **Equipo** (multi-empresa): asigna a una persona **varios clientes** a la vez con un
  rol (operador/asistente/consulta) y activa/desactiva su acceso por cliente.
- **Miembros** (empresa activa): gestiona quién accede a **la empresa seleccionada**:
  invitar, cambiar rol o remover.

> En estas pantallas el rol asignable es **operador / asistente / consulta**. “Admin” no
> aparece (se gestiona en Administradores).
>
> **Externos** (dueño del negocio / auditor): invítalos con rol **Consulta** a su
> empresa; verán solo esa empresa, en modo lectura.

## 6. Solicitudes de acceso (pestaña **Solicitudes de acceso**)

Cuando alguien pide acceso a una de **tus** empresas (intentó registrar un RUC que ya es
tuyo, o solicitó acceso desde adentro):

1. La solicitud aparece en tu bandeja (quién la pide y a qué empresa).
2. **Aprobar**: eliges el **rol** con el que entrará. Si la persona no tenía cuenta, se
   le **provisiona** (recibe correo para fijar contraseña) y queda con acceso.
3. **Rechazar**: no obtiene acceso.

> Así nadie entra a tus empresas “a ciegas”: **tú** decides quién y con qué rol.

## 7. Tareas del SuperAdmin de plataforma

### 7.1 Aprobar altas de estudio (pestaña **Solicitudes**)
Las solicitudes públicas con un **RUC libre** llegan aquí. Al **aprobar**, se crea el
**estudio** del nuevo contador + su primera empresa + su cuenta + los módulos.

### 7.2 Empresas (pestaña **Empresas**)
- Listar todas las empresas de la plataforma.
- **Crear** una empresa (eligiendo su **estudio dueño**).
- **Editar** razón social / RUC.
- **Módulos**: activar/suspender módulos por empresa. *(Hoy las empresas nacen con todos
  los módulos; esto cambiará con planes/suscripciones.)*

### 7.3 Traspaso (cambio de contador)
Mover una empresa a otro estudio: el destino pasa a verla, el origen deja de verla
(corte limpio). *(Verificación con Clave SOL: mejora futura.)*

## 8. Buenas prácticas de seguridad

- **Mínimo privilegio**: el rol más bajo que permita el trabajo.
- **Varios admins** de estudio (no depender de una sola persona).
- **Externos acotados**: Consulta y solo a su empresa.
- **Revisa las solicitudes** antes de aprobar: confirma que la persona trabaja con esa
  empresa.
- **Al desvincular a alguien**, remuévelo del equipo o quítale el co-admin a tiempo.
- **Credenciales SOL**: se guardan cifradas; cámbialas si sospechas de una fuga.

## 9. Resumen “quién puede hacer qué”

| Acción | Admin estudio | Operador | Asistente | Consulta | SuperAdmin |
|---|:---:|:---:|:---:|:---:|:---:|
| Ver toda la cartera | ✅ | — | — | — | ✅ |
| Operar módulos de un cliente | ✅ | ✅ (asignados) | ✅ (sin borrar) | 👁️ ver | — |
| Invitar co-admins | ✅ | — | — | — | — |
| Asignar clientes a staff | ✅ | — | — | — | — |
| Aprobar solicitudes de acceso | ✅ | — | — | — | — |
| Aprobar altas de estudio | — | — | — | — | ✅ |
| Crear empresas / activar módulos | ✅ (su estudio) | — | — | — | ✅ (global) |
| Traspasar empresas entre estudios | — | — | — | — | ✅ |

---

## Preguntas frecuentes

**Agregué una empresa y otro admin no la ve.**
No debería pasar: los admins de estudio ven **toda** la cartera. Si ocurre, avisa a
soporte (puede ser un dato heredado del modelo anterior).

**Quiero que alguien vea todo el estudio.**
Agrégalo como **co-admin** en la pestaña Administradores (no por empresa).

**Un cliente se fue con otro estudio contable.**
Es un **traspaso**: lo hace el SuperAdmin, verificando que el nuevo estudio esté
autorizado.

**Invité a alguien pero no aparece.**
Aparecerá cuando **ingrese por primera vez** (tras fijar su contraseña desde el correo).
