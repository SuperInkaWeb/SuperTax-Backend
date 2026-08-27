# Seguridad

Resumen de las defensas transversales y cómo se mapean a OWASP. Complementa
[auth.md](auth.md) (identidad/RBAC) y [../architecture/tenancy.md](../architecture/tenancy.md)
(aislamiento entre tenants).

## Cifrado en reposo (Fernet)

Datos sensibles (credenciales SOL de SUNAT/SIRE, tokens de Google Drive) se
guardan **cifrados** con Fernet (`platform/security`, `encrypt_field` /
`decrypt_field`). La llave es `ENCRYPTION_KEY`:

- En desarrollo hay una llave por defecto (cómoda, pero pública en el repo).
- En **producción** un validador **impide arrancar** si `ENV=production` y la
  llave sigue siendo la de dev o está vacía. Genera una propia:

  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```

> Si se pierde la llave, las credenciales ya guardadas no se pueden descifrar.

## Secretos solo por variables de entorno

Ningún secreto vive en código. Todo sale de `settings` (variables de entorno). Los
`.env` están en `.gitignore`; solo se versiona `.env.example`. Ver
[../operations/env-vars.md](../operations/env-vars.md).

## CORS

`CORS_ORIGINS` es una lista separada por comas de orígenes permitidos. **Nunca
`*` en producción** — se ponen los dominios reales del frontend.

## Rate limiting y hardening

- **Rate limiting** en endpoints públicos/costosos (p. ej. conciliaciones).
- **Path traversal**: las rutas de storage se construyen de forma controlada y se
  validan; nunca se concatena input del usuario a una ruta de archivo sin sanear.
- **IP tras proxy**: se resuelve la IP real del cliente considerando el proxy del
  hosting (para el rate limiting), sin confiar ciegamente en cabeceras arbitrarias.

## Manejo de errores

Los errores de negocio son excepciones tipadas por módulo (`SunatJobError`,
`DriveError`, …) que la capa API traduce a códigos HTTP (400/403/…). No se
exponen trazas al cliente; el detalle va a logs del servidor.

## Logs sin PII

No se registran contraseñas, tokens ni datos sensibles. Las credenciales SOL se
descifran solo en memoria dentro del worker, para el login.

## Mapa OWASP Top 10

| OWASP | Mitigación en la plataforma |
|---|---|
| A01 Broken Access Control | pipeline `membership → módulo → permiso`; aislamiento por `company_id` |
| A02 Cryptographic Failures | Fernet en reposo; TLS en tránsito; llave obligatoria en prod |
| A03 Injection | SQLAlchemy (consultas parametrizadas), nunca SQL concatenado |
| A04 Insecure Design | fronteras verificadas (import-linter), capas estrictas |
| A05 Security Misconfiguration | validador de `ENCRYPTION_KEY`; CORS explícito; `DEBUG=false` en prod |
| A06 Vulnerable Components | dependencias pineadas en `pyproject.toml` |
| A07 Auth Failures | Auth0 (RS256/JWKS); usuarios inactivos bloqueados |
| A08 Integrity Failures | seed idempotente; migraciones versionadas |
| A09 Logging/Monitoring | logs por job en Postgres; `/health` y `/health/ready` |
| A10 SSRF | integraciones salientes solo a endpoints fijos conocidos (Google, SUNAT) |
