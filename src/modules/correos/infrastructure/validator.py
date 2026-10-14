"""
Validación de seguridad de adjuntos (último filtro antes de almacenar y, luego,
abrir con parsers nativos). Solo stdlib (portado del standalone).

Reglas: allowlist de 4 tipos (PDF/JPG/PNG/WebP) detectados por CONTENIDO (magic
bytes), coherencia extensión↔contenido, nombre seguro, y heurísticas baratas
anti-bomba/anti-contenido-activo en PDF. Devuelve (ok, mime, sha256, motivo).
"""
import hashlib
import re
from dataclasses import dataclass

# (firma, mime, extensiones aceptadas)
_FIRMAS = (
    (b"%PDF-", "application/pdf", (".pdf",)),
    (b"\xff\xd8\xff", "image/jpeg", (".jpg", ".jpeg")),
    (b"\x89PNG\r\n\x1a\n", "image/png", (".png",)),
    (b"RIFF", "image/webp", (".webp",)),  # se confirma con 'WEBP' en el offset 8
)
_MIME_PERMITIDOS = frozenset(m for _, m, _ in _FIRMAS)

_MINIMO_BYTES = 64
_MAXIMO_PAGINAS_PDF = 500
_MAXIMO_OBJETOS_PDF = 50_000
_RATIO_MAXIMO_OBJETOS_POR_KB = 40
_PATRON_NOMBRE_SEGURO = re.compile(r"^[\w\s.,()\[\]\-+#&@]{1,255}$", re.UNICODE)
_PATRON_PAGINA_PDF = re.compile(rb"/Type\s*/Page(?![s/\w])")


@dataclass(frozen=True)
class ResultadoValidacion:
    ok: bool
    mime: str
    sha256: str
    motivo: str | None


def validar(contenido: bytes, *, nombre: str, tamano_maximo: int) -> ResultadoValidacion:
    sha256 = hashlib.sha256(contenido).hexdigest()

    rechazo = _revisar_tamano(contenido, tamano_maximo) or _revisar_nombre(nombre)
    if rechazo:
        return ResultadoValidacion(False, "", sha256, rechazo)

    detectado = _detectar_tipo(contenido)
    if detectado is None:
        return ResultadoValidacion(False, "", sha256, "tipo_no_reconocido")
    mime, extensiones = detectado
    if mime not in _MIME_PERMITIDOS:
        return ResultadoValidacion(False, mime, sha256, "tipo_no_permitido")
    if not any(nombre.lower().endswith(ext) for ext in extensiones):
        return ResultadoValidacion(False, mime, sha256, "extension_no_coincide_con_contenido")
    if mime == "application/pdf":
        anomalia = _revisar_pdf(contenido)
        if anomalia:
            return ResultadoValidacion(False, mime, sha256, anomalia)
    return ResultadoValidacion(True, mime, sha256, None)


def _revisar_tamano(contenido: bytes, tamano_maximo: int) -> str | None:
    if len(contenido) < _MINIMO_BYTES:
        return "archivo_vacio_o_truncado"
    if len(contenido) > tamano_maximo:
        return "tamano_excedido"
    return None


def _revisar_nombre(nombre: str) -> str | None:
    if not nombre or len(nombre) > 255:
        return "nombre_invalido"
    if "\x00" in nombre or "/" in nombre or "\\" in nombre or ".." in nombre:
        return "nombre_invalido"
    if not _PATRON_NOMBRE_SEGURO.match(nombre):
        return "nombre_invalido"
    return None


def _detectar_tipo(contenido: bytes) -> tuple[str, tuple[str, ...]] | None:
    for firma, mime, extensiones in _FIRMAS:
        if not contenido.startswith(firma):
            continue
        if mime == "image/webp" and (len(contenido) < 12 or contenido[8:12] != b"WEBP"):
            continue
        return mime, extensiones
    return None


def _revisar_pdf(contenido: bytes) -> str | None:
    objetos = contenido.count(b" obj")
    if objetos > _MAXIMO_OBJETOS_PDF:
        return "pdf_demasiados_objetos"
    kilobytes = max(1, len(contenido) // 1024)
    if objetos / kilobytes > _RATIO_MAXIMO_OBJETOS_POR_KB:
        return "pdf_sospechoso_de_bomba"
    if len(_PATRON_PAGINA_PDF.findall(contenido)) > _MAXIMO_PAGINAS_PDF:
        return "pdf_demasiadas_paginas"
    for marcador in (b"/JavaScript", b"/JS ", b"/Launch", b"/EmbeddedFile"):
        if marcador in contenido:
            return "pdf_con_contenido_activo"
    return None
