"""Prueba la extensión de Chrome completa: extracción sobre el mock, CSV y
actualización contra la API WooCommerce simulada de test_woo."""
import functools
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_woo  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None


@unittest.skipUnless(sync_playwright and os.environ.get("BIDCOM_CHROME"), "requiere Playwright y BIDCOM_CHROME")
class TestExtension(unittest.TestCase):
    def test_flujo_completo(self):
        tmp = Path(tempfile.mkdtemp())
        ext = tmp / "ext"
        shutil.copytree(HERE.parent / "extension-chrome", ext)
        man = json.loads((ext / "manifest.json").read_text())
        man["host_permissions"].append("http://127.0.0.1/*")
        (ext / "manifest.json").write_text(json.dumps(man))

        quiet = type("Q", (SimpleHTTPRequestHandler,), {"log_message": lambda *a: None})
        mock = HTTPServer(("127.0.0.1", 0), functools.partial(quiet, directory=str(HERE / "mock")))
        woo = HTTPServer(("127.0.0.1", 0), test_woo.H)
        for s in (mock, woo):
            threading.Thread(target=s.serve_forever, daemon=True).start()
        test_woo.POSTS.clear()

        with sync_playwright() as pw:
            ctx = pw.chromium.launch_persistent_context(
                str(tmp / "perfil"), headless=True, executable_path=os.environ["BIDCOM_CHROME"],
                args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}", "--headless=new"])
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
            page = ctx.new_page()
            page.goto(f"chrome-extension://{sw.url.split('/')[2]}/app.html")
            # menú de categorías: las principales no tienen link, se eligen con una casilla
            base = f"http://127.0.0.1:{mock.server_port}"
            page.fill("#categorias", base + "/p1.html")
            page.click("#btnMenu")
            page.wait_for_selector("#menuBox:not([hidden])", timeout=30_000)
            grupos = page.inner_text("#menuGrupos")
            self.assertIn("Bebés y Niños", grupos)
            self.assertIn("Tecnología", grupos)
            self.assertNotIn("Contacto", grupos)
            page.click("#menuGrupos details:has-text('Bebés y Niños') input.grupo")
            page.click("#btnUsarMenu")
            self.assertEqual(page.input_value("#categorias").splitlines(),
                             [base + "/seguridad-bebes", base + "/juegos-y-juguetes", base + "/rodados"])
            page.fill("#categorias", base + "/p1.html")
            # la primera vez que se abre la pestaña de trabajo, la cerramos en el medio
            cerrada = []
            def cerrar(pg):
                if not cerrada:
                    cerrada.append(1)
                    pg.wait_for_load_state()
                    pg.wait_for_timeout(300)
                    pg.close()
            ctx.on("page", cerrar)
            page.click("#btnExtraer")
            page.wait_for_selector("text=Listo", timeout=90_000)
            self.assertIn("Reintentando", page.inner_text("#estado"))
            self.assertIn("Listo: 5 productos", page.inner_text("#estado"))
            self.assertNotIn("REF-", page.inner_text("#tabla"))
            self.assertIn("1 descartados", page.inner_text("#resumen"))
            page.fill("#excluir", "")  # sin filtro aparece al instante
            self.assertIn("REF-DRDJI073", page.inner_text("#tabla"))
            page.fill("#excluir", "REF, USA")
            # filtro de marcas (sin mayúsculas ni tildes): solo DJI -> el Gadnic queda afuera
            page.fill("#marcas", "dji")
            self.assertNotIn("GAD001", page.inner_text("#tabla"))
            self.assertIn("1 de otras marcas", page.inner_text("#resumen"))
            page.fill("#marcas", "DJI, Gádnic")
            self.assertIn("GAD001", page.inner_text("#tabla"))
            page.fill("#marcas", "")
            with page.expect_download() as dl:
                page.click("#btnCsv")
            csv_text = Path(dl.value.path()).read_text(encoding="utf-8-sig")

            page.click("#detWoo summary")
            page.fill("#wooUrl", f"http://127.0.0.1:{woo.server_port}")
            page.fill("#wooCk", "ck")
            page.fill("#wooCs", "cs")
            page.click("#btnVer")
            page.wait_for_selector("#btnAplicar:not([disabled])", timeout=20_000)
            page.on("dialog", lambda d: d.accept())
            page.click("#btnAplicar")
            page.wait_for_selector("text=Tienda actualizada", timeout=20_000)
            # lista de faltantes: sigue visible después de aplicar, sin REF/USA
            self.assertTrue(page.is_visible("#secFaltantes"))
            self.assertIn("/drones/dron-dji-mini-5-pro-combo", page.inner_text("#faltTabla"))  # link visible
            with page.expect_download() as dl2:
                page.click("#btnFaltantes")
            falt = Path(dl2.value.path()).read_text(encoding="utf-8-sig").splitlines()
            ctx.close()
        mock.shutdown()
        woo.shutdown()

        # REF-DRDJI073 (página 2) queda afuera por el filtro "REF, USA" por defecto
        self.assertEqual(csv_text.splitlines(), [
            "SKU,Precio normal,Precio rebajado",
            "DRDJI077,6199998,3099999", "DRDJI090,6399998,3199999", "GAD001,545907,",
            "DRDJI100,3999998,1799999", "DRDJI200,5999998,2999999"])
        self.assertEqual(falt[0], "SKU;Nombre;URL")
        self.assertEqual([l.split(";")[0] for l in falt[1:]], ["DRDJI090"])
        self.assertIn("Dron DJI Mini 5 Pro Combo", falt[1])
        self.assertTrue(falt[1].endswith("/drones/dron-dji-mini-5-pro-combo"))
        posts = dict(test_woo.POSTS)
        self.assertEqual(len(posts["/wp-json/wc/v3/products/batch"]["update"]), 2)
        self.assertEqual(len(posts["/wp-json/wc/v3/products/4/variations/batch"]["update"]), 1)


if __name__ == "__main__":
    unittest.main()
