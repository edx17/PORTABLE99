# Unificador de PDF (portable, Windows 10/11)

Recorre una carpeta base con esta estructura y genera **un PDF final por operación**:

```
BASE\
  ARDION\                      <- cliente
    183512 - 1064679\          <- operación
      260925-01243-ARDION S.A.-F-A....pdf   (factura del cliente)
      260925-01243-ARDION S.A.-R-A....pdf   (remito)
      183512-NAVE.pdf                        (factura de terceros)
      183512-TEFASA.pdf
  SACDE\ ...
  copiar a contable\           <- se ignora
```

Resultado: `BASE\_UNIFICADOS\ARDION\183512 - 1064679.pdf` (se puede elegir otra carpeta de salida).

## Uso

1. Abrir `UnificadorPDF.exe` (no requiere instalación).
2. **Carpeta base** → Examinar… → se listan clientes y operaciones con su estado:
   - **Pendiente**: todavía no se unificó.
   - **Cambió**: se agregó o modificó algún PDF después de unificar.
   - **Unificado**: ya está al día.
3. Clic en una operación para ver las hojas en miniatura:
   - **Arrastrar** para reordenar (o ◀ Mover / Mover ▶, o flechas del teclado).
   - **↻ Rotar**, **Quitar hoja** (tecla Supr), **Restaurar orden**.
   - **Doble clic** / barra espaciadora → vista previa grande (←/→ para navegar).
4. **Unificar pendientes** genera todo lo que falta; **Unificar seleccionadas** rehace las marcadas
   (se puede seleccionar un cliente entero).

### Orden automático

1. Facturas del cliente (archivo con el nombre del cliente y `-F-`, `FACTURA`, `-NC-`, `-ND-`)
2. Remitos (`-R-` o `REMITO`)
3. Otros documentos del cliente
4. Facturas de terceros

Dentro de cada grupo, por nombre de archivo. Si el orden automático no sirve para una operación,
se reordena a mano en el visor antes de unificar.

La configuración (última carpeta, etc.) se guarda en `unificador_config.json` al lado del exe.

## Obtener el .exe

- **GitHub Actions**: cada push compila el exe en Windows. Descargarlo desde la pestaña
  *Actions* → última ejecución → artefacto `UnificadorPDF-windows`. Con un tag `v1.0` se publica
  además como Release.
- **Local en Windows**: instalar Python 3.10+ y ejecutar `build.bat` → `dist\UnificadorPDF.exe`.

## Desarrollo

```
pip install -r requirements.txt
python UnificadorPDF.py
```
