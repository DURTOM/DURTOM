# Bidcom → WooCommerce: actualizador de precios por SKU

Automatiza lo que hacías a mano con la extensión **Web Scraper**:

1. Abre cada categoría de bidcom.com.ar que configures (por ejemplo `https://www.bidcom.com.ar/drones`).
2. Carga **todos** los productos (hace scroll, pulsa "Ver más" y sigue la paginación).
3. De cada producto toma **SKU** (el `COD. DRDJI077` de la tarjeta), **precio normal** (tachado) y **precio rebajado**.
4. Genera un **CSV** listo para importar en WooCommerce (`SKU, Precio normal, Precio rebajado`).
5. De forma opcional, **actualiza la tienda directamente** por la API de WooCommerce, **solo en los SKU que ya existen**. Los que no están en tu tienda se ignoran.

No depende de las clases CSS de Bidcom: busca el texto `COD.` y los precios `$` de cada tarjeta. Ignora los montos de cuotas, el "precio sin impuestos nacionales" y el "ahorrás".

## Instalación (Windows)

1. Instalá Python 3.10 o más nuevo desde https://www.python.org y marcá **"Add Python to PATH"** durante la instalación.
2. Descargá esta carpeta y hacé doble clic en **`instalar.bat`**.
3. Abrí `config.ini` con el Bloc de notas y poné las categorías, una por línea:

```ini
[bidcom]
categorias =
    https://www.bidcom.com.ar/drones
    https://www.bidcom.com.ar/otra-categoria
```

## Uso

| Archivo | Qué hace |
|---|---|
| `ejecutar.bat` | Extrae los precios, genera el CSV y, si está activado, actualiza WooCommerce |
| `simular.bat` | Muestra qué precios cambiarían en la tienda, sin modificar nada |
| `programar_tarea_diaria.bat` | Programa una ejecución automática todos los días a las 08:00 |

Los resultados se guardan en la carpeta `salida/`:

- `woocommerce_precios_AAAAMMDD_HHMM.csv` es el archivo para importar.
- `bidcom_detalle_...csv` trae nombre, URL y categoría para que puedas controlar (se abre en Excel).
- `registro.log` guarda el historial de ejecuciones y los cambios aplicados.

Por línea de comandos:

```
python bidcom_sync.py                      # proceso completo
python bidcom_sync.py --solo-csv           # solo genera el CSV
python bidcom_sync.py --simular            # muestra los cambios sin aplicarlos
python bidcom_sync.py --ver                # muestra el navegador mientras trabaja
python bidcom_sync.py --url https://www.bidcom.com.ar/drones   # una categoría puntual
```

## Opción A: importar el CSV a mano (igual que ahora)

En WordPress, andá a **Productos → Importar**, elegí el CSV y marcá **"Actualizar productos existentes"**. Las columnas se asignan solas a *SKU*, *Precio normal* y *Precio rebajado*. Si un SKU no existe en tu tienda, WooCommerce lo omite.

## Opción B: actualización automática (sin importar nada)

1. En WordPress, andá a **WooCommerce → Ajustes → Avanzado → API REST → Añadir clave**, con permisos de **Lectura/Escritura**.
2. En `config.ini`, completá lo siguiente:

```ini
[woocommerce]
activar = true
url = https://tu-tienda.com.ar
consumer_key = ck_...
consumer_secret = cs_...
```

3. Corré primero `simular.bat` para revisar los cambios y después `ejecutar.bat`.

El sistema solo modifica los productos (y variaciones) cuyo SKU coincide y cuyo precio cambió. Si en Bidcom un producto no tiene descuento, se borra el precio rebajado en tu tienda. Esto se puede desactivar con `borrar_rebaja_si_no_hay = false`.

## Si algo no sale bien

- **Faltan productos**: subí `espera_ms` en `config.ini` (por ejemplo a 3000) para darle más tiempo a una conexión lenta.
- **"sin precio detectado"**: corré con `--ver` para mirar la página. Si hay un error, queda una captura en `salida/error_*.png`.
- **El código tiene otro formato**: ajustá `regex_sku` en `config.ini`.

## Pruebas

```
python -m unittest discover -s tests
```
