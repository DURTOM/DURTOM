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
            page.fill("#categorias", f"http://127.0.0.1:{mock.server_port}/p1.html")
            page.click("#btnExtraer")
            page.wait_for_selector("text=Listo", timeout=90_000)
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
            ctx.close()
        mock.shutdown()
        woo.shutdown()

        self.assertEqual(csv_text.splitlines(), [
            "SKU,Precio normal,Precio rebajado",
            "DRDJI077,6199998,3099999", "DRDJI090,6399998,3199999", "GAD001,545907,",
            "DRDJI100,3999998,1799999", "DRDJI200,5999998,2999999"])
        posts = dict(test_woo.POSTS)
        self.assertEqual(len(posts["/wp-json/wc/v3/products/batch"]["update"]), 2)
        self.assertEqual(len(posts["/wp-json/wc/v3/products/4/variations/batch"]["update"]), 1)


if __name__ == "__main__":
    unittest.main()
