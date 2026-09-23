#!/usr/bin/env python3
"""
Bidcom -> WooCommerce: sincronizador de precios por SKU.

Reemplaza el proceso manual con la extensión Web Scraper:
  1. Abre cada categoría de bidcom.com.ar configurada (con un navegador real).
  2. Recorre todas las páginas / scroll hasta cargar todos los productos.
  3. Extrae SKU (el "COD. XXXX"), precio normal y precio rebajado.
  4. Genera un CSV listo para el importador de WooCommerce.
  5. (Opcional) Actualiza directamente en WooCommerce vía API REST,
     SOLO los productos cuyo SKU exista en la tienda.

Uso:
  python bidcom_sync.py                 # usa config.ini
  python bidcom_sync.py --solo-csv      # no toca WooCommerce
  python bidcom_sync.py --simular       # muestra qué cambiaría, sin escribir
  python bidcom_sync.py --ver           # abre el navegador visible
"""
import argparse
import configparser
import csv
import datetime as dt
import logging
import os
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
log = logging.getLogger("bidcom_sync")

# --------------------------------------------------------------------------- #
# Extracción: la lógica vive en extension-chrome/extractor.js (compartida con
# la extensión de Chrome) y se ejecuta dentro de la página.
# --------------------------------------------------------------------------- #
_JS = (BASE_DIR / "extension-chrome" / "extractor.js").read_text(encoding="utf-8")
EXTRACT_JS = "(opts) => {\n" + _JS + "\nreturn bidcomExtract(opts);\n}"
LOAD_MORE_JS = "() => {\n" + _JS + "\nreturn bidcomLoadMore();\n}"
NEXT_PAGE_JS = "() => {\n" + _JS + "\nreturn bidcomNextPage();\n}"


def scrape_category(page, url, cfg):
    """Devuelve lista de dicts {sku, name, normal, sale, url} de una categoría."""
    opts = {
        "codRegex": cfg.get("scraper", "regex_sku", fallback=r"\bCOD[.:]\s*([A-Z0-9][A-Z0-9\-_/]{2,})"),
        "minPrice": cfg.getfloat("scraper", "precio_minimo", fallback=100),
    }
    max_pages = cfg.getint("scraper", "max_paginas", fallback=30)
    wait_ms = cfg.getint("scraper", "espera_ms", fallback=1500)

    products = {}
    visited = set()
    current = url
    for _ in range(max_pages):
        if current in visited:
            break
        visited.add(current)
        log.info("  Abriendo %s", current)
        page.goto(current, wait_until="domcontentloaded", timeout=90_000)
        try:
            page.wait_for_selector("text=/COD[.:]/", timeout=30_000)
        except Exception:
            log.warning("  No se encontraron códigos 'COD.' en %s", current)

        # scroll + "ver más" hasta que no aparezcan productos nuevos
        last = -1
        for _ in range(60):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(wait_ms)
            clicked = page.evaluate(LOAD_MORE_JS)
            if clicked:
                page.wait_for_timeout(wait_ms)
            count = page.locator("text=/COD[.:]/").count()
            if count == last and not clicked:
                break
            last = count

        for p in page.evaluate(EXTRACT_JS, opts):
            if p["sku"] not in products or products[p["sku"]]["normal"] is None:
                products[p["sku"]] = p
        log.info("  Productos acumulados: %d", len(products))

        nxt = page.evaluate(NEXT_PAGE_JS)
        if not nxt:
            break
        current = nxt
    return list(products.values())


def launch_browser(pw, headless):
    """Usa el Chrome/Edge instalado en la PC; si no hay, el Chromium de Playwright."""
    exe = os.environ.get("BIDCOM_CHROME")
    if exe:
        return pw.chromium.launch(headless=headless, executable_path=exe)
    for channel in ("chrome", "msedge"):
        try:
            return pw.chromium.launch(headless=headless, channel=channel)
        except Exception:
            pass
    return pw.chromium.launch(headless=headless)


def scrape_all(categories, cfg, headed=False):
    from playwright.sync_api import sync_playwright

    results = {}
    with sync_playwright() as pw:
        browser = launch_browser(pw, headless=not headed)
        ctx = browser.new_context(
            locale="es-AR",
            viewport={"width": 1400, "height": 1000},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"),
        )
        page = ctx.new_page()
        for url in categories:
            log.info("Categoría: %s", url)
            try:
                items = scrape_category(page, url, cfg)
            except Exception as e:  # una categoría rota no frena el resto
                log.error("  Error en %s: %s", url, e)
                shot = BASE_DIR / "salida" / f"error_{dt.datetime.now():%Y%m%d_%H%M%S}.png"
                shot.parent.mkdir(exist_ok=True)
                try:
                    page.screenshot(path=str(shot), full_page=True)
                    log.error("  Captura guardada en %s", shot)
                except Exception:
                    pass
                continue
            for it in items:
                it["categoria"] = url
                results.setdefault(it["sku"], it)
            log.info("  %d productos en esta categoría", len(items))
        browser.close()
    return list(results.values())


# --------------------------------------------------------------------------- #
# Salida CSV
# --------------------------------------------------------------------------- #
def fmt(v):
    if v is None:
        return ""
    return str(int(v)) if float(v).is_integer() else f"{v:.2f}"


def write_csvs(products, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M")
    # 1) CSV para el importador de WooCommerce (Productos > Importar,
    #    tildar "Actualizar productos existentes"). Solo actualiza SKUs existentes.
    woo = out_dir / f"woocommerce_precios_{stamp}.csv"
    with woo.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["SKU", "Precio normal", "Precio rebajado"])
        for p in products:
            w.writerow([p["sku"], fmt(p["normal"]), fmt(p["sale"])])
    # 2) CSV de detalle para control
    det = out_dir / f"bidcom_detalle_{stamp}.csv"
    with det.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["SKU", "Nombre", "Precio normal", "Precio rebajado", "URL", "Categoria"])
        for p in products:
            w.writerow([p["sku"], p["name"], fmt(p["normal"]), fmt(p["sale"]), p["url"], p["categoria"]])
    return woo, det


# --------------------------------------------------------------------------- #
# WooCommerce REST API
# --------------------------------------------------------------------------- #
class Woo:
    def __init__(self, url, key, secret, timeout=60):
        import requests
        self.s = requests.Session()
        self.s.auth = (key, secret)
        self.s.headers["User-Agent"] = "bidcom-sync/1.0"
        self.base = url.rstrip("/") + "/wp-json/wc/v3"
        self.timeout = timeout

    def _get(self, path, **params):
        r = self.s.get(self.base + path, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r

    def sku_map(self, include_variations=True):
        """SKU -> datos de producto/variación existentes en la tienda."""
        out = {}
        page = 1
        variables = []
        while True:
            r = self._get("/products", per_page=100, page=page, status="any",
                          _fields="id,sku,type,regular_price,sale_price")
            data = r.json()
            for p in data:
                if p.get("sku"):
                    out[p["sku"].strip().upper()] = {**p, "parent": None}
                if p.get("type") == "variable":
                    variables.append(p["id"])
            if page >= int(r.headers.get("X-WP-TotalPages", 1)):
                break
            page += 1
        if include_variations:
            for pid in variables:
                page = 1
                while True:
                    r = self._get(f"/products/{pid}/variations", per_page=100, page=page,
                                  _fields="id,sku,regular_price,sale_price")
                    for v in r.json():
                        if v.get("sku"):
                            out[v["sku"].strip().upper()] = {**v, "parent": pid}
                    if page >= int(r.headers.get("X-WP-TotalPages", 1)):
                        break
                    page += 1
        return out

    def batch(self, path, updates):
        for i in range(0, len(updates), 100):
            chunk = updates[i:i + 100]
            r = self.s.post(self.base + path, json={"update": chunk}, timeout=self.timeout * 2)
            r.raise_for_status()
            for item in r.json().get("update", []):
                if "error" in item:
                    log.error("  Error actualizando id %s: %s", item.get("id"), item["error"].get("message"))


def sync_woo(products, cfg, simulate=False):
    woo = Woo(cfg.get("woocommerce", "url"),
              cfg.get("woocommerce", "consumer_key"),
              cfg.get("woocommerce", "consumer_secret"))
    clear_sale = cfg.getboolean("woocommerce", "borrar_rebaja_si_no_hay", fallback=True)
    log.info("Leyendo productos de WooCommerce...")
    existing = woo.sku_map(cfg.getboolean("woocommerce", "incluir_variaciones", fallback=True))
    log.info("  %d SKUs en la tienda", len(existing))

    simple, variations = [], {}
    report = []
    not_found = []
    for p in products:
        cur = existing.get(p["sku"].upper())
        if not cur:
            not_found.append(p["sku"])
            continue
        if p["normal"] is None:
            continue
        new_reg = fmt(p["normal"])
        new_sale = fmt(p["sale"]) if p["sale"] else ("" if clear_sale else cur.get("sale_price", ""))
        if cur.get("regular_price", "") == new_reg and cur.get("sale_price", "") == new_sale:
            continue
        upd = {"id": cur["id"], "regular_price": new_reg, "sale_price": new_sale}
        report.append((p["sku"], cur.get("regular_price"), cur.get("sale_price"), new_reg, new_sale))
        if cur["parent"]:
            variations.setdefault(cur["parent"], []).append(upd)
        else:
            simple.append(upd)

    for sku, r0, s0, r1, s1 in report:
        log.info("  %-15s normal %s -> %s | rebajado %s -> %s", sku, r0 or "-", r1, s0 or "-", s1 or "-")
    log.info("Coinciden %d SKUs, %d con cambios, %d no existen en la tienda (ignorados).",
             len(products) - len(not_found), len(report), len(not_found))

    if simulate:
        log.info("Modo simulación: no se escribió nada en WooCommerce.")
        return report
    if simple:
        woo.batch("/products/batch", simple)
    for pid, ups in variations.items():
        woo.batch(f"/products/{pid}/variations/batch", ups)
    log.info("WooCommerce actualizado.")
    return report


# --------------------------------------------------------------------------- #
def load_config(path):
    cfg = configparser.ConfigParser()
    if not path.exists():
        sys.exit(f"No existe {path}. Copiá config.example.ini como config.ini y completalo.")
    cfg.read(path, encoding="utf-8")
    return cfg


def main():
    ap = argparse.ArgumentParser(description="Sincroniza precios de Bidcom a WooCommerce por SKU")
    ap.add_argument("--config", default=str(BASE_DIR / "config.ini"))
    ap.add_argument("--url", action="append", help="Categoría a procesar (reemplaza las del config)")
    ap.add_argument("--solo-csv", action="store_true", help="Solo genera el CSV, no toca WooCommerce")
    ap.add_argument("--simular", action="store_true", help="Muestra los cambios sin aplicarlos")
    ap.add_argument("--ver", action="store_true", help="Muestra el navegador mientras trabaja")
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    out_dir = BASE_DIR / cfg.get("general", "carpeta_salida", fallback="salida")
    out_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(out_dir / "registro.log", encoding="utf-8")])

    categories = args.url or [l.strip() for l in cfg.get("bidcom", "categorias").splitlines()
                              if l.strip() and not l.strip().startswith("#")]
    products = scrape_all(categories, cfg, headed=args.ver)
    prefijos = [x.strip().upper() for x in re.split(r"[,;\s]+", cfg.get("bidcom", "excluir_prefijos", fallback="REF, USA")) if x.strip()]
    descartados = [p for p in products if p["sku"].upper().startswith(tuple(prefijos))] if prefijos else []
    if descartados:
        log.info("Se descartan %d productos por SKU (%s).", len(descartados), ", ".join(prefijos))
        products = [p for p in products if p not in descartados]
    if cfg.getboolean("bidcom", "saltear_ofertas_relampago", fallback=True):
        relampago = [p for p in products if p.get("relampago")]
        if relampago:
            log.info("No se actualizan %d productos en oferta relámpago (Sólo por hoy): %s",
                     len(relampago), ", ".join(p["sku"] for p in relampago))
            products = [p for p in products if not p.get("relampago")]
    if not products:
        log.error("No se extrajo ningún producto. Probá con --ver para mirar qué pasa.")
        return 1
    sin_precio = [p["sku"] for p in products if p["normal"] is None]
    if sin_precio:
        log.warning("%d productos sin precio detectado: %s", len(sin_precio), ", ".join(sin_precio[:20]))

    woo_csv, det_csv = write_csvs(products, out_dir)
    log.info("Total: %d productos. CSV WooCommerce: %s", len(products), woo_csv)
    log.info("Detalle para control: %s", det_csv)

    enabled = cfg.getboolean("woocommerce", "activar", fallback=False)
    if enabled and not args.solo_csv:
        sync_woo([p for p in products if p["normal"] is not None], cfg, simulate=args.simular)
    elif not args.solo_csv:
        log.info("Actualización automática desactivada: importá el CSV en WooCommerce "
                 "(Productos > Importar > 'Actualizar productos existentes').")
    return 0


if __name__ == "__main__":
    sys.exit(main())
