# Multiempresa (tenancy)

La plataforma es multi-tenant: un usuario puede pertenecer a varias **empresas**,
y todo dato y operación está acotado a una empresa activa. El modelo es el
**"Modelo B"**: la empresa activa la indica el cliente en cada petición, y el
backend la valida contra las membresías del usuario.

## Piezas (`platform/tenancy/`)

| Entidad | Descripción |
|---|---|
| `Company` | una empresa (tenant) |
| `Membership` | vínculo usuario ↔ empresa, con su rol |
| `service.get_active_membership` | valida que el usuario sea miembro de la empresa |
| `current_tenant.get_active_context` | dependencia FastAPI que resuelve el contexto |

APIs asociadas: `companies_api`, `members_api`, `my_companies_api`, `team_api`.

## Cómo se resuelve la empresa activa

El frontend envía en cada request la cabecera **`X-Company-Id`** (la inyecta el
cliente axios; ver la doc del frontend). El backend:

```python
get_active_context(x_company_id, user, db):
    if x_company_id is None:            → 400 (falta la cabecera)
    membership = get_active_membership(db, user.id, x_company_id)
    if membership is None:              → 403 (no pertenece a la empresa)
    company = db.get(Company, x_company_id)
    if company is None:                 → 404
    return ActiveContext(user, company, membership)
```

`ActiveContext` (usuario + empresa + membresía) es lo que reciben los routers.

## Aislamiento entre tenants

Es la **defensa #1** contra fuga de datos: sin una membresía válida para el
`X-Company-Id` recibido, la petición se rechaza con 403 **antes** de tocar
lógica de negocio, sin importar qué envíe el cliente. A partir de ahí, cada
consulta de un módulo filtra por `company.id` tomado del contexto — nunca de un
valor arbitrario del cliente.

## Datos por empresa vs. globales

- **Global (`core`)**: usuarios, empresas, membresías, roles, permisos, módulos.
- **Por empresa**: credenciales SUNAT (cifradas), tokens de Drive, jobs y sus
  resultados, documentos del Scanner. Siempre llevan `company_id`.

## Relación con Auth0

Auth0 **autentica** (¿quién eres?); la **pertenencia y los permisos** viven en la
base de datos de la plataforma. Hoy la empresa activa se resuelve por
`X-Company-Id` + membresías. Si en el futuro se adoptan **Auth0 Organizations**
(una org por empresa), el `org_id` podría viajar en el token; el modelo actual ya
funciona sin ello. Ver [platform/auth.md](../platform/auth.md).
