# ADR 0003 — Descarga SUNAT híbrida (Playwright + API `consultacpe`)

**Estado:** aceptada · **Fecha:** reescritura del bot de descarga SUNAT

## Contexto

El bot de descarga original hacía **todo** con Playwright, clic a clic en el
portal de SUNAT para bajar cada PDF/XML. Era frágil: se quedaba trabado, dependía
del render del portal y era lento. Se buscó algo más robusto que obtuviera los
**archivos reales** de forma fiable.

## Investigación

SUNAT expone una API oficial, **`consultacpe`**:

```
GET https://api-cpe.sunat.gob.pe/v1/contribuyente/consultacpe/comprobantes/
    {rucEmisor}-{tipo}-{serie}-{numero}-{origen}/{cod}
cod: 01=PDF · 02=XML · 03=CDR    origen=2 (recibido)
→ JSON { nomArchivo, valArchivo }   (valArchivo = base64 de un ZIP con el archivo)
```

Hallazgos:
- **No** se puede habilitar `consultacpe` en las credenciales de API del
  contribuyente (no aparece en el catálogo de controlacceso/apis).
- El token válido es el de la **sesión del portal SOL**.
- El WAF descarta peticiones sin **User-Agent de navegador**; hay 500 intermitentes.

## Decisión

Enfoque **híbrido**:

1. **Playwright** solo para **login** en SOL y navegar al módulo.
2. Extraer el **Bearer token** de `consultacpe` desde la sesión del navegador
   (interceptores de red + escaneo de storage).
3. Descargar cada archivo por **HTTP** contra `consultacpe`, con User-Agent de
   navegador y reintentos; desempaquetar el base64→ZIP→archivo.

Se validó de extremo a extremo (XML ~18 KB y PDF reales, estado OK).

## Consecuencias

**A favor**
- Archivos reales por **API oficial**, mucho más robusto que el scraping de
  descargas.
- Playwright se reduce a lo que sí necesita navegador (login), menos superficie
  frágil.

**En contra / operativo**
- Sigue dependiendo de Playwright/Chromium para el login (la imagen Docker lo
  incluye).
- El token expira/rota: un 401 lanza `TokenExpirado` y hay que recapturarlo.
- Hay que tolerar 500 intermitentes de la API (reintentos).
