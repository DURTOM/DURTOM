// Extracción de productos de bidcom.com.ar.
// Compartido por la extensión de Chrome y por bidcom_sync.py.
//
// No depende de nombres de clases CSS (que Bidcom puede cambiar): busca el texto
// "COD. XXXX" de cada tarjeta, sube hasta el contenedor del producto y lee los
// precios ($) que contiene. El precio tachado es el normal; el vigente, el
// rebajado. Se descartan montos de cuotas, "sin impuestos" y "ahorrás".

var BIDCOM_COD_REGEX = '\\bCOD[.:]\\s*([A-Z0-9][A-Z0-9\\-_/]{2,})';

function bidcomExtract(opts) {
  opts = Object.assign({ codRegex: BIDCOM_COD_REGEX, minPrice: 100 }, opts || {});
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
    // (se descartan contadores de ofertas tipo "Finaliza en: 00:58:01")
    const noEsNombre = (s) => !s || s.length < 8 || codRe.test(s) || s.includes('$') ||
      /finaliza|termina|\d{1,2}:\d{2}|%\s*off|cuota|env[ií]o/i.test(s);
    let name = '';
    for (const el of card.querySelectorAll('h1,h2,h3,h4,[class*="name" i],[class*="title" i],[class*="nombre" i]')) {
      const t = (el.innerText || '').trim();
      if (!noEsNombre(t)) { name = t; break; }
    }
    if (!name) {
      const img = card.querySelector('img[alt]');
      name = (card.innerText || '').split('\n').map(s => s.trim()).find(s => !noEsNombre(s)) ||
             (img ? img.alt.trim() : '');
    }
    const a = card.querySelector('a[href]');
    out.push({ sku, name, normal, sale, url: a ? a.href : '' });
  }
  return out;
}

function bidcomLoadMore() {
  const re = /^(ver|cargar|mostrar)\s+m[aá]s/i;
  const btn = Array.from(document.querySelectorAll('button, a'))
    .find(b => re.test((b.innerText || '').trim()) && b.offsetParent !== null);
  if (btn) { btn.click(); return true; }
  return false;
}

function bidcomNextPage() {
  const rel = document.querySelector('a[rel="next"]');
  if (rel && rel.href) return rel.href;
  const cand = Array.from(document.querySelectorAll('a[href]')).find(a => {
    const t = (a.innerText || a.getAttribute('aria-label') || '').trim().toLowerCase();
    return (t === 'siguiente' || t === '>' || t === '›' || t === '»' || t === 'next' ||
            t.startsWith('siguiente')) && !a.closest('[aria-disabled="true"], .disabled');
  });
  return cand ? cand.href : null;
}

// Espera a que aparezcan productos y hace scroll / "ver más" hasta que dejen de
// aparecer nuevos. Devuelve la cantidad de códigos visibles.
async function bidcomLoadAll(waitMs) {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const re = new RegExp(BIDCOM_COD_REGEX, 'g');
  const count = () => ((document.body.innerText || '').match(re) || []).length;
  for (let i = 0; i < 40 && count() === 0; i++) await sleep(500);
  let last = -1;
  for (let i = 0; i < 200; i++) {
    window.scrollTo(0, document.body.scrollHeight);
    await sleep(waitMs);
    const clicked = bidcomLoadMore();
    if (clicked) await sleep(waitMs);
    const c = count();
    if (c === last && !clicked) break;
    last = c;
  }
  return count();
}
