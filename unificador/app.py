"""Unificador de PDF — interfaz gráfica (Tkinter)."""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import pymupdf

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unificador import core

APP_TITULO = "Unificador de PDF"
COLOR_SEL = "#1f6feb"
COLOR_SEL_FONDO = "#d6e4ff"


def activar_alta_resolucion() -> None:
    """En Windows con escalado (125 %, 150 %…) evita que todo se vea borroso."""
    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def carpeta_app() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


RUTA_CONFIG = carpeta_app() / "unificador_config.json"


def cargar_config() -> dict:
    try:
        return json.loads(RUTA_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def guardar_config(cfg: dict) -> None:
    try:
        RUTA_CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass  # carpeta de solo lectura: no pasa nada


def motivo_error(pag: core.Pagina, e: Exception) -> str:
    if not pag.archivo.exists():
        return "Archivo no\nencontrado"
    texto = str(e).lower()
    if "contraseña" in texto:
        return "Protegido con\ncontraseña"
    return "No se pudo\nabrir el PDF"


def abrir_en_sistema(ruta: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(ruta)  # type: ignore[attr-defined]
    else:
        import subprocess

        subprocess.Popen(["xdg-open", str(ruta)])


class Renderizador:
    """Abre cada PDF una sola vez y cachea las miniaturas renderizadas."""

    def __init__(self) -> None:
        self.docs: dict[Path, pymupdf.Document] = {}
        self.cache: dict[tuple, tk.PhotoImage] = {}

    def _pagina(self, pag: core.Pagina) -> pymupdf.Page:
        if pag.archivo not in self.docs:
            self.docs[pag.archivo] = core.abrir(pag.archivo)
        return self.docs[pag.archivo][pag.indice]

    def tamano(self, pag: core.Pagina) -> tuple[float, float]:
        """Ancho y alto en puntos, ya con la rotación aplicada."""
        rect = self._pagina(pag).rect  # contempla la rotación propia de la página
        return (rect.height, rect.width) if pag.rotacion % 180 else (rect.width, rect.height)

    def en_cache(self, pag: core.Pagina, ancho: int, alto: int) -> tk.PhotoImage | None:
        return self.cache.get((pag.archivo, pag.indice, pag.rotacion, ancho, alto))

    def imagen(self, pag: core.Pagina, ancho: int, alto: int, cachear: bool = True) -> tk.PhotoImage:
        """Renderiza la hoja para que entre en ancho x alto píxeles."""
        clave = (pag.archivo, pag.indice, pag.rotacion, ancho, alto)
        if clave in self.cache:
            return self.cache[clave]
        w, h = self.tamano(pag)
        return self.imagen_zoom(pag, min(ancho / w, alto / h), clave if cachear else None)

    def imagen_zoom(self, pag: core.Pagina, zoom: float, clave: tuple | None = None) -> tk.PhotoImage:
        mat = pymupdf.Matrix(zoom, zoom).prerotate(pag.rotacion)
        pix = self._pagina(pag).get_pixmap(matrix=mat, alpha=False, colorspace=pymupdf.csRGB)
        img = tk.PhotoImage(data=pix.tobytes("ppm"))
        if clave is not None:
            if len(self.cache) > 1500:
                # primero se descartan miniaturas de otros tamaños; las que están
                # en pantalla no se pierden porque el visor guarda su propia referencia
                tamano = clave[3:]
                for k in [k for k in self.cache if k[3:] != tamano] or list(self.cache)[:500]:
                    del self.cache[k]
            self.cache[clave] = img
        return img

    def cerrar(self) -> None:
        for d in self.docs.values():
            d.close()
        self.docs.clear()
        self.cache.clear()


class VistaPrevia(tk.Toplevel):
    """Hoja en grande, nítida, con zoom y desplazamiento."""

    ZOOMS = [0.25, 0.33, 0.5, 0.67, 0.75, 0.9, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0]

    def __init__(self, app: "App", indice: int) -> None:
        super().__init__(app.root)
        self.app = app
        self.indice = indice
        self.modo = "pagina"  # "pagina", "ancho" o "zoom"
        self.zoom = 1.0  # 1.0 = tamaño real (72 ppp * escala de pantalla)
        self.img: tk.PhotoImage | None = None
        self._pendiente = None
        self._pan = None
        self.title("Vista previa")
        pantalla_h = self.winfo_screenheight()
        ancho = min(self.winfo_screenwidth() - 40, max(int(pantalla_h * 0.75), int(900 * app.escala)))
        self.geometry(f"{ancho}x{int(pantalla_h * 0.9)}")

        barra = ttk.Frame(self, padding=4)
        barra.pack(fill="x")
        ttk.Button(barra, text="◀ Anterior", command=lambda: self.ir(-1)).pack(side="left")
        ttk.Button(barra, text="Siguiente ▶", command=lambda: self.ir(1)).pack(side="left", padx=4)
        ttk.Separator(barra, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(barra, text="↺", width=3, command=lambda: self.rotar(-90)).pack(side="left")
        ttk.Button(barra, text="↻", width=3, command=lambda: self.rotar(90)).pack(side="left", padx=2)
        ttk.Button(barra, text="Quitar hoja", command=self.quitar).pack(side="left", padx=2)
        ttk.Separator(barra, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(barra, text="Página entera", command=lambda: self.set_modo("pagina")).pack(side="left")
        ttk.Button(barra, text="Ancho", command=lambda: self.set_modo("ancho")).pack(side="left", padx=2)
        ttk.Button(barra, text="−", width=3, command=lambda: self.paso_zoom(-1)).pack(side="left", padx=(6, 0))
        self.lbl_zoom = ttk.Label(barra, width=6, anchor="center")
        self.lbl_zoom.pack(side="left")
        ttk.Button(barra, text="+", width=3, command=lambda: self.paso_zoom(1)).pack(side="left")
        self.lbl = ttk.Label(self, padding=(6, 0, 6, 4))
        self.lbl.pack(fill="x")

        marco = ttk.Frame(self)
        marco.pack(fill="both", expand=True)
        marco.rowconfigure(0, weight=1)
        marco.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(marco, bg="#4a4a4a", highlightthickness=0)
        sy = ttk.Scrollbar(marco, orient="vertical", command=self.canvas.yview)
        sx = ttk.Scrollbar(marco, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")

        self.canvas.bind("<Configure>", lambda e: self.programar())
        self.canvas.bind("<ButtonPress-1>", lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B1-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self.bind("<MouseWheel>", self._rueda)
        self.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-3, "units"))
        self.bind("<Button-5>", lambda e: self.canvas.yview_scroll(3, "units"))
        for tecla, cmd in [("<Left>", lambda: self.ir(-1)), ("<Right>", lambda: self.ir(1)),
                           ("<Prior>", lambda: self.ir(-1)), ("<Next>", lambda: self.ir(1)),
                           ("<plus>", lambda: self.paso_zoom(1)), ("<minus>", lambda: self.paso_zoom(-1)),
                           ("<KP_Add>", lambda: self.paso_zoom(1)), ("<KP_Subtract>", lambda: self.paso_zoom(-1)),
                           ("<Key-0>", lambda: self.set_modo("pagina")), ("<Delete>", self.quitar),
                           ("<Key-r>", lambda: self.rotar(90)), ("<Escape>", self.destroy)]:
            self.bind(tecla, lambda e, c=cmd: c())
        self.focus_set()

    # zoom -----------------------------------------------------------------
    def _zoom_base(self) -> float:
        return self.app.escala  # 1.0 = 100 % en pantalla

    def zoom_efectivo(self, w: float, h: float) -> float:
        cw = max(self.canvas.winfo_width(), 100) - 16
        ch = max(self.canvas.winfo_height(), 100) - 16
        if self.modo == "pagina":
            return min(cw / w, ch / h)
        if self.modo == "ancho":
            return cw / w
        return self.zoom * self._zoom_base()

    def set_modo(self, modo: str) -> None:
        self.modo = modo
        self.dibujar()

    def paso_zoom(self, direccion: int) -> None:
        pag = self.pagina()
        if pag is None:
            return
        actual = self.zoom_efectivo(*self.app.render.tamano(pag)) / self._zoom_base()
        if direccion > 0:
            nuevo = next((z for z in self.ZOOMS if z > actual + 0.01), self.ZOOMS[-1])
        else:
            nuevo = next((z for z in reversed(self.ZOOMS) if z < actual - 0.01), self.ZOOMS[0])
        self.modo, self.zoom = "zoom", nuevo
        self.dibujar()

    def _rueda(self, e) -> None:
        if e.state & 0x0004:  # Ctrl + rueda = zoom
            self.paso_zoom(1 if e.delta > 0 else -1)
        elif e.state & 0x0001:  # Shift + rueda = horizontal
            self.canvas.xview_scroll(-1 if e.delta > 0 else 1, "units")
        else:
            self.canvas.yview_scroll(int(-e.delta / 40) or (-1 if e.delta > 0 else 1), "units")

    # navegación ----------------------------------------------------------
    def pagina(self) -> core.Pagina | None:
        paginas = self.app.paginas
        if not paginas:
            return None
        self.indice = max(0, min(self.indice, len(paginas) - 1))
        return paginas[self.indice]

    def ir(self, delta: int) -> None:
        total = len(self.app.paginas)
        if total:
            self.indice = max(0, min(total - 1, self.indice + delta))
            self.app.seleccionar({self.indice}, mostrar=True)
            self.dibujar(arriba=True)

    def rotar(self, grados: int) -> None:
        if self.pagina() is not None:
            self.app.seleccionar({self.indice})
            self.app.rotar(grados)

    def quitar(self) -> None:
        if self.pagina() is not None:
            self.app.seleccionar({self.indice})
            self.app.quitar()

    def programar(self) -> None:
        if self._pendiente:
            self.after_cancel(self._pendiente)
        self._pendiente = self.after(60, self.dibujar)

    def dibujar(self, arriba: bool = False) -> None:
        self._pendiente = None
        self.canvas.delete("all")
        pag = self.pagina()
        if pag is None:
            self.lbl.config(text="(sin hojas)")
            self.img = None
            return
        try:
            w, h = self.app.render.tamano(pag)
            zoom = self.zoom_efectivo(w, h)
            zoom = min(zoom, 9000 / max(w, h))  # límite de memoria
            self.img = self.app.render.imagen_zoom(pag, zoom)
        except Exception as e:
            self.canvas.create_text(20, 20, anchor="nw", fill="white",
                                    text=f"{motivo_error(pag, e)}\n\n{pag.archivo}\n{e}")
            return
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        iw, ih = self.img.width(), self.img.height()
        x0, y0 = max(8, (cw - iw) // 2), max(8, (ch - ih) // 2)
        self.canvas.create_rectangle(x0 + 3, y0 + 3, x0 + iw + 3, y0 + ih + 3, fill="#2a2a2a", width=0)
        self.canvas.create_image(x0, y0, anchor="nw", image=self.img)
        self.canvas.configure(scrollregion=(0, 0, max(cw, iw + 16), max(ch, ih + 16)))
        if arriba:
            self.canvas.yview_moveto(0)
        self.lbl_zoom.config(text=f"{zoom / self._zoom_base() * 100:.0f} %")
        self.lbl.config(text=f"Hoja {self.indice + 1} de {len(self.app.paginas)}   —   "
                             f"{pag.archivo.name}  (pág. {pag.indice + 1})")


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.cfg = cargar_config()
        self.escala = max(1.0, root.winfo_fpixels("1i") / 96)
        self.operaciones: list[core.Operacion] = []
        self.op_actual: core.Operacion | None = None
        self.paginas: list[core.Pagina] = []
        self.sel: set[int] = set()
        self.ancla: int | None = None
        self.render = Renderizador()
        self.arrastre: dict | None = None
        self.vista: VistaPrevia | None = None
        self.cola: queue.Queue = queue.Queue()
        self.trabajando = False
        self._generacion = 0
        self._pendientes: list = []
        self._en_pantalla: list[tk.PhotoImage] = []
        self._dibujo_programado = None
        self._doc_origen: int | None = None

        root.title(APP_TITULO)
        root.geometry(self.cfg.get("geometria", f"{int(1300 * self.escala)}x{int(820 * self.escala)}"))
        root.minsize(int(950 * self.escala), int(560 * self.escala))
        self._armar_ui()
        root.protocol("WM_DELETE_WINDOW", self.salir)
        root.bind("<FocusIn>", self.al_volver_a_la_ventana)
        if self.var_base.get() and Path(self.var_base.get()).is_dir():
            root.after(100, self.escanear)

    # ------------------------------------------------------------ medidas
    @property
    def mini_alto(self) -> int:
        return int(self.var_tamano.get() * self.escala)

    @property
    def celda(self) -> tuple[int, int]:
        alto = self.mini_alto
        return int(alto * 0.8) + int(24 * self.escala), alto + int(46 * self.escala)

    # ------------------------------------------------------------------ UI
    def _armar_ui(self) -> None:
        arriba = ttk.Frame(self.root, padding=(8, 8, 8, 4))
        arriba.pack(fill="x")
        arriba.columnconfigure(1, weight=1)

        self.var_base = tk.StringVar(value=self.cfg.get("base", ""))
        self.var_salida = tk.StringVar(value=self.cfg.get("salida", ""))
        self.var_plantilla = tk.StringVar(value=self.cfg.get("plantilla", core.PLANTILLA_DEFAULT))
        self.var_excluir = tk.StringVar(value=self.cfg.get("excluir", ", ".join(core.EXCLUIR_DEFAULT)))
        self.var_tamano = tk.IntVar(value=self.cfg.get("tamano", 200))

        ttk.Label(arriba, text="Carpeta base:").grid(row=0, column=0, sticky="w")
        ttk.Entry(arriba, textvariable=self.var_base).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(arriba, text="Examinar…", command=self.elegir_base).grid(row=0, column=2)
        ttk.Button(arriba, text="Escanear", command=self.escanear).grid(row=0, column=3, padx=(4, 0))

        ttk.Label(arriba, text="Guardar en:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(arriba, textvariable=self.var_salida).grid(row=1, column=1, sticky="ew", padx=4, pady=(4, 0))
        ttk.Button(arriba, text="Examinar…", command=self.elegir_salida).grid(row=1, column=2, pady=(4, 0))
        ttk.Button(arriba, text="Abrir", command=self.abrir_salida).grid(row=1, column=3, padx=(4, 0), pady=(4, 0))
        ttk.Label(arriba, text=f"(vacío = carpeta base\\{core.CARPETA_SALIDA_DEFAULT})",
                  foreground="#666").grid(row=1, column=4, sticky="w", padx=4, pady=(4, 0))

        fila2 = ttk.Frame(arriba)
        fila2.grid(row=2, column=0, columnspan=5, sticky="ew", pady=(4, 0))
        ttk.Label(fila2, text="Nombre del PDF:").pack(side="left")
        ttk.Entry(fila2, textvariable=self.var_plantilla, width=22).pack(side="left", padx=4)
        ttk.Label(fila2, text="{operacion} = 183512   ", foreground="#666").pack(side="left")
        ttk.Label(fila2, text="Ignorar carpetas:").pack(side="left", padx=(12, 0))
        ttk.Entry(fila2, textvariable=self.var_excluir).pack(side="left", fill="x", expand=True, padx=4)

        panel = ttk.PanedWindow(self.root, orient="horizontal")
        panel.pack(fill="both", expand=True, padx=8, pady=4)

        # --- Izquierda: clientes / operaciones
        izq = ttk.Frame(panel)
        panel.add(izq, weight=0)
        self.arbol = ttk.Treeview(izq, columns=("estado",), selectmode="extended")
        self.arbol.heading("#0", text="Cliente / Operación")
        self.arbol.heading("estado", text="Estado")
        self.arbol.column("#0", width=int(210 * self.escala))
        self.arbol.column("estado", width=int(95 * self.escala), anchor="center")
        self.arbol.tag_configure(core.NUEVO, foreground="#b35900")
        self.arbol.tag_configure(core.MODIFICADO, foreground="#b30000")
        self.arbol.tag_configure(core.LISTO, foreground="#1a7f37")
        sb = ttk.Scrollbar(izq, orient="vertical", command=self.arbol.yview)
        self.arbol.configure(yscrollcommand=sb.set)
        self.arbol.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.arbol.bind("<<TreeviewSelect>>", self.al_elegir_operacion)
        self.arbol.bind("<Double-1>", lambda e: self.abrir_unificado())

        # --- Centro: documentos de la operación
        centro = ttk.Frame(panel)
        panel.add(centro, weight=0)
        ttk.Label(centro, text="Documentos (orden en el PDF final)").pack(anchor="w")
        self.lista_docs = ttk.Treeview(centro, columns=("hojas",), show="tree headings",
                                       selectmode="browse", height=8)
        self.lista_docs.heading("#0", text="Archivo")
        self.lista_docs.heading("hojas", text="Hojas")
        self.lista_docs.column("#0", width=int(230 * self.escala))
        self.lista_docs.column("hojas", width=int(50 * self.escala), anchor="center")
        self.lista_docs.pack(fill="both", expand=True, pady=(2, 4))
        # solo clics/teclas del usuario (no los cambios de selección hechos por código)
        self.lista_docs.bind("<KeyRelease-Up>", self.al_elegir_documento)
        self.lista_docs.bind("<KeyRelease-Down>", self.al_elegir_documento)
        self.lista_docs.bind("<ButtonPress-1>", self._doc_presionar)
        self.lista_docs.bind("<B1-Motion>", self._doc_arrastrar)
        self.lista_docs.bind("<ButtonRelease-1>", self._doc_soltar)
        bdocs = ttk.Frame(centro)
        bdocs.pack(fill="x")
        ttk.Button(bdocs, text="⤒", width=3, command=lambda: self.mover_documento("inicio")).pack(side="left")
        ttk.Button(bdocs, text="▲ Subir", command=lambda: self.mover_documento(-1)).pack(side="left", padx=2)
        ttk.Button(bdocs, text="▼ Bajar", command=lambda: self.mover_documento(1)).pack(side="left", padx=2)
        ttk.Button(bdocs, text="⤓", width=3, command=lambda: self.mover_documento("fin")).pack(side="left")
        ttk.Label(centro, text="Arrastrá un documento para moverlo\ncon todas sus hojas.",
                  foreground="#666").pack(anchor="w", pady=(4, 0))

        # --- Derecha: hojas
        der = ttk.Frame(panel)
        panel.add(der, weight=1)
        self.lbl_op = ttk.Label(der, text="Elegí una operación", font=("Segoe UI", 10, "bold"))
        self.lbl_op.pack(anchor="w")
        botones = ttk.Frame(der)
        botones.pack(fill="x", pady=(4, 0))
        for texto, cmd in [
            ("⏮", lambda: self.mover_seleccion("inicio")),
            ("◀ Mover", lambda: self.mover_seleccion(-1)),
            ("Mover ▶", lambda: self.mover_seleccion(1)),
            ("⏭", lambda: self.mover_seleccion("fin")),
            ("↺", lambda: self.rotar(-90)),
            ("↻", lambda: self.rotar(90)),
            ("Quitar", self.quitar),
            ("Vista previa", self.ver_grande),
        ]:
            ttk.Button(botones, text=texto, command=cmd, width=3 if len(texto) == 1 else None
                       ).pack(side="left", padx=1)
        ttk.Button(botones, text="Restaurar", command=self.restaurar).pack(side="left", padx=(8, 0))
        ttk.Label(botones, text="Tamaño").pack(side="left", padx=(12, 2))
        ttk.Scale(botones, from_=110, to=420, variable=self.var_tamano, length=int(110 * self.escala),
                  command=lambda _v: self.programar_dibujo(150)).pack(side="left")

        marco = ttk.Frame(der)
        marco.pack(fill="both", expand=True, pady=(4, 0))
        self.canvas = tk.Canvas(marco, bg="#e4e4e4", highlightthickness=0, takefocus=1)
        sb2 = ttk.Scrollbar(marco, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb2.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb2.pack(side="left", fill="y")
        self.canvas.bind("<Configure>", lambda e: self.programar_dibujo(80))
        self.canvas.bind("<ButtonPress-1>", self.al_presionar)
        self.canvas.bind("<B1-Motion>", self.al_arrastrar)
        self.canvas.bind("<ButtonRelease-1>", self.al_soltar)
        self.canvas.bind("<Double-1>", lambda e: self.ver_grande())
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._rueda))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-2, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(2, "units"))
        for tecla, cmd in [("<Delete>", self.quitar), ("<Left>", lambda: self.mover_seleccion(-1)),
                           ("<Right>", lambda: self.mover_seleccion(1)),
                           ("<Home>", lambda: self.mover_seleccion("inicio")),
                           ("<End>", lambda: self.mover_seleccion("fin")),
                           ("<space>", self.ver_grande), ("<Return>", self.ver_grande),
                           ("<Key-r>", lambda: self.rotar(90)),
                           ("<Control-a>", lambda: self.seleccionar(set(range(len(self.paginas)))))]:
            self.canvas.bind(tecla, lambda e, c=cmd: c())
        ttk.Label(der, foreground="#666", text=(
            "Clic = elegir · Ctrl+clic / Shift+clic = varias · Arrastrar = mover · "
            "Doble clic = ver en grande · Supr = quitar · ←/→ = mover")).pack(anchor="w", pady=(2, 0))

        # --- Abajo: acciones y estado
        abajo = ttk.Frame(self.root, padding=(8, 4, 8, 8))
        abajo.pack(fill="x")
        ttk.Button(abajo, text="Unificar pendientes", command=lambda: self.unificar(solo_pendientes=True)).pack(side="right")
        ttk.Button(abajo, text="Unificar seleccionadas", command=lambda: self.unificar(solo_pendientes=False)).pack(side="right", padx=4)
        ttk.Button(abajo, text="Abrir PDF unificado", command=self.abrir_unificado).pack(side="right", padx=4)
        self.progreso = ttk.Progressbar(abajo, length=int(160 * self.escala), mode="determinate")
        self.progreso.pack(side="right", padx=8)
        self.lbl_estado = ttk.Label(abajo, text="Listo.")
        self.lbl_estado.pack(side="left")

    # ------------------------------------------------------------ carpetas
    def dir_salida(self) -> Path:
        s = self.var_salida.get().strip()
        if s:
            return Path(s)
        return Path(self.var_base.get().strip()) / core.CARPETA_SALIDA_DEFAULT

    def elegir_base(self) -> None:
        d = filedialog.askdirectory(title="Carpeta base (la que contiene los clientes)",
                                    initialdir=self.var_base.get() or None)
        if d:
            self.var_base.set(d)
            self.escanear()

    def elegir_salida(self) -> None:
        d = filedialog.askdirectory(title="Dónde guardar los PDF unificados",
                                    initialdir=self.var_salida.get() or self.var_base.get() or None)
        if d:
            self.var_salida.set(d)
            self.escanear()

    def abrir_salida(self) -> None:
        d = self.dir_salida()
        d.mkdir(parents=True, exist_ok=True)
        abrir_en_sistema(d)

    def escanear(self) -> None:
        base = Path(self.var_base.get().strip())
        if not base.is_dir():
            messagebox.showwarning(APP_TITULO, "Elegí una carpeta base válida.")
            return
        # conservar el orden manual de lo que ya se había editado
        editadas = {(op.cliente, op.nombre): (op.paginas, op.firmas)
                    for op in self.operaciones if op.editada}
        try:
            self.operaciones = core.escanear(base, self.dir_salida(), self.var_excluir.get().split(","),
                                             self.var_plantilla.get().strip())
        except Exception as e:
            messagebox.showerror(APP_TITULO, f"Error al leer las carpetas:\n{e}")
            return
        cambios = []
        for op in self.operaciones:
            if (op.cliente, op.nombre) in editadas:
                previas, firmas = editadas[(op.cliente, op.nombre)]
                agregados, quitados = core.reconciliar(op, previas, firmas)
                if agregados or quitados:
                    cambios.append(self._texto_cambio(op, agregados, quitados))
        self.guardar_preferencias()
        self.arbol.delete(*self.arbol.get_children())
        clientes: dict[str, str] = {}
        for i, op in enumerate(self.operaciones):
            if op.cliente not in clientes:
                clientes[op.cliente] = self.arbol.insert("", "end", text=op.cliente, open=True)
            self.arbol.insert(clientes[op.cliente], "end", iid=f"op{i}", text=op.nombre)
        self.refrescar_estados()
        self.mostrar_operacion(None)
        pendientes = sum(1 for op in self.operaciones if op.estado(self.dir_salida()) != core.LISTO)
        self.estado(f"{len(self.operaciones)} operaciones en {len(clientes)} clientes — "
                    f"{pendientes} para unificar." + ("  " + " · ".join(cambios) if cambios else ""))

    def refrescar_estados(self) -> None:
        salida = self.dir_salida()
        for i, op in enumerate(self.operaciones):
            iid = f"op{i}"
            if self.arbol.exists(iid):
                est = op.estado(salida)
                self.arbol.item(iid, values=(est + (" ✎" if op.editada else ""),), tags=(est,))

    def ops_seleccionadas(self) -> list[core.Operacion]:
        elegidas: list[int] = []
        for iid in self.arbol.selection():
            for h in self.arbol.get_children(iid) or (iid,):
                if h.startswith("op") and int(h[2:]) not in elegidas:
                    elegidas.append(int(h[2:]))
        return [self.operaciones[i] for i in elegidas]

    # ------------------------------------------------------------ operación
    def al_elegir_operacion(self, _evento=None) -> None:
        sel = self.arbol.selection()
        op = self.operaciones[int(sel[0][2:])] if len(sel) == 1 and sel[0].startswith("op") else None
        if op is not self.op_actual:
            self.refrescar_operacion(op, mostrar=False)
            self.mostrar_operacion(op)

    def _texto_cambio(self, op: core.Operacion, agregados: int, quitados: int) -> str:
        partes = []
        if agregados:
            partes.append(f"{agregados} archivo(s) nuevo(s) agregado(s) al final")
        if quitados:
            partes.append(f"{quitados} ya no está(n)")
        return f"{op.nombre}: " + ", ".join(partes)

    def refrescar_operacion(self, op: core.Operacion | None, mostrar: bool = True) -> None:
        """Relee la carpeta de la operación por si se agregaron o borraron archivos."""
        if op is None or self.trabajando:
            return
        cambio = core.refrescar(op, self.var_plantilla.get().strip())
        if cambio is None:
            return
        self.refrescar_estados()
        self.estado("Carpeta actualizada — " + self._texto_cambio(op, *cambio))
        if mostrar and op is self.op_actual:
            sel = self.sel
            self.mostrar_operacion(op)
            self.seleccionar(sel)

    def al_volver_a_la_ventana(self, e) -> None:
        # al volver del Explorador, revisar si cambió la carpeta que se está viendo
        if e.widget is self.root:
            self.refrescar_operacion(self.op_actual)

    def mostrar_operacion(self, op: core.Operacion | None) -> None:
        self.render.cerrar()
        self.op_actual = op
        self.sel, self.ancla = set(), None
        self.paginas = []
        if op is None:
            self.lbl_op.config(text="Elegí una operación")
        else:
            try:
                self.paginas = list(core.paginas_de(op))
            except Exception as e:
                messagebox.showerror(APP_TITULO, f"No se pudieron leer los PDF de '{op.nombre}':\n{e}")
        self.actualizar_titulo()
        self.actualizar_documentos()
        self.canvas.yview_moveto(0)
        self.dibujar_miniaturas()
        if self.vista and self.vista.winfo_exists():
            self.vista.indice = 0
            self.vista.dibujar(arriba=True)

    def actualizar_titulo(self) -> None:
        op = self.op_actual
        if op:
            extra = "   (orden manual ✎)" if op.editada else ""
            if op.ignorados:
                extra += "   · se ignora " + ", ".join(p.name for p in op.ignorados) + " (unificado anterior)"
            self.lbl_op.config(text=f"{op.cliente} / {op.nombre}  →  {op.archivo}  —  "
                                    f"{len(self.paginas)} hojas{extra}")

    def cambio(self, mostrar: bool = True) -> None:
        """Llamar después de cualquier modificación de self.paginas."""
        if self.op_actual is not None:
            self.op_actual.paginas = self.paginas
            self.actualizar_titulo()
            self.refrescar_estados()
            self.actualizar_documentos()
        self.dibujar_miniaturas()
        if mostrar and self.sel:
            self.asegurar_visible(min(self.sel))
        if self.vista and self.vista.winfo_exists():
            if self.sel:
                self.vista.indice = min(self.sel)
            self.vista.dibujar()

    # ------------------------------------------------------------ documentos
    def documentos(self) -> list[tuple[Path, list[core.Pagina]]]:
        grupos: dict[Path, list[core.Pagina]] = {}
        for p in self.paginas:
            grupos.setdefault(p.archivo, []).append(p)
        return list(grupos.items())

    def actualizar_documentos(self) -> None:
        self.lista_docs.delete(*self.lista_docs.get_children())
        for i, (archivo, pags) in enumerate(self.documentos()):
            self.lista_docs.insert("", "end", iid=f"d{i}", text=f"{i + 1}. {archivo.name}",
                                   values=(len(pags),))
        self.sincronizar_documento()

    def sincronizar_documento(self) -> None:
        """Resalta en la lista el documento de las hojas elegidas (si es uno solo)."""
        archivos = {self.paginas[i].archivo for i in self.sel}
        iid = ""
        if len(archivos) == 1:
            archivo = archivos.pop()
            for n, (a, _) in enumerate(self.documentos()):
                if a == archivo:
                    iid = f"d{n}"
        if iid:
            self.lista_docs.selection_set(iid)
            self.lista_docs.see(iid)
        else:
            self.lista_docs.selection_remove(*self.lista_docs.selection())

    def doc_elegido(self) -> int | None:
        sel = self.lista_docs.selection()
        return int(sel[0][1:]) if sel else None

    def al_elegir_documento(self, _e=None) -> None:
        i = self.doc_elegido()
        docs = self.documentos()
        if i is None or i >= len(docs):
            return
        archivo = docs[i][0]
        indices = {n for n, p in enumerate(self.paginas) if p.archivo == archivo}
        self.seleccionar(indices, mostrar=True)

    def reordenar_documentos(self, origen: int, destino: int) -> None:
        docs = self.documentos()
        if not (0 <= origen < len(docs)) or not (0 <= destino < len(docs)) or origen == destino:
            return
        docs.insert(destino, docs.pop(origen))
        self.paginas = [p for _, pags in docs for p in pags]
        archivo = docs[destino][0]
        self.sel = {n for n, p in enumerate(self.paginas) if p.archivo == archivo}
        self.ancla = min(self.sel)
        self.cambio()

    def mover_documento(self, delta) -> None:
        i = self.doc_elegido()
        if i is None:
            return
        n = len(self.documentos())
        destino = 0 if delta == "inicio" else n - 1 if delta == "fin" else i + delta
        self.reordenar_documentos(i, max(0, min(n - 1, destino)))

    def _doc_presionar(self, e) -> None:
        fila = self.lista_docs.identify_row(e.y)
        self._doc_origen = int(fila[1:]) if fila else None

    def _doc_arrastrar(self, e) -> None:
        if self._doc_origen is None:
            return
        self.lista_docs.configure(cursor="sb_v_double_arrow")

    def _doc_soltar(self, e) -> None:
        self.lista_docs.configure(cursor="")
        origen, self._doc_origen = self._doc_origen, None
        fila = self.lista_docs.identify_row(e.y)
        if origen is None:
            return
        if fila:
            destino = int(fila[1:])
        else:
            destino = len(self.documentos()) - 1 if e.y > 10 else 0
        if destino != origen:
            self.reordenar_documentos(origen, destino)
        else:
            self.al_elegir_documento()

    # ------------------------------------------------------------ miniaturas
    def columnas(self) -> int:
        return max(1, (self.canvas.winfo_width() - 10) // self.celda[0])

    def programar_dibujo(self, ms: int) -> None:
        if self._dibujo_programado:
            self.root.after_cancel(self._dibujo_programado)
        self._dibujo_programado = self.root.after(ms, self.dibujar_miniaturas)

    def dibujar_miniaturas(self) -> None:
        self._dibujo_programado = None
        self._generacion += 1
        self._pendientes = []
        self._en_pantalla: list[tk.PhotoImage] = []
        c = self.canvas
        c.delete("all")
        cw, ch = self.celda
        alto = self.mini_alto
        ancho = cw - int(16 * self.escala)
        cols = self.columnas()
        fuente = ("Segoe UI", 8)
        for i, pag in enumerate(self.paginas):
            fila, col = divmod(i, cols)
            x0, y0 = 10 + col * cw, 10 + fila * ch
            cx = x0 + (cw - 8) // 2
            if i in self.sel:
                c.create_rectangle(x0 - 3, y0 - 3, x0 + cw - 5, y0 + ch - 6,
                                   outline=COLOR_SEL, width=3, fill=COLOR_SEL_FONDO)
            img = self.render.en_cache(pag, ancho, alto)
            if img is not None:
                c.create_image(cx, y0 + alto // 2, image=img)
                self._en_pantalla.append(img)
            else:
                item = c.create_text(cx, y0 + alto // 2, text="…", fill="#888")
                self._pendientes.append((i, pag, cx, y0 + alto // 2, item))
            nombre = pag.archivo.stem
            maximo = max(10, int(ancho / (6.2 * self.escala)))
            if len(nombre) > maximo:
                nombre = nombre[: maximo - 1] + "…"
            c.create_text(cx, y0 + alto + int(12 * self.escala), text=f"{i + 1}. {nombre}", font=fuente)
            c.create_text(cx, y0 + alto + int(27 * self.escala), text=f"pág. {pag.indice + 1}",
                          font=fuente, fill="#666")
        filas = (len(self.paginas) + cols - 1) // cols
        c.configure(scrollregion=(0, 0, cols * cw + 10, filas * ch + 20))
        if self._pendientes:
            self.root.after(1, self._renderizar_pendientes, self._generacion)

    def _renderizar_pendientes(self, generacion: int) -> None:
        """Renderiza miniaturas de a poco para que la ventana no se congele."""
        if generacion != self._generacion:
            return
        alto = self.mini_alto
        ancho = self.celda[0] - int(16 * self.escala)
        # primero las que están a la vista
        top = self.canvas.canvasy(0)
        bottom = top + self.canvas.winfo_height()
        self._pendientes.sort(key=lambda t: 0 if top - alto <= t[3] <= bottom + alto else 1)
        for _ in range(3):
            if not self._pendientes:
                return
            _, pag, x, y, item = self._pendientes.pop(0)
            try:
                img = self.render.imagen(pag, ancho, alto)
                nuevo = self.canvas.create_image(x, y, image=img)
                self._en_pantalla.append(img)
                self.canvas.tag_lower(nuevo, item)  # encima del recuadro de selección
                self.canvas.delete(item)
            except Exception as e:
                self.canvas.itemconfig(item, text=motivo_error(pag, e), justify="center", fill="#b30000")
        self.root.after(1, self._renderizar_pendientes, generacion)

    def indice_en(self, x: float, y: float) -> int | None:
        cw, ch = self.celda
        col, fila = int((x - 10) // cw), int((y - 10) // ch)
        if col < 0 or fila < 0 or col >= self.columnas():
            return None
        i = fila * self.columnas() + col
        return i if i < len(self.paginas) else None

    def destino_en(self, x: float, y: float) -> int:
        """Posición de inserción (0..len) para soltar en x, y."""
        cw, ch = self.celda
        cols = self.columnas()
        fila = max(0, int((y - 10) // ch))
        col = int((x - 10) // cw)
        if col < 0:
            i = fila * cols
        elif col >= cols:
            i = fila * cols + cols
        else:
            i = fila * cols + col + ((x - 10) % cw > cw / 2)
        return max(0, min(i, len(self.paginas)))

    def asegurar_visible(self, i: int) -> None:
        cols = self.columnas()
        _, ch = self.celda
        y = 10 + (i // cols) * ch
        total = max(1, ((len(self.paginas) + cols - 1) // cols) * ch + 20)
        top = self.canvas.canvasy(0)
        alto = self.canvas.winfo_height()
        if y < top or y + ch > top + alto:
            self.canvas.yview_moveto(max(0, (y - 10) / total))

    def seleccionar(self, indices: set[int], mostrar: bool = False) -> None:
        self.sel = {i for i in indices if 0 <= i < len(self.paginas)}
        self.ancla = min(self.sel) if self.sel else None
        self.dibujar_miniaturas()
        self.sincronizar_documento()
        if mostrar and self.sel:
            self.asegurar_visible(min(self.sel))

    def al_presionar(self, e) -> None:
        self.canvas.focus_set()
        x, y = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
        i = self.indice_en(x, y)
        ctrl, shift = e.state & 0x0004, e.state & 0x0001
        self.arrastre = None
        if i is None:
            if not (ctrl or shift):
                self.seleccionar(set())
            return
        if shift and self.ancla is not None:
            a, b = sorted((self.ancla, i))
            self.sel = set(range(a, b + 1))
        elif ctrl:
            self.sel ^= {i}
            self.ancla = i
        elif i not in self.sel:
            self.sel, self.ancla = {i}, i
        self.dibujar_miniaturas()
        self.sincronizar_documento()
        self.arrastre = {"x": e.x, "y": e.y, "indice": i, "movio": False, "mod": bool(ctrl or shift)}

    def al_arrastrar(self, e) -> None:
        a = self.arrastre
        if not a or not self.sel:
            return
        if not a["movio"] and abs(e.x - a["x"]) + abs(e.y - a["y"]) < 6:
            return
        a["movio"] = True
        self.canvas.configure(cursor="fleur")
        destino = self.destino_en(self.canvas.canvasx(e.x), self.canvas.canvasy(e.y))
        self.canvas.delete("marcador")
        cw, ch = self.celda
        cols = self.columnas()
        fila, col = divmod(destino, cols)
        if col == 0 and destino and destino == len(self.paginas):
            fila, col = fila - 1, cols
        mx, my = 10 + col * cw - 6, 10 + fila * ch
        self.canvas.create_line(mx, my - 4, mx, my + ch - 8, fill="#e8590c", width=5, tags="marcador")
        if e.y < 40:
            self.canvas.yview_scroll(-1, "units")
        elif e.y > self.canvas.winfo_height() - 40:
            self.canvas.yview_scroll(1, "units")

    def al_soltar(self, e) -> None:
        a, self.arrastre = self.arrastre, None
        self.canvas.configure(cursor="")
        self.canvas.delete("marcador")
        if not a:
            return
        if not a["movio"]:
            if not a["mod"] and len(self.sel) > 1:  # clic simple sobre una selección múltiple
                self.seleccionar({a["indice"]})
                self.ancla = a["indice"]
            return
        self.mover_a(self.destino_en(self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)))

    def _rueda(self, e) -> None:
        if e.state & 0x0004:  # Ctrl + rueda = tamaño de miniaturas
            self.var_tamano.set(max(110, min(420, self.var_tamano.get() + (20 if e.delta > 0 else -20))))
            self.programar_dibujo(100)
        else:
            self.canvas.yview_scroll(int(-e.delta / 40) or (-1 if e.delta > 0 else 1), "units")

    # --------------------------------------------------------- edición hojas
    def mover_a(self, destino: int) -> None:
        """Mueve las hojas seleccionadas (en bloque) a la posición destino."""
        if not self.sel:
            return
        orden = sorted(self.sel)
        bloque = [self.paginas[i] for i in orden]
        resto = [p for i, p in enumerate(self.paginas) if i not in self.sel]
        destino -= sum(1 for i in orden if i < destino)
        destino = max(0, min(destino, len(resto)))
        nuevas = resto[:destino] + bloque + resto[destino:]
        if nuevas == self.paginas:
            return
        self.paginas = nuevas
        self.sel = set(range(destino, destino + len(bloque)))
        self.ancla = destino
        self.cambio()

    def mover_seleccion(self, delta) -> None:
        if not self.sel:
            return
        if delta == "inicio":
            self.mover_a(0)
        elif delta == "fin":
            self.mover_a(len(self.paginas))
        elif delta < 0 and min(self.sel) > 0:
            self.mover_a(min(self.sel) - 1)
        elif delta > 0 and max(self.sel) < len(self.paginas) - 1:
            self.mover_a(max(self.sel) + 2)

    def rotar(self, grados: int = 90) -> None:
        if not self.sel:
            return
        for i in self.sel:
            pag = self.paginas[i]
            # copia nueva: la miniatura en caché se identifica por la rotación
            self.paginas[i] = core.Pagina(pag.archivo, pag.indice, (pag.rotacion + grados) % 360)
        self.cambio(mostrar=False)

    def quitar(self) -> None:
        if not self.sel:
            return
        primero = min(self.sel)
        self.paginas = [p for i, p in enumerate(self.paginas) if i not in self.sel]
        self.sel = {min(primero, len(self.paginas) - 1)} if self.paginas else set()
        self.ancla = min(self.sel) if self.sel else None
        self.cambio()

    def restaurar(self) -> None:
        if self.op_actual is not None and self.op_actual.editada:
            if messagebox.askyesno(APP_TITULO, "¿Descartar los cambios de orden de esta operación?"):
                self.op_actual.paginas = None
                self.refrescar_estados()
                self.mostrar_operacion(self.op_actual)

    def ver_grande(self) -> None:
        if not self.paginas:
            return
        i = min(self.sel) if self.sel else 0
        if not self.sel:
            self.seleccionar({0})
        if self.vista and self.vista.winfo_exists():
            self.vista.indice = i
            self.vista.dibujar(arriba=True)
            self.vista.lift()
            self.vista.focus_set()
        else:
            self.vista = VistaPrevia(self, i)

    # ----------------------------------------------------------- unificar
    def unificar(self, solo_pendientes: bool) -> None:
        if self.trabajando:
            return
        salida = self.dir_salida()
        if solo_pendientes:
            ops = [op for op in self.operaciones if op.editada or op.estado(salida) != core.LISTO]
            if not ops:
                messagebox.showinfo(APP_TITULO, "No hay operaciones pendientes. Todo está unificado.")
                return
        else:
            ops = self.ops_seleccionadas()
            if not ops:
                messagebox.showinfo(APP_TITULO, "Seleccioná una o más operaciones (o un cliente entero).")
                return
        self.trabajando = True
        self.progreso.configure(maximum=len(ops), value=0)
        threading.Thread(target=self._trabajar, args=(ops, salida), daemon=True).start()
        self.root.after(100, self._revisar_cola)

    def _trabajar(self, ops: list[core.Operacion], salida: Path) -> None:
        errores = []
        for n, op in enumerate(ops, 1):
            self.cola.put(("progreso", n - 1, f"Unificando {op.cliente} / {op.nombre}…"))
            try:
                core.unificar(op, salida)
            except Exception as e:
                errores.append(f"{op.cliente} / {op.nombre}: {e}")
        self.cola.put(("fin", len(ops), errores))

    def _revisar_cola(self) -> None:
        try:
            while True:
                msg = self.cola.get_nowait()
                if msg[0] == "progreso":
                    self.progreso.configure(value=msg[1])
                    self.estado(msg[2])
                else:
                    _, total, errores = msg
                    self.trabajando = False
                    self.progreso.configure(value=total)
                    self.refrescar_estados()
                    ok = total - len(errores)
                    self.estado(f"Listo: {ok} PDF generados en {self.dir_salida()}")
                    if errores:
                        messagebox.showwarning(APP_TITULO, f"{ok} OK, {len(errores)} con error:\n\n"
                                               + "\n".join(errores[:20]))
                    return
        except queue.Empty:
            pass
        self.root.after(100, self._revisar_cola)

    def abrir_unificado(self) -> None:
        ops = self.ops_seleccionadas()
        if len(ops) != 1:
            return
        destino = ops[0].salida(self.dir_salida())
        if destino.exists():
            abrir_en_sistema(destino)
        else:
            self.estado("Esa operación todavía no está unificada.")

    # ------------------------------------------------------------- varios
    def estado(self, texto: str) -> None:
        self.lbl_estado.config(text=texto)

    def guardar_preferencias(self) -> None:
        self.cfg.update(base=self.var_base.get(), salida=self.var_salida.get(),
                        plantilla=self.var_plantilla.get(), excluir=self.var_excluir.get(),
                        tamano=int(self.var_tamano.get()), geometria=self.root.geometry())
        guardar_config(self.cfg)

    def salir(self) -> None:
        if self.trabajando and not messagebox.askyesno(APP_TITULO, "Se están generando PDFs. ¿Salir igual?"):
            return
        self.guardar_preferencias()
        self.render.cerrar()
        self.root.destroy()


def main() -> None:
    activar_alta_resolucion()
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
