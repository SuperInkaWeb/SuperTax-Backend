# Multiempresa (tenancy) — modelo de estudios

La plataforma es multi-tenant con una jerarquía de **estudios contables**:

```
Estudio (inquilino)  →  Empresas (clientes, RUC único)  →  Usuarios (por rol)
```

- Un **estudio** es el dueño de una cartera de empresas. Sirve igual para un contador
  independiente (estudio de 1 persona) que para un estudio grande.
- Una **empresa** (cliente) tiene **RUC único** en todo el sistema y pertenece a **un
  solo** estudio.
- Todo dato y operación está acotado a una **empresa activa**, que el cliente indica en
  cada petición (cabecera `X-Company-Id`) y el backend valida.

## Piezas (`platform/tenancy/`)

| Entidad | Descripción |
|---|---|
| `Estudio` | Inquilino (grupo contable). `owner_user_id` = creador/principal. |
| `EstudioMember` | Usuario ↔ estudio ↔ rol. **Aquí viven los admins** (ven toda la cartera). |
| `Company` | Empresa cliente. `ruc` **único**; `estudio_id` = estudio dueño. |
| `Membership` | Usuario ↔ empresa ↔ rol. **Solo staff acotado** (operador/asistente/consulta). |
| `CompanyAccessRequest` | Solicitud de acceso a una empresa existente (pública o interna). |

APIs: `estudio_api` (co-admins), `team_api` / `members_api` (staff por cliente),
`company_access_api` (solicitar acceso), `companies_api` (SuperAdmin: empresas, módulos,
traspaso), `my_companies_api` (alta self-service).

## Dos tablas de acceso, un propósito cada una

| Tabla | Pregunta que responde | Roles |
|---|---|---|
| `estudio_members` | ¿Quién administra el estudio? | **solo** admin |
| `memberships` | ¿Qué clientes toca este colaborador? | **solo** operador/asistente/consulta |

El sistema **impide** asignar `admin` por empresa (los endpoints de asignación lo
rechazan): un admin vive **siempre** a nivel estudio. Un admin de estudio ve y opera
toda su cartera **sin** filas por empresa.

## Cómo se resuelve la empresa activa

El frontend envía la cabecera **`X-Company-Id`**. El backend
(`get_active_context`):

```python
get_active_context(x_company_id, user, db):
    if x_company_id is None:                 → 400 (falta la cabecera)
    company = db.get(Company, x_company_id)
    if company is None:                      → 404
    # El admin del estudio dueño PREVALECE sobre cualquier membresía directa menor.
    role_id = estudio_admin_role_id(user, company.estudio_id)
    if role_id is None:
        membership = get_active_membership(user, company)
        if membership is None:               → 403 (no pertenece a la empresa)
        role_id = membership.role_id
    return ActiveContext(user, company, role_id)
```

`ActiveContext` (usuario + empresa + **rol efectivo**) es lo que reciben los routers.
Nota: un admin de estudio **nunca** pierde su rol admin por tener además una membresía
directa de menor rol.

## Aislamiento entre tenants

Es la **defensa #1** contra fuga de datos: sin acceso válido (admin de estudio **o**
membresía) para el `X-Company-Id` recibido, la petición se rechaza con 403 **antes** de
tocar lógica de negocio. A partir de ahí, cada módulo filtra por `company.id` del
contexto — nunca de un valor arbitrario del cliente. Un estudio **nunca** ve empresas de
otro estudio.

## Flujos de acceso

- **Registro de estudio** (público, RUC libre) → lo aprueba el SuperAdmin → crea estudio
  + empresa + admin. Ver [../platform/auth.md](../platform/auth.md).
- **Solicitar acceso** a un RUC existente → lo aprueba el admin dueño, concediendo una
  membresía con rol.
- **Invitar** co-admin (`estudio_members`) o staff a clientes (`memberships`).
- **Traspaso**: el SuperAdmin mueve una empresa a otro estudio (cambio de contador).

## Datos por empresa vs. globales

- **Global (`core`)**: usuarios, estudios, empresas, membresías, roles, permisos,
  módulos, credenciales (bóveda), solicitudes.
- **Por empresa**: jobs y resultados de cada módulo (SIRE/SUNAT/Scanner/DataFilter/
  Correos). Siempre llevan `company_id`.

## Relación con Auth0

Auth0 **autentica** (¿quién eres?); la **pertenencia y los permisos** viven en la base de
datos. La empresa activa se resuelve por `X-Company-Id` + (estudio-admin / membresía).
