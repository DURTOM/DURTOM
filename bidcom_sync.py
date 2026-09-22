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
# Extracción (se ejecuta dentro de la página)
# --------------------------------------------------------------------------- #
# No depende de nombres de clases CSS (que Bidcom puede cambiar): busca el texto
# "COD. XXXX" de cada tarjeta, sube hasta el contenedor del producto y lee los
# precios ($) que contiene. El precio tachado es el normal; el vigente, el
# rebajado. Se descartan montos de cuotas, "sin impuestos" y "ahorrás".
EXTRACT_JS = r"""
(opts) => {
  const codRe = new RegExp(opts.codRegex);
  const priceRe = /\$\s*([\d.]+(?:,\d{1,2})?)/g;
  const skipRe = /cuota|impuesto|imp\.|ahorr|env[ií]o|desde|x mes|mensual/i;

  const parsePrice = (s) => {
    const n = parseFloat(s.replace(/\./g, '').replace(',', '.'));
    return isFinite(n) ? n : null;
  };
  const isStruck = (el) => {
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      if (['DEL', 'S', 'STRIKE'].includes(e.tagName)) return true;
      const td = getComputedStyle(e).textDecorationLine || '';
      if (td.includes('line-through')) return true;
      const cls = (e.className && e.className.baseVal !== undefined) ? e.className.baseVal : (e.className || '');
      if (/(old|list|before|regular|tachad|strike|previous|original)/i.test(cls)) return true;
      if (e.dataset && e.dataset.card) break;
    }
    return false;
  };

  // 1) nodos de texto con "COD."
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const codNodes = [];
  let n;
  while ((n = walker.nextNode())) {
    const tag = n.parentElement && n.parentElement.tagName;
    if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE'].includes(tag)) continue;
    const m = n.textContent.match(codRe);
    if (m) codNodes.push({ node: n, sku: m[1].trim() });
  }

  const countCods = (el) => {
    const m = (el.innerText || '').match(new RegExp(opts.codRegex, 'g'));
    return m ? m.length : 0;
  };

  const out = [];
  const seen = new Set();
  for (const { node, sku } of codNodes) {
    // 2) subir hasta el contenedor que tenga precios pero un solo COD.
    let card = node.parentElement;
    while (card && card.parentElement) {
      const txt = card.innerText || '';
      if (/\$\s*\d/.test(txt)) break;
      if (countCods(card.parentElement) > 1) break;
      card = card.parentElement;
    }
    if (!card || seen.has(card)) continue;
    // asegurar que incluya ambos precios si están en un hermano cercano
    while (card.parentElement && countCods(card.parentElement) === 1 &&
           ((card.innerText || '').match(/\$\s*\d/g) || []).length < 2) {
      card = card.parentElement;
    }
    seen.add(card);
    card.dataset.card = '1';

    // 3) precios: cada elemento "hoja" que contenga $
    const prices = [];
    const els = card.querySelectorAll('*');
    for (const el of els) {
      const own = Array.from(el.childNodes)
        .filter(c => c.nodeType === 3).map(c => c.textContent).join(' ');
      const text = own.includes('$') ? own : (el.children.length === 0 ? el.textContent : '');
      if (!text || !text.includes('$')) continue;
      // contexto corto: "12 cuotas de $X", "Precio sin impuestos $Y"
      const par = el.parentElement;
      const ptxt = (par && par !== card) ? (par.textContent || '').trim() : '';
      const context = ptxt.length <= 80 ? ptxt : text;
      if (skipRe.test(text) || skipRe.test(context)) continue;
      let m;
      priceRe.lastIndex = 0;
      while ((m = priceRe.exec(text))) {
        const v = parsePrice(m[1]);
        if (v && v >= opts.minPrice) prices.push({ v, struck: isStruck(el) });
      }
    }
    delete card.dataset.card;

    const struck = prices.filter(p => p.struck).map(p => p.v);
    const live = prices.filter(p => !p.struck).map(p => p.v);
    let normal = null, sale = null;
    if (struck.length && live.length) {
      normal = Math.max(...struck);
      sale = Math.max(...live.filter(v => v < normal).concat([0])) || null;
    } else {
      const all = [...new Set(prices.map(p => p.v))].sort((a, b) => b - a);
      const hasOff = /%\s*off|% de desc|descuento/i.test(card.innerText || '');
      if (all.length >= 2 && hasOff) { normal = all[0]; sale = all[1]; }
      else if (all.length >= 1) { normal = all[0]; }
    }

    // nombre: primer texto largo que no sea COD ni precio
    let name = '';
    const title = card.querySelector('h1,h2,h3,h4,[class*="name" i],[class*="title" i]');
    if (title) name = title.innerText.trim();
    if (!name) {
      name = (card.innerText || '').split('\n').map(s => s.trim())
        .find(s => s.length > 8 && !codRe.test(s) && !s.includes('$')) || '';
    }
    const a = card.querySelector('a[href]');
    out.push({ sku, name, normal, sale, url: a ? a.href : '' });
  }
  return out;
}
"""

LOAD_MORE_JS = r"""
() => {
  const re = /^(ver|cargar|mostrar)\s+m[aá]s/i;
  const btn = Array.from(document.querySelectorAll('button, a'))
    .find(b => re.test((b.innerText || '').trim()) && b.offsetParent !== null);
  if (btn) { btn.click(); return true; }
  return false;
}
"""

NEXT_PAGE_JS = r"""
() => {
  const rel = document.querySelector('a[rel="next"]');
  if (rel && rel.href) return rel.href;
  const cand = Array.from(document.querySelectorAll('a[href]')).find(a => {
    const t = (a.innerText || a.getAttribute('aria-label') || '').trim().toLowerCase();
    return (t === 'siguiente' || t === '>' || t === '›' || t === '»' || t === 'next' ||
            t.startsWith('siguiente')) && !a.closest('[aria-disabled="true"], .disabled');
  });
  return cand ? cand.href : null;
}
"""


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
