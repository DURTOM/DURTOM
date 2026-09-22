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
const validos = () => productos.filter((p) => !excluido(p) && deMarca(p));
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
    wooUrl: $('wooUrl').value.trim(), wooCk: $('wooCk').value.trim(), wooCs: $('wooCs').value.trim(),
    wooBorrar: $('wooBorrar').checked,
  });
}

async function cargar() {
  const d = await chrome.storage.local.get(null);
  $('categorias').value = d.categorias ?? 'https://www.bidcom.com.ar/drones';
  $('excluir').value = d.excluir ?? 'REF, USA';
  $('marcas').value = d.marcas ?? '';
  $('wooUrl').value = d.wooUrl ?? '';
  $('wooCk').value = d.wooCk ?? '';
  $('wooCs').value = d.wooCs ?? '';
  $('wooBorrar').checked = d.wooBorrar ?? true;
  if (d.wooUrl) $('detWoo').open = true;
  if (d.ultimos && d.ultimos.length) { productos = d.ultimos; fechaExtraccion = d.ultimosFecha; mostrarResultados(); }
}

// ---------- extracción ----------
function esperarCarga(tabId) {
  return new Promise((resolve) => {
    const fin = setTimeout(listo, 60000);
    function listo() { clearTimeout(fin); chrome.tabs.onUpdated.removeListener(escucha); resolve(); }
    function escucha(id, info) { if (id === tabId && info.status === 'complete') listo(); }
    chrome.tabs.onUpdated.addListener(escucha);
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
    await chrome.tabs.update(tabId, { url: actual });
    await cargada;
    await chrome.scripting.executeScript({ target: { tabId }, files: ['extractor.js'] });
    const visibles = await enPestana(tabId, (ms) => bidcomLoadAll(ms), [ESPERA_MS]);
    if (!visibles) estado(`   ⚠ No se encontraron productos (códigos "COD.") en ${actual}`, 'err');
    const lista = await enPestana(tabId, () => bidcomExtract()) || [];
    for (const p of lista) {
      if (!encontrados[p.sku] || encontrados[p.sku].normal == null) encontrados[p.sku] = { ...p, categoria: url };
    }
    estado(`   Página ${pag + 1}: ${Object.keys(encontrados).length} productos`);
    actual = await enPestana(tabId, () => bidcomNextPage());
  }
  return Object.values(encontrados);
}

async function extraerTodo() {
  guardar();
  const urls = $('categorias').value.split('\n').map((s) => s.trim()).filter((s) => /^https?:\/\//i.test(s));
  if (!urls.length) { alert('Pegá al menos una dirección de categoría de Bidcom.'); return; }
  $('btnExtraer').disabled = true;
  $('estado').textContent = '';
  const miPestana = await chrome.tabs.getCurrent();
  const tab = await chrome.tabs.create({ url: 'about:blank', active: true });
  const todos = {};
  try {
    for (const url of urls) {
      estado(`Abriendo ${url} …`);
      try {
        for (const p of await extraerCategoria(tab.id, url)) {
          if (!todos[p.sku] || todos[p.sku].normal == null) todos[p.sku] = p;
        }
      } catch (e) {
        estado(`   ✖ Error en ${url}: ${e.message}`, 'err');
      }
    }
  } finally {
    try { await chrome.tabs.remove(tab.id); } catch (e) { /* ya cerrada */ }
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
  cambiosWoo = null; $('btnAplicar').disabled = true; $('wooTablaBox').hidden = true; $('wooResumen').textContent = '';
  faltantes = []; mostrarFaltantes();
  $('secResultados').hidden = !productos.length;
  const conRebaja = ok.filter((p) => p.sale).length;
  $('resumen').textContent = `${ok.length} productos (${conRebaja} con precio rebajado)` +
    (porSku ? ` · ${porSku} descartados por SKU (${prefijos().join(', ')})` : '') +
    (porMarca ? ` · ${porMarca} de otras marcas` : '') + ` — ${fechaExtraccion || ''}`;
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
    const lista = validos();
    for (const p of lista) {
      const cur = mapa[p.sku.toUpperCase()];
      if (!cur) { nuevos.push(p); continue; }
      if (p.normal == null) continue;
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
for (const id of ['excluir', 'marcas']) $(id).addEventListener('input', () => { guardar(); mostrarResultados(); });
for (const id of ['categorias', 'wooUrl', 'wooCk', 'wooCs', 'wooBorrar']) $(id).addEventListener('change', guardar);
cargar();
