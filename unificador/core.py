"""Lógica sin interfaz: escanear carpetas, ordenar documentos y unificar PDFs."""

from __future__ import annotations

import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

CARPETA_SALIDA_DEFAULT = "_Unificados"
EXCLUIR_DEFAULT = ["copiar a contable", CARPETA_SALIDA_DEFAULT]
# {operacion} = primer número del nombre de la carpeta ("183512 - 1064679" -> "183512")
# {carpeta} = nombre completo de la carpeta, {cliente} = carpeta del cliente
PLANTILLA_DEFAULT = "{operacion}-99-"

# Estados de una operación respecto de su PDF unificado.
NUEVO = "Pendiente"
MODIFICADO = "Cambió"
LISTO = "Unificado"


@dataclass
class Pagina:
    """Una hoja concreta de un PDF de origen, con rotación extra opcional."""

    archivo: Path
    indice: int
    rotacion: int = 0  # grados a sumar a la rotación original (múltiplo de 90)

    def etiqueta(self) -> str:
        return f"{self.archivo.stem} · p.{self.indice + 1}"


@dataclass
class Operacion:
    cliente: str
    nombre: str
    carpeta: Path
    pdfs: list[Path]
    archivo: str = ""  # nombre del PDF final (sin carpeta)
    paginas: list[Pagina] | None = field(default=None)  # None = orden automático

    @property
    def editada(self) -> bool:
        return self.paginas is not None

    def salida(self, dir_salida: Path) -> Path:
        return dir_salida / (self.archivo or f"{_nombre_valido(self.nombre)}.pdf")

    def estado(self, dir_salida: Path) -> str:
        destino = self.salida(dir_salida)
        if not destino.exists():
            return NUEVO
        ultimo = max((p.stat().st_mtime for p in self.pdfs), default=0)
        return LISTO if destino.stat().st_mtime >= ultimo else MODIFICADO


def _nombre_valido(nombre: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", nombre).strip(" .") or "sin_nombre"


def numero_operacion(nombre: str) -> str:
    m = re.search(r"\d+", nombre)
    return m.group(0) if m else nombre.strip()


def nombre_salida(plantilla: str, op: "Operacion") -> str:
    nombre = (plantilla or PLANTILLA_DEFAULT)
    for clave, valor in (("operacion", numero_operacion(op.nombre)), ("carpeta", op.nombre),
                         ("cliente", op.cliente)):
        nombre = nombre.replace("{" + clave + "}", valor)
    nombre = _nombre_valido(nombre)
    return nombre if nombre.lower().endswith(".pdf") else nombre + ".pdf"


def _clave_natural(p: Path) -> list:
    """'hoja 2' antes que 'hoja 10'."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", p.name.lower())]


def _es_pdf(nombre: str) -> bool:
    return nombre.lower().endswith(".pdf") and not nombre.startswith("~$")


def escanear(base: Path, dir_salida: Path, excluir: list[str],
             plantilla: str = PLANTILLA_DEFAULT) -> list[Operacion]:
    """Recorre base/CLIENTE/.../OPERACION y devuelve cada carpeta que tenga PDFs.

    Una operación es cualquier carpeta (dentro de la carpeta de un cliente) que
    contenga PDFs directamente. Si el cliente tiene PDFs sueltos en su raíz,
    esos forman una operación con el nombre del cliente.
    """
    excluir_norm = {e.strip().lower() for e in excluir if e.strip()}
    salida_real = dir_salida.resolve()
    operaciones: list[Operacion] = []

    def excluida(carpeta: Path) -> bool:
        return carpeta.name.lower() in excluir_norm or carpeta.resolve() == salida_real

    for cliente in sorted(base.iterdir(), key=lambda p: p.name.lower()):
        if not cliente.is_dir() or excluida(cliente):
            continue
        for raiz, dirs, archivos in os.walk(cliente):
            raiz_p = Path(raiz)
            dirs[:] = sorted((d for d in dirs if not excluida(raiz_p / d)), key=str.lower)
            pdfs = ordenar_pdfs([raiz_p / a for a in archivos if _es_pdf(a)])
            if not pdfs:
                continue
            rel = raiz_p.relative_to(cliente)
            nombre = cliente.name if rel == Path(".") else " - ".join(rel.parts)
            operaciones.append(Operacion(cliente.name, nombre, raiz_p, pdfs))

    # Todos los PDF van a la misma carpeta: si dos operaciones dan el mismo
    # nombre se les agrega el cliente (y un número si aun así se repite).
    usados: set[str] = set()
    cuenta = Counter(nombre_salida(plantilla, o).lower() for o in operaciones)
    repetidos = {n for n, c in cuenta.items() if c > 1}
    for op in operaciones:
        archivo = nombre_salida(plantilla, op)
        if archivo.lower() in repetidos:
            archivo = f"{archivo[:-4]} ({_nombre_valido(op.cliente)}).pdf"
        base_nombre, n = archivo[:-4], 2
        while archivo.lower() in usados:
            archivo, n = f"{base_nombre} ({n}).pdf", n + 1
        usados.add(archivo.lower())
        op.archivo = archivo
    return operaciones


def ordenar_pdfs(pdfs: list[Path]) -> list[Path]:
    """Orden inicial: alfabético natural por nombre de archivo."""
    return sorted(pdfs, key=_clave_natural)


def abrir(pdf: Path) -> pymupdf.Document:
    doc = pymupdf.open(pdf)
    if doc.needs_pass and not doc.authenticate(""):
        doc.close()
        raise ValueError(f"'{pdf.name}' está protegido con contraseña")
    return doc


def paginas_por_defecto(op: Operacion) -> list[Pagina]:
    paginas: list[Pagina] = []
    for pdf in ordenar_pdfs(op.pdfs):
        with abrir(pdf) as doc:
            paginas.extend(Pagina(pdf, i) for i in range(doc.page_count))
    return paginas


def paginas_de(op: Operacion) -> list[Pagina]:
    return op.paginas if op.paginas is not None else paginas_por_defecto(op)


def unificar(op: Operacion, dir_salida: Path) -> Path:
    """Genera el PDF final de la operación. Escribe a un temporal y reemplaza."""
    paginas = paginas_de(op)
    if not paginas:
        raise ValueError("no hay hojas para unificar")
    destino = op.salida(dir_salida)
    destino.parent.mkdir(parents=True, exist_ok=True)

    abiertos: dict[Path, pymupdf.Document] = {}
    final = pymupdf.open()
    try:
        for pag in paginas:
            if pag.archivo not in abiertos:
                try:
                    abiertos[pag.archivo] = abrir(pag.archivo)
                except Exception as e:
                    raise ValueError(f"no se pudo abrir '{pag.archivo.name}': {e}") from e
            src = abiertos[pag.archivo]
            final.insert_pdf(src, from_page=pag.indice, to_page=pag.indice)
            if pag.rotacion:
                nueva = final[-1]
                nueva.set_rotation((nueva.rotation + pag.rotacion) % 360)
        fd, tmp = tempfile.mkstemp(suffix=".pdf", dir=destino.parent)
        os.close(fd)
        try:
            final.save(tmp, garbage=3, deflate=True)
            final.close()
            os.replace(tmp, destino)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    finally:
        if not final.is_closed:
            final.close()
        for d in abiertos.values():
            d.close()
    return destino
