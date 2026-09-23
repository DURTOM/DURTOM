// Pantalla principal de la extensión: recorre las categorías, junta los
// productos, genera el CSV y (opcional) actualiza WooCommerce por API.
const $ = (id) => document.getElementById(id);
const ESPERA_MS = 1500;
let productos = [];      // todo lo extraído: [{sku, name, normal, sale, url, categoria}]
let fechaExtraccion = '';

// Prefijos de SKU a descartar (ej. "REF, USA"). Se aplica siempre al mostrar,
// descargar y actualizar, así un cambio en el casillero vale al instante.
const prefijos = () => $('excluir').value.split(/[,;\s]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
const excluido = (p) => prefijos().some((x) => p.sku.toUpperCase().startsWith(x));

// Filtro de marcas: si hay marcas cargadas, solo quedan los productos cuyo nombre
// (o dirección) las menciona como palabra completa. Sin tildes ni mayúsculas.
const normalizar = (s) => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
const listaMarcas = (id) => $(id).value.split(/[,;\n]+/).map((s) => normalizar(s).trim()).filter(Boolean);
const esc_re = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
function mencionaAlguna(p, lista) {
  const texto = ' ' + normalizar(p.name + ' ' + (p.url || '').replace(/[\/_-]+/g, ' ')) + ' ';
  return lista.some((m) => new RegExp('[^a-z0-9]' + esc_re(m).replace(/\s+/g, '[^a-z0-9]+') + '[^a-z0-9]').test(texto));
}
function deMarca(p) {
  const solo = listaMarcas('marcas');
  return !solo.length || mencionaAlguna(p, solo);
}
// Ofertas relámpago ("Sólo por hoy"): su precio vence en el día, por defecto no se actualizan.
const saltearRelampago = (p) => p.relampago && $('sinRelampago').checked;
const validos = () => productos.filter((p) => !excluido(p) && deMarca(p) && !saltearRelampago(p));
let faltantes = [];      // productos de Bidcom cuyo SKU no existe en la tienda
let cambiosWoo = null;   // {simples: [...], variaciones: {pid: [...]}, filas: [...]}

// ---------- utilidades ----------
function estado(msg, clase) {
  const box = $('estado');
  box.style.display = 'block';
  const linea = document.createElement('div');
  if (clase) linea.className = clase;
  linea.textContent = msg;
  box.appendChild(linea);
  box.scrollTop = box.scrollHeight;
}
const fmt = (v) => (v == null || v === '' ? '' : (Number.isInteger(v) ? String(v) : Number(v).toFixed(2)));
const pesos = (v) => (v == null || v === '' ? '—' : '$' + Number(v).toLocaleString('es-AR'));
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function guardar() {
  chrome.storage.local.set({
    categorias: $('categorias').value,
    excluir: $('excluir').value,
    marcas: $('marcas').value,
    sinRelampago: $('sinRelampago').checked,
    wooUrl: $('wooUrl').value.trim(), wooCk: $('wooCk').value.trim(), wooCs: $('wooCs').value.trim(),
    wooBorrar: $('wooBorrar').checked,
  });
}

async function cargar() {
  const d = await chrome.storage.local.get(null);
  $('categorias').value = d.categorias ?? 'https://www.bidcom.com.ar/drones';
  $('excluir').value = d.excluir ?? 'REF, USA';
  $('marcas').value = d.marcas ?? '';
  $('sinRelampago').checked = d.sinRelampago ?? true;
  $('wooUrl').value = d.wooUrl ?? '';
  $('wooCk').value = d.wooCk ?? '';
  $('wooCs').value = d.wooCs ?? '';
  $('wooBorrar').checked = d.wooBorrar ?? true;
  if (d.wooUrl) $('detWoo').open = true;
  if (d.ultimos && d.ultimos.length) { productos = d.ultimos; fechaExtraccion = d.ultimosFecha; mostrarResultados(); }
}

// ---------- extracción ----------
// Espera a que la pestaña termine de cargar una página web (no la pestaña en
// blanco inicial). Falla si la pestaña se cierra, para poder reintentar.
function esperarCarga(tabId) {
  return new Promise((resolve, reject) => {
    const fin = setTimeout(() => listo(), 60000);
    function listo(error) {
      clearTimeout(fin);
      chrome.tabs.onUpdated.removeListener(escucha);
      chrome.tabs.onRemoved.removeListener(cerrada);
      error ? reject(error) : resolve();
    }
    function escucha(id, info, tab) {
      if (id === tabId && info.status === 'complete' && /^https?:/i.test(tab.url || '')) listo();
    }
    function cerrada(id) { if (id === tabId) listo(new Error('se cerró la pestaña')); }
    chrome.tabs.onUpdated.addListener(escucha);
    chrome.tabs.onRemoved.addListener(cerrada);
  });
}

async function enPestana(tabId, func, args = []) {
  const [r] = await chrome.scripting.executeScript({ target: { tabId }, func, args });
  return r ? r.result : undefined;
}

async function extraerCategoria(tabId, url) {
  const encontrados = {};
  const visitadas = new Set();
  let actual = url;
  for (let pag = 0; pag < 100 && actual && !visitadas.has(actual); pag++) {
    visitadas.add(actual);
    const cargada = esperarCarga(tabId);
    cargada.catch(() => {});  // si falla la navegación, el error sale abajo
    await chrome.tabs.update(tabId, { url: actual });
    await cargada;
    await chrome.scripting.executeScript({ target: { tabId }, files: ['extractor.js'] });
    const visibles = await enPestana(tabId, (ms) => bidcomLoadAll(ms), [ESPERA_MS]);
    if (!visibles && pag === 0) {
      // categoría principal sin productos: devolver sus subcategorías para recorrerlas
      const subs = (await enPestana(tabId, () => bidcomSubcategorias())) || [];
      if (subs.length) return { productos: [], subs };
    }
    if (!visibles) estado(`   ⚠ No se encontraron productos (códigos "COD.") en ${actual}`, 'err');
    const lista = await enPestana(tabId, () => bidcomExtract()) || [];
    for (const p of lista) {
      if (!encontrados[p.sku] || encontrados[p.sku].normal == null) encontrados[p.sku] = { ...p, categoria: url };
    }
    estado(`   Página ${pag + 1}: ${Object.keys(encontrados).length} productos`);
    actual = await enPestana(tabId, () => bidcomNextPage());
  }
  return { productos: Object.values(encontrados), subs: [] };
}

async function extraerTodo() {
  guardar();
  const urls = $('categorias').value.split('\n').map((s) => s.trim()).filter((s) => /^https?:\/\//i.test(s));
  if (!urls.length) { alert('Pegá al menos una dirección de categoría de Bidcom.'); return; }
  $('btnExtraer').disabled = true;
  $('estado').textContent = '';
  const miPestana = await chrome.tabs.getCurrent();
  // Pestaña de trabajo: si se cierra en el medio (a mano, por Chrome o por la
  // página), se abre otra y se reintenta la categoría una vez.
  let tab = null;
  const pestanaViva = async () => {
    if (tab) { try { return await chrome.tabs.get(tab.id); } catch (e) { /* se cerró */ } }
    tab = await chrome.tabs.create({ url: 'about:blank', active: true });
    return tab;
  };
  const todos = {};
  // cola de categorías: una categoría principal sin productos agrega sus subcategorías
  const cola = urls.map((url) => ({ url, nivel: 0 }));
  const enCola = new Set(urls.map((u) => u.replace(/\/+$/, '').replace('://www.', '://')));
  try {
    while (cola.length) {
      const { url, nivel } = cola.shift();
      estado(`${nivel ? '   ↳ ' : ''}Abriendo ${url} …`);
      for (let intento = 1; intento <= 2; intento++) {
        try {
          const t = await pestanaViva();
          const r = await extraerCategoria(t.id, url);
          for (const p of r.productos) {
            if (!todos[p.sku] || todos[p.sku].normal == null) todos[p.sku] = p;
          }
          const nuevas = nivel < 2 ? r.subs.filter((u) => !enCola.has(u.replace('://www.', '://'))) : [];
          if (r.subs.length) estado(`   Categoría principal: ${nuevas.length} subcategorías para recorrer.`);
          nuevas.forEach((u) => enCola.add(u.replace('://www.', '://')));
          cola.unshift(...nuevas.map((u) => ({ url: u, nivel: nivel + 1 })));
          break;
        } catch (e) {
          if (intento === 1) { estado(`   ↻ Reintentando ${url} (${e.message})`); continue; }
          estado(`   ✖ Error en ${url}: ${e.message}`, 'err');
        }
      }
    }
  } finally {
    if (tab) { try { await chrome.tabs.remove(tab.id); } catch (e) { /* ya cerrada */ } }
    if (miPestana) await chrome.tabs.update(miPestana.id, { active: true });
    $('btnExtraer').disabled = false;
  }
  productos = Object.values(todos);
  const ok = validos();
  const sinPrecio = ok.filter((p) => p.normal == null);
  estado(`✔ Listo: ${ok.length} productos.` + (sinPrecio.length ? ` (${sinPrecio.length} sin precio)` : ''), 'ok');
  fechaExtraccion = new Date().toLocaleString('es-AR');
  chrome.storage.local.set({ ultimos: productos, ultimosFecha: fechaExtraccion });
  mostrarResultados();
}

function mostrarResultados() {
  const ok = validos();
  const porSku = productos.filter(excluido).length;
  const porMarca = productos.filter((p) => !excluido(p) && !deMarca(p)).length;
  const porRelampago = productos.filter((p) => !excluido(p) && deMarca(p) && saltearRelampago(p));
  cambiosWoo = null; $('btnAplicar').disabled = true; $('wooTablaBox').hidden = true; $('wooResumen').textContent = '';
  faltantes = []; mostrarFaltantes();
  $('secResultados').hidden = !productos.length;
  const conRebaja = ok.filter((p) => p.sale).length;
  $('resumen').textContent = `${ok.length} productos (${conRebaja} con precio rebajado)` +
    (porSku ? ` · ${porSku} descartados por SKU (${prefijos().join(', ')})` : '') +
    (porMarca ? ` · ${porMarca} de otras marcas` : '') +
    (porRelampago.length ? ` · ${porRelampago.length} en oferta "Sólo por hoy" (no se actualizan: ${porRelampago.map((p) => p.sku).join(', ')})` : '') +
    ` — ${fechaExtraccion || ''}`;
  $('tabla').innerHTML = '<tr><th>SKU</th><th>Producto</th><th>Precio normal</th><th>Precio rebajado</th></tr>' +
    ok.map((p) => `<tr><td>${esc(p.sku)}</td><td>${esc(p.name).slice(0, 60)}</td>` +
      `<td class="num">${pesos(p.normal)}</td><td class="num">${pesos(p.sale)}</td></tr>`).join('');
}

// sep ',' para el importador de WooCommerce; ';' para que Excel (config. Argentina) abra columnas.
function bajarCsv(nombre, filas, sep = ',') {
  const q = (s) => (/[",;\n]/.test(s) ? `"${String(s).replace(/"/g, '""')}"` : s);
  const csv = '\ufeff' + filas.map((f) => f.map(q).join(sep)).join('\r\n') + '\r\n';
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
  a.download = `${nombre}_${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

function descargarCsv() {
  bajarCsv('bidcom_precios', [['SKU', 'Precio normal', 'Precio rebajado'],
    ...validos().filter((p) => p.normal != null).map((p) => [p.sku, fmt(p.normal), fmt(p.sale)])]);
}

function descargarFaltantes() {
  bajarCsv('bidcom_no_estan_en_mi_tienda', [['SKU', 'Nombre', 'URL'],
    ...faltantes.map((p) => [p.sku, p.name || '', p.url || ''])], ';');
}

function mostrarFaltantes() {
  $('secFaltantes').hidden = !faltantes.length;
  $('faltResumen').textContent = `${faltantes.length} productos de Bidcom no están en tu tienda (con tus filtros de SKU y marcas).`;
  $('faltTabla').innerHTML = '<tr><th>SKU</th><th>Nombre</th><th>URL</th></tr>' +
    faltantes.map((p) => `<tr><td>${esc(p.sku)}</td><td>${esc(p.name).slice(0, 70)}</td>` +
      `<td>${p.url ? `<a href="${esc(p.url)}" target="_blank">${esc(p.url)}</a>` : ''}</td></tr>`).join('');
}

// ---------- WooCommerce ----------
function wooConfig() {
  let url = $('wooUrl').value.trim().replace(/\/+$/, '');
  if (url && !/^https?:\/\//i.test(url)) url = 'https://' + url;
  const ck = $('wooCk').value.trim(), cs = $('wooCs').value.trim();
  if (!url || !ck || !cs) throw new Error('Completá la dirección de la tienda y las dos claves.');
  return { url, base: url + '/wp-json/wc/v3', auth: 'Basic ' + btoa(ck + ':' + cs) };
}

async function wooFetch(cfg, path, opts = {}) {
  const r = await fetch(cfg.base + path, {
    ...opts, headers: { Authorization: cfg.auth, 'Content-Type': 'application/json', ...(opts.headers || {}) },
  });
  if (!r.ok) {
    let msg = r.status + ' ' + r.statusText;
    try { msg = (await r.json()).message || msg; } catch (e) { /* sin cuerpo */ }
    if (r.status === 401 || r.status === 403) msg += ' (revisá las claves y que tengan permiso de Lectura/Escritura)';
    throw new Error(msg);
  }
  return { data: await r.json(), paginas: parseInt(r.headers.get('X-WP-TotalPages') || '1', 10) };
}

async function wooMapaSku(cfg) {
  const mapa = {}, variables = [];
  for (let pag = 1; ; pag++) {
    const { data, paginas } = await wooFetch(cfg, `/products?per_page=100&page=${pag}&status=any&_fields=id,sku,name,type,regular_price,sale_price`);
    for (const p of data) {
      if (p.sku) mapa[p.sku.trim().toUpperCase()] = { ...p, parent: null };
      if (p.type === 'variable') variables.push(p.id);
    }
    $('wooResumen').textContent = `Leyendo tu tienda… ${Object.keys(mapa).length} SKUs`;
    if (pag >= paginas) break;
  }
  for (const pid of variables) {
    for (let pag = 1; ; pag++) {
      const { data, paginas } = await wooFetch(cfg, `/products/${pid}/variations?per_page=100&page=${pag}&_fields=id,sku,regular_price,sale_price`);
      for (const v of data) if (v.sku) mapa[v.sku.trim().toUpperCase()] = { ...v, parent: pid };
      if (pag >= paginas) break;
    }
  }
  return mapa;
}

function pedirPermiso() {
  // Debe llamarse directo desde el clic (Chrome lo exige).
  let cfg;
  try { cfg = wooConfig(); } catch (e) { alert(e.message); return null; }
  return chrome.permissions.request({ origins: [new URL(cfg.url).origin + '/*'] }).then((ok) => (ok ? cfg : null));
}

async function verCambios() {
  guardar();
  if (!validos().length) { alert('Primero extraé los precios (paso 1).'); return; }
  const cfg = await pedirPermiso();
  if (!cfg) return;
  $('btnVer').disabled = true; $('btnAplicar').disabled = true;
  try {
    const mapa = await wooMapaSku(cfg);
    const borrar = $('wooBorrar').checked;
    const simples = [], variaciones = {}, filas = [], nuevos = [];
    let coinciden = 0;
    // la lista de faltantes incluye las ofertas relámpago (solo importa el SKU)
    const lista = productos.filter((p) => !excluido(p) && deMarca(p));
    for (const p of lista) {
      const cur = mapa[p.sku.toUpperCase()];
      if (!cur) { nuevos.push(p); continue; }
      if (p.normal == null || saltearRelampago(p)) continue;
      coinciden++;
      const reg = fmt(p.normal);
      const sale = p.sale ? fmt(p.sale) : (borrar ? '' : (cur.sale_price || ''));
      if ((cur.regular_price || '') === reg && (cur.sale_price || '') === sale) continue;
      const upd = { id: cur.id, regular_price: reg, sale_price: sale };
      if (cur.parent) (variaciones[cur.parent] ||= []).push(upd); else simples.push(upd);
      filas.push([p.sku, cur.regular_price, reg, cur.sale_price, sale]);
    }
    cambiosWoo = { cfg, simples, variaciones, filas };
    faltantes = nuevos;
    mostrarFaltantes();
    $('wooResumen').textContent = `${coinciden} SKUs de Bidcom están en tu tienda · ${filas.length} con precio distinto · ` +
      `${nuevos.length} no están en tu tienda (ver lista abajo).`;
    $('wooTabla').innerHTML = '<tr><th>SKU</th><th>Normal actual</th><th>Normal nuevo</th><th>Rebajado actual</th><th>Rebajado nuevo</th></tr>' +
      filas.map((f) => `<tr><td>${esc(f[0])}</td><td class="num">${pesos(f[1])}</td><td class="num"><b>${pesos(f[2])}</b></td>` +
        `<td class="num">${pesos(f[3])}</td><td class="num"><b>${pesos(f[4])}</b></td></tr>`).join('');
    $('wooTablaBox').hidden = !filas.length;
    $('btnAplicar').disabled = !filas.length;
  } catch (e) {
    $('wooResumen').innerHTML = `<span class="err">✖ No se pudo conectar con la tienda: ${esc(e.message)}</span>`;
  } finally {
    $('btnVer').disabled = false;
  }
}

async function aplicarCambios() {
  if (!cambiosWoo) return;
  const { cfg, simples, variaciones, filas } = cambiosWoo;
  if (!confirm(`Se van a actualizar ${filas.length} productos en tu tienda. ¿Continuar?`)) return;
  $('btnAplicar').disabled = true; $('btnVer').disabled = true;
  const lotes = [];
  for (let i = 0; i < simples.length; i += 100) lotes.push(['/products/batch', simples.slice(i, i + 100)]);
  for (const [pid, ups] of Object.entries(variaciones)) {
    for (let i = 0; i < ups.length; i += 100) lotes.push([`/products/${pid}/variations/batch`, ups.slice(i, i + 100)]);
  }
  let errores = 0, hechos = 0;
  try {
    for (const [path, update] of lotes) {
      const { data } = await wooFetch(cfg, path, { method: 'POST', body: JSON.stringify({ update }) });
      for (const it of data.update || []) (it.error ? errores++ : hechos++);
      $('wooResumen').textContent = `Actualizando… ${hechos} de ${filas.length}`;
    }
    $('wooResumen').innerHTML = `<span class="ok">✔ Tienda actualizada: ${hechos} productos.</span>` +
      (errores ? ` <span class="err">${errores} con error.</span>` : '');
    cambiosWoo = null;
  } catch (e) {
    $('wooResumen').innerHTML = `<span class="err">✖ Error al actualizar (${hechos} ya aplicados): ${esc(e.message)}</span>`;
  } finally {
    $('btnVer').disabled = false;
  }
}

// ---------- eventos ----------
$('btnExtraer').addEventListener('click', extraerTodo);
$('btnCsv').addEventListener('click', descargarCsv);
$('btnVer').addEventListener('click', verCambios);
$('btnAplicar').addEventListener('click', aplicarCambios);
$('btnFaltantes').addEventListener('click', descargarFaltantes);
$('sinRelampago').addEventListener('change', () => { guardar(); mostrarResultados(); });
for (const id of ['excluir', 'marcas']) $(id).addEventListener('input', () => { guardar(); mostrarResultados(); });
for (const id of ['categorias', 'wooUrl', 'wooCk', 'wooCs', 'wooBorrar']) $(id).addEventListener('change', guardar);
$('version').textContent = 'versión ' + chrome.runtime.getManifest().version;
cargar();
