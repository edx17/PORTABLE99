"""Unificador de PDF — interfaz gráfica (Tkinter)."""

from __future__ import annotations

import base64
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
MINI_ALTO = 180  # alto de las miniaturas en px
CELDA_W, CELDA_H = 170, MINI_ALTO + 44
COLOR_SEL = "#1f6feb"


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


def abrir_en_sistema(ruta: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(ruta)  # type: ignore[attr-defined]
    else:
        import subprocess

        subprocess.Popen(["xdg-open", str(ruta)])


class Renderizador:
    """Abre cada PDF una sola vez y cachea imágenes renderizadas."""

    def __init__(self) -> None:
        self.docs: dict[Path, pymupdf.Document] = {}
        self.cache: dict[tuple, tk.PhotoImage] = {}

    def imagen(self, pag: core.Pagina, alto: int, ancho_max: int | None = None) -> tk.PhotoImage:
        clave = (pag.archivo, pag.indice, pag.rotacion, alto, ancho_max)
        if clave in self.cache:
            return self.cache[clave]
        if pag.archivo not in self.docs:
            self.docs[pag.archivo] = core.abrir(pag.archivo)
        page = self.docs[pag.archivo][pag.indice]
        rect = page.rect  # ya contempla la rotación propia de la página
        w, h = (rect.height, rect.width) if pag.rotacion % 180 else (rect.width, rect.height)
        zoom = alto / h
        if ancho_max and w * zoom > ancho_max:
            zoom = ancho_max / w
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom).prerotate(pag.rotacion), alpha=False)
        img = tk.PhotoImage(data=base64.b64encode(pix.tobytes("png")))
        if len(self.cache) > 400:
            self.cache.clear()
        self.cache[clave] = img
        return img

    def cerrar(self) -> None:
        for d in self.docs.values():
            d.close()
        self.docs.clear()
        self.cache.clear()


class VistaPrevia(tk.Toplevel):
    """Ventana para ver una hoja en grande y navegar entre las hojas."""

    def __init__(self, app: "App", indice: int) -> None:
        super().__init__(app.root)
        self.app = app
        self.indice = indice
        self.title("Vista previa")
        self.geometry("820x980")
        barra = ttk.Frame(self, padding=4)
        barra.pack(fill="x")
        ttk.Button(barra, text="◀ Anterior", command=lambda: self.ir(-1)).pack(side="left")
        ttk.Button(barra, text="Siguiente ▶", command=lambda: self.ir(1)).pack(side="left", padx=4)
        ttk.Button(barra, text="↻ Rotar", command=self.rotar).pack(side="left", padx=12)
        self.lbl = ttk.Label(barra)
        self.lbl.pack(side="left", padx=8)
        self.canvas = tk.Canvas(self, bg="#555")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.programar())
        self.bind("<Left>", lambda e: self.ir(-1))
        self.bind("<Right>", lambda e: self.ir(1))
        self.bind("<Escape>", lambda e: self.destroy())
        self._pendiente = None
        self.focus_set()

    def programar(self) -> None:
        if self._pendiente:
            self.after_cancel(self._pendiente)
        self._pendiente = self.after(80, self.dibujar)

    def ir(self, delta: int) -> None:
        total = len(self.app.paginas)
        if total:
            self.indice = (self.indice + delta) % total
            self.app.seleccionar(self.indice)
            self.dibujar()

    def rotar(self) -> None:
        self.app.seleccionar(self.indice)
        self.app.rotar()
        self.dibujar()

    def dibujar(self) -> None:
        self._pendiente = None
        paginas = self.app.paginas
        self.canvas.delete("all")
        if not paginas:
            self.lbl.config(text="(sin hojas)")
            return
        self.indice = min(self.indice, len(paginas) - 1)
        pag = paginas[self.indice]
        w = max(self.canvas.winfo_width(), 100)
        h = max(self.canvas.winfo_height(), 100)
        try:
            img = self.app.render.imagen(pag, h - 20, w - 20)
        except Exception as e:
            self.canvas.create_text(w // 2, h // 2, text=f"Error: {e}", fill="white")
            return
        self.canvas.create_image(w // 2, h // 2, image=img)
        self.lbl.config(text=f"Hoja {self.indice + 1} de {len(paginas)}  —  {pag.etiqueta()}")


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.cfg = cargar_config()
        self.operaciones: list[core.Operacion] = []
        self.op_actual: core.Operacion | None = None
        self.paginas: list[core.Pagina] = []
        self.sel: int | None = None
        self.render = Renderizador()
        self.arrastre: int | None = None
        self.vista: VistaPrevia | None = None
        self.cola: queue.Queue = queue.Queue()
        self.trabajando = False

        root.title(APP_TITULO)
        root.geometry(self.cfg.get("geometria", "1280x800"))
        root.minsize(900, 550)
        self._armar_ui()
        root.protocol("WM_DELETE_WINDOW", self.salir)
        if self.var_base.get() and Path(self.var_base.get()).is_dir():
            root.after(100, self.escanear)

    # ------------------------------------------------------------------ UI
    def _armar_ui(self) -> None:
        arriba = ttk.Frame(self.root, padding=(8, 8, 8, 4))
        arriba.pack(fill="x")
        arriba.columnconfigure(1, weight=1)

        self.var_base = tk.StringVar(value=self.cfg.get("base", ""))
        self.var_salida = tk.StringVar(value=self.cfg.get("salida", ""))
        self.var_excluir = tk.StringVar(value=self.cfg.get("excluir", ", ".join(core.EXCLUIR_DEFAULT)))

        ttk.Label(arriba, text="Carpeta base:").grid(row=0, column=0, sticky="w")
        ttk.Entry(arriba, textvariable=self.var_base).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(arriba, text="Examinar…", command=self.elegir_base).grid(row=0, column=2)
        ttk.Button(arriba, text="Escanear", command=self.escanear).grid(row=0, column=3, padx=(4, 0))

        ttk.Label(arriba, text="Guardar en:").grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(arriba, textvariable=self.var_salida).grid(row=1, column=1, sticky="ew", padx=4, pady=(4, 0))
        ttk.Button(arriba, text="Examinar…", command=self.elegir_salida).grid(row=1, column=2, pady=(4, 0))
        ttk.Button(arriba, text="Abrir", command=self.abrir_salida).grid(row=1, column=3, padx=(4, 0), pady=(4, 0))

        ttk.Label(arriba, text="Ignorar carpetas:").grid(row=2, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(arriba, textvariable=self.var_excluir).grid(row=2, column=1, sticky="ew", padx=4, pady=(4, 0))
        ttk.Label(arriba, text="(separadas por coma)", foreground="#666").grid(row=2, column=2, columnspan=2, sticky="w", pady=(4, 0))

        panel = ttk.PanedWindow(self.root, orient="horizontal")
        panel.pack(fill="both", expand=True, padx=8, pady=4)

        # --- Izquierda: clientes / operaciones
        izq = ttk.Frame(panel)
        panel.add(izq, weight=1)
        self.arbol = ttk.Treeview(izq, columns=("estado", "pdfs"), selectmode="extended")
        self.arbol.heading("#0", text="Cliente / Operación")
        self.arbol.heading("estado", text="Estado")
        self.arbol.heading("pdfs", text="PDFs")
        self.arbol.column("#0", width=230)
        self.arbol.column("estado", width=90, anchor="center")
        self.arbol.column("pdfs", width=45, anchor="center")
        self.arbol.tag_configure(core.NUEVO, foreground="#b35900")
        self.arbol.tag_configure(core.MODIFICADO, foreground="#b30000")
        self.arbol.tag_configure(core.LISTO, foreground="#1a7f37")
        sb = ttk.Scrollbar(izq, orient="vertical", command=self.arbol.yview)
        self.arbol.configure(yscrollcommand=sb.set)
        self.arbol.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.arbol.bind("<<TreeviewSelect>>", self.al_elegir_operacion)
        self.arbol.bind("<Double-1>", lambda e: self.abrir_unificado())

        # --- Derecha: hojas de la operación
        der = ttk.Frame(panel)
        panel.add(der, weight=3)
        barra = ttk.Frame(der)
        barra.pack(fill="x")
        self.lbl_op = ttk.Label(barra, text="Elegí una operación", font=("Segoe UI", 10, "bold"))
        self.lbl_op.pack(side="top", anchor="w")
        botones = ttk.Frame(der)
        botones.pack(fill="x", pady=(4, 0))
        ttk.Label(botones, text="Arrastrá las hojas para reordenar · doble clic = vista previa",
                  foreground="#666").pack(side="left")
        for texto, cmd in [
            ("Restaurar orden", self.restaurar),
            ("Quitar hoja", self.quitar),
            ("↻ Rotar", self.rotar),
            ("Mover ▶", lambda: self.mover(1)),
            ("◀ Mover", lambda: self.mover(-1)),
            ("Vista previa", self.ver_grande),
        ]:
            ttk.Button(botones, text=texto, command=cmd).pack(side="right", padx=2)

        marco = ttk.Frame(der)
        marco.pack(fill="both", expand=True, pady=(4, 0))
        self.canvas = tk.Canvas(marco, bg="#e9e9e9", highlightthickness=0)
        sb2 = ttk.Scrollbar(marco, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=sb2.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb2.pack(side="left", fill="y")
        self.canvas.bind("<Configure>", lambda e: self.dibujar_miniaturas())
        self.canvas.bind("<ButtonPress-1>", self.al_presionar)
        self.canvas.bind("<B1-Motion>", self.al_arrastrar)
        self.canvas.bind("<ButtonRelease-1>", self.al_soltar)
        self.canvas.bind("<Double-1>", lambda e: self.ver_grande())
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._rueda))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))
        for tecla, cmd in [("<Delete>", self.quitar), ("<Left>", lambda: self.mover(-1)),
                           ("<Right>", lambda: self.mover(1)), ("<space>", self.ver_grande)]:
            self.canvas.bind(tecla, lambda e, c=cmd: c())

        # --- Abajo: acciones y estado
        abajo = ttk.Frame(self.root, padding=(8, 4, 8, 8))
        abajo.pack(fill="x")
        ttk.Button(abajo, text="Unificar pendientes", command=lambda: self.unificar(solo_pendientes=True)).pack(side="right")
        ttk.Button(abajo, text="Unificar seleccionadas", command=lambda: self.unificar(solo_pendientes=False)).pack(side="right", padx=4)
        ttk.Button(abajo, text="Abrir PDF unificado", command=self.abrir_unificado).pack(side="right", padx=4)
        self.progreso = ttk.Progressbar(abajo, length=180, mode="determinate")
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
            self.refrescar_estados()

    def abrir_salida(self) -> None:
        d = self.dir_salida()
        d.mkdir(parents=True, exist_ok=True)
        abrir_en_sistema(d)

    def escanear(self) -> None:
        base = Path(self.var_base.get().strip())
        if not base.is_dir():
            messagebox.showwarning(APP_TITULO, "Elegí una carpeta base válida.")
            return
        excluir = [e for e in self.var_excluir.get().split(",")]
        try:
            self.operaciones = core.escanear(base, self.dir_salida(), excluir)
        except Exception as e:
            messagebox.showerror(APP_TITULO, f"Error al leer las carpetas:\n{e}")
            return
        self.guardar_preferencias()
        self.arbol.delete(*self.arbol.get_children())
        clientes: dict[str, str] = {}
        for i, op in enumerate(self.operaciones):
            if op.cliente not in clientes:
                clientes[op.cliente] = self.arbol.insert("", "end", text=op.cliente, open=True)
            self.arbol.insert(clientes[op.cliente], "end", iid=f"op{i}", text=op.nombre,
                              values=("", len(op.pdfs)))
        self.refrescar_estados()
        self.mostrar_operacion(None)
        pendientes = sum(1 for op in self.operaciones if op.estado(self.dir_salida()) != core.LISTO)
        self.estado(f"{len(self.operaciones)} operaciones en {len(clientes)} clientes — {pendientes} para unificar.")

    def refrescar_estados(self) -> None:
        salida = self.dir_salida()
        for i, op in enumerate(self.operaciones):
            iid = f"op{i}"
            if not self.arbol.exists(iid):
                continue
            est = op.estado(salida)
            texto = est + (" ✎" if op.editada else "")
            self.arbol.item(iid, values=(texto, len(op.pdfs)), tags=(est,))

    def ops_seleccionadas(self) -> list[core.Operacion]:
        elegidas: list[int] = []
        for iid in self.arbol.selection():
            hijos = self.arbol.get_children(iid) or (iid,)
            for h in hijos:
                if h.startswith("op"):
                    idx = int(h[2:])
                    if idx not in elegidas:
                        elegidas.append(idx)
        return [self.operaciones[i] for i in elegidas]

    # ------------------------------------------------------------- hojas
    def al_elegir_operacion(self, _evento=None) -> None:
        sel = self.arbol.selection()
        op = None
        if len(sel) == 1 and sel[0].startswith("op"):
            op = self.operaciones[int(sel[0][2:])]
        if op is not self.op_actual:
            self.mostrar_operacion(op)

    def mostrar_operacion(self, op: core.Operacion | None) -> None:
        self.render.cerrar()
        self.op_actual = op
        self.sel = None
        if op is None:
            self.paginas = []
            self.lbl_op.config(text="Elegí una operación")
        else:
            try:
                self.paginas = list(core.paginas_de(op))
            except Exception as e:
                self.paginas = []
                messagebox.showerror(APP_TITULO, f"No se pudieron leer los PDF de '{op.nombre}':\n{e}")
            self.actualizar_titulo()
        self.canvas.yview_moveto(0)
        self.dibujar_miniaturas()
        if self.vista and self.vista.winfo_exists():
            self.vista.indice = 0
            self.vista.dibujar()

    def actualizar_titulo(self) -> None:
        op = self.op_actual
        if op:
            extra = "  (orden manual)" if op.editada else ""
            self.lbl_op.config(text=f"{op.cliente} / {op.nombre} — {len(self.paginas)} hojas{extra}")

    def columnas(self) -> int:
        return max(1, (self.canvas.winfo_width() - 10) // CELDA_W)

    def dibujar_miniaturas(self) -> None:
        c = self.canvas
        c.delete("all")
        cols = self.columnas()
        for i, pag in enumerate(self.paginas):
            fila, col = divmod(i, cols)
            x0, y0 = 10 + col * CELDA_W, 10 + fila * CELDA_H
            cx = x0 + (CELDA_W - 10) // 2
            if i == self.sel:
                c.create_rectangle(x0 - 4, y0 - 4, x0 + CELDA_W - 6, y0 + CELDA_H - 8,
                                   outline=COLOR_SEL, width=3, fill="#d6e4ff")
            try:
                img = self.render.imagen(pag, MINI_ALTO, CELDA_W - 20)
                c.create_image(cx, y0 + MINI_ALTO // 2, image=img)
            except Exception:
                c.create_rectangle(cx - 60, y0, cx + 60, y0 + MINI_ALTO, fill="white")
                c.create_text(cx, y0 + MINI_ALTO // 2, text="No se pudo\nmostrar", justify="center")
            nombre = pag.archivo.stem
            if len(nombre) > 22:
                nombre = nombre[:20] + "…"
            c.create_text(cx, y0 + MINI_ALTO + 12, text=f"{i + 1}. {nombre}", font=("Segoe UI", 8))
            c.create_text(cx, y0 + MINI_ALTO + 26, text=f"pág. {pag.indice + 1}", font=("Segoe UI", 8),
                          fill="#666")
        filas = (len(self.paginas) + cols - 1) // cols
        c.configure(scrollregion=(0, 0, cols * CELDA_W + 10, filas * CELDA_H + 20))

    def indice_en(self, x: float, y: float, para_soltar: bool = False) -> int | None:
        cols = self.columnas()
        col = int((x - 10) // CELDA_W)
        fila = int((y - 10) // CELDA_H)
        if col < 0 or fila < 0:
            return 0 if para_soltar else None
        if para_soltar:
            if col >= cols:  # a la derecha de la última columna: después de esa fila
                i = fila * cols + cols
            else:
                i = fila * cols + col + ((x - 10) % CELDA_W > CELDA_W / 2)
            return min(i, len(self.paginas))
        if col >= cols:
            return None
        i = fila * cols + col
        return i if i < len(self.paginas) else None

    def seleccionar(self, i: int | None) -> None:
        self.sel = i
        self.dibujar_miniaturas()
        if i is not None:
            cols = self.columnas()
            filas = max(1, (len(self.paginas) + cols - 1) // cols)
            fila = i // cols
            top, bottom = self.canvas.yview()
            if not top <= fila / filas <= bottom - 1 / filas:
                self.canvas.yview_moveto(max(0, fila / filas - 0.05))

    def al_presionar(self, e) -> None:
        self.canvas.focus_set()
        i = self.indice_en(self.canvas.canvasx(e.x), self.canvas.canvasy(e.y))
        self.arrastre = i
        self.seleccionar(i)

    def al_arrastrar(self, e) -> None:
        if self.arrastre is None:
            return
        x, y = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
        destino = self.indice_en(x, y, para_soltar=True)
        self.canvas.delete("marcador")
        if destino is None:
            return
        cols = self.columnas()
        fila, col = divmod(destino, cols)
        if destino == len(self.paginas) and destino % cols == 0 and destino:
            fila, col = divmod(destino - 1, cols)
            col += 1
        mx = 10 + col * CELDA_W - 6
        my = 10 + fila * CELDA_H
        self.canvas.create_line(mx, my - 4, mx, my + CELDA_H - 8, fill=COLOR_SEL, width=4, tags="marcador")
        # desplazamiento automático al arrastrar cerca de los bordes
        if e.y < 30:
            self.canvas.yview_scroll(-1, "units")
        elif e.y > self.canvas.winfo_height() - 30:
            self.canvas.yview_scroll(1, "units")

    def al_soltar(self, e) -> None:
        origen, self.arrastre = self.arrastre, None
        self.canvas.delete("marcador")
        if origen is None:
            return
        destino = self.indice_en(self.canvas.canvasx(e.x), self.canvas.canvasy(e.y), para_soltar=True)
        if destino is None or destino in (origen, origen + 1):
            return
        pag = self.paginas.pop(origen)
        if destino > origen:
            destino -= 1
        self.paginas.insert(destino, pag)
        self.marcar_editada()
        self.seleccionar(destino)

    def _rueda(self, e) -> None:
        self.canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")

    def marcar_editada(self) -> None:
        if self.op_actual is not None:
            self.op_actual.paginas = self.paginas
            self.actualizar_titulo()
            self.refrescar_estados()
            if self.vista and self.vista.winfo_exists():
                self.vista.dibujar()

    def mover(self, delta: int) -> None:
        if self.sel is None:
            return
        nuevo = self.sel + delta
        if 0 <= nuevo < len(self.paginas):
            p = self.paginas
            p[self.sel], p[nuevo] = p[nuevo], p[self.sel]
            self.marcar_editada()
            self.seleccionar(nuevo)

    def rotar(self) -> None:
        if self.sel is not None:
            pag = self.paginas[self.sel]
            pag.rotacion = (pag.rotacion + 90) % 360
            self.marcar_editada()
            self.dibujar_miniaturas()

    def quitar(self) -> None:
        if self.sel is None:
            return
        self.paginas.pop(self.sel)
        self.marcar_editada()
        self.seleccionar(min(self.sel, len(self.paginas) - 1) if self.paginas else None)

    def restaurar(self) -> None:
        if self.op_actual is not None:
            self.op_actual.paginas = None
            self.refrescar_estados()
            self.mostrar_operacion(self.op_actual)

    def ver_grande(self) -> None:
        if not self.paginas:
            return
        i = self.sel if self.sel is not None else 0
        if self.vista and self.vista.winfo_exists():
            self.vista.indice = i
            self.vista.dibujar()
            self.vista.lift()
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
                    self.dibujar_miniaturas()
                    ok = total - len(errores)
                    self.estado(f"Listo: {ok} PDF generados en {self.dir_salida()}")
                    if errores:
                        messagebox.showwarning(APP_TITULO, f"{ok} OK, {len(errores)} con error:\n\n" + "\n".join(errores[:20]))
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
                        excluir=self.var_excluir.get(), geometria=self.root.geometry())
        guardar_config(self.cfg)

    def salir(self) -> None:
        if self.trabajando and not messagebox.askyesno(APP_TITULO, "Se están generando PDFs. ¿Salir igual?"):
            return
        self.guardar_preferencias()
        self.render.cerrar()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
