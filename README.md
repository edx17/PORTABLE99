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

Resultado: todos los PDF finales en `BASE\_Unificados\`, con el nombre
`<número de operación>-99-.pdf` → `183512-99-.pdf`. El nombre se puede cambiar
en el campo **Nombre del PDF** (`{operacion}` = primer número de la carpeta,
`{carpeta}` = nombre completo, `{cliente}` = cliente). Si dos operaciones dieran
el mismo nombre, se agrega el cliente: `183512-99- (SACDE).pdf`.

## Uso

1. Abrir `UnificadorPDF.exe` (no requiere instalación).
2. **Carpeta base** → Examinar… → se listan clientes y operaciones con su estado:
   - **Pendiente**: todavía no se unificó.
   - **Cambió**: se agregó o modificó algún PDF después de unificar.
   - **Unificado**: ya está al día.  (✎ = tiene orden manual)
   Si se agregan o borran archivos, al volver a la ventana (o con Escanear) se actualiza
   solo: lo nuevo se agrega al final y se conserva el orden manual del resto.
   Un PDF viejo con el nombre del unificado (ej. `183358-99-...pdf`) dentro de la carpeta
   se ignora para no duplicar hojas.
3. Clic en una operación. Los documentos arrancan en orden alfabético; se ordenan a mano:
   - **Documentos** (panel del medio): arrastrar o ▲/▼ mueve el PDF entero con todas sus
     hojas. Clic en un documento marca sus hojas.
   - **Hojas** (miniaturas): arrastrar para mover; Ctrl+clic / Shift+clic para elegir varias
     y moverlas juntas; ⏮ ⏭ al principio / final; ↺ ↻ rotar; Supr quita.
     Ctrl+rueda o el control **Tamaño** agranda las miniaturas.
   - **Vista previa** (doble clic, Enter o espacio): hoja en grande y nítida, con
     *Página entera*, *Ancho*, zoom −/+ (o Ctrl+rueda), arrastrar para desplazarse,
     ←/→ para pasar de hoja, rotar y quitar desde ahí mismo.
4. **Unificar pendientes** genera todo lo que falta (y lo que tenga orden manual);
   **Unificar seleccionadas** rehace las marcadas (se puede elegir un cliente entero).

La configuración (última carpeta, tamaño de miniaturas, etc.) se guarda en
`unificador_config.json` al lado del exe.

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
