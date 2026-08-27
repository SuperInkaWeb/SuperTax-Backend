# ADR 0001 — Monolito modular (no microservicios)

**Estado:** aceptada · **Fecha:** fase de unificación de la plataforma

## Contexto

Tres automatizaciones existían por separado (Scanner, SUNAT, SIRE) y se querían
unificar en un producto (SuperTax) con login único, multiempresa y una sola SPA.
La duda era cómo estructurarlas: microservicios, monolito acoplado, o algo
intermedio. Requisito explícito: **agregar SUNAT no debe interferir con SIRE**, y
cada automatización debe poder evolucionar y probarse de forma aislada.

## Decisión

**Monolito modular** ("Opción A"): un solo proceso web FastAPI y una sola base de
datos, con tres módulos de negocio independientes sobre un núcleo compartido
(`platform/`). La independencia se **verifica automáticamente** con `import-linter`
(contrato de `independence` entre módulos + `forbidden` del núcleo hacia módulos).
Cada módulo tiene su esquema de base de datos y su propia cola de jobs.

## Consecuencias

**A favor**
- Un despliegue, una base de datos, un login — mucho menos costo operativo que
  microservicios para un equipo pequeño (KISS/YAGNI).
- Las fronteras son reales: un import cruzado rompe el build, no solo el estilo.
- Se puede escalar cada **worker** por separado aunque el `web` sea uno.

**En contra / límites**
- No hay aislamiento de fallos ni de despliegue a nivel de proceso entre módulos
  (un bug de arranque tumba todo el `web`).
- Código común entre módulos debe subir a `platform/`, no compartirse de lado a
  lado (a veces implica pequeña duplicación deliberada, p. ej. una `job_queue` por
  módulo).

## Alternativas descartadas

- **Microservicios**: sobredimensionado para el tamaño del equipo y del tráfico.
- **Monolito acoplado**: no garantiza que un módulo no rompa a otro.
