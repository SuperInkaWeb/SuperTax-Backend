# Módulos de negocio

Cada módulo es un **bounded context** con arquitectura **hexagonal**. Se crean
en sus fases respectivas (SIRE → Fase 2, SUNAT → Fase 3, Scanner → Fase 4).

## Estructura de un módulo

```
modules/<key>/
├─ domain/           # Reglas y entidades PURAS (sin FastAPI ni SQLAlchemy)
│  ├─ entities.py
│  ├─ value_objects.py
│  ├─ rules.py
│  ├─ ports.py       # interfaces que la infraestructura implementa
│  └─ events.py
├─ application/      # Casos de uso (orquestan el dominio)
│  └─ dto.py
├─ infrastructure/  # Adaptadores: repos SQLAlchemy, clientes externos
│  ├─ models.py      # tablas (schema propio del módulo)
│  └─ repositories.py
├─ api/              # Routers FastAPI + schemas Pydantic
│  ├─ routes.py
│  └─ schemas.py
└─ descriptor.py     # expone un ModuleDescriptor → se registra en module_registry
```

## Reglas (verificadas por import-linter en CI)

1. `domain/` no importa frameworks. Es Python puro y testeable en aislamiento.
2. Un módulo **nunca** importa a otro módulo. Se comunican por eventos
   (`platform/events`) o por interfaces del núcleo (`platform/`).
3. El núcleo (`platform/`) no depende de ningún módulo.
