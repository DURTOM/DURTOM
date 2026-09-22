"""Prueba sync_woo contra una API WooCommerce simulada (sin red)."""
import configparser
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bidcom_sync  # noqa: E402

STORE = [
    {"id": 1, "sku": "DRDJI077", "type": "simple", "regular_price": "6000000", "sale_price": ""},
    {"id": 2, "sku": "GAD001", "type": "simple", "regular_price": "545907", "sale_price": "500000"},
    {"id": 3, "sku": "OTRO", "type": "simple", "regular_price": "10", "sale_price": ""},
    {"id": 4, "sku": "", "type": "variable", "regular_price": "", "sale_price": ""},
]
VARS = {4: [{"id": 41, "sku": "DRDJI200", "regular_price": "5999998", "sale_price": "2999999"},
            {"id": 42, "sku": "DRDJI100", "regular_price": "1", "sale_price": ""}]}
POSTS = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, pages=1):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-WP-TotalPages", str(pages))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        page = int(parse_qs(u.query).get("page", ["1"])[0])
        if u.path.endswith("/products"):
            return self._send(STORE[:2] if page == 1 else STORE[2:], pages=2)
        pid = int(u.path.split("/")[-2])
        self._send(VARS[pid])

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        POSTS.append((urlparse(self.path).path, data))
        self._send({"update": data["update"]})


class TestSync(unittest.TestCase):
    def test_only_existing_skus_changed(self):
        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        cfg = configparser.ConfigParser()
        cfg.read_dict({"woocommerce": {"url": f"http://127.0.0.1:{srv.server_port}",
                                       "consumer_key": "k", "consumer_secret": "s"}})
        products = [
            {"sku": "DRDJI077", "normal": 6199998, "sale": 3099999},
            {"sku": "GAD001", "normal": 545907, "sale": None},
            {"sku": "DRDJI200", "normal": 5999998, "sale": 2999999},  # sin cambios
            {"sku": "DRDJI100", "normal": 3999998, "sale": 1799999},
            {"sku": "NOEXISTE", "normal": 1000, "sale": None},
        ]
        report = bidcom_sync.sync_woo(products, cfg)
        srv.shutdown()
        self.assertEqual({r[0] for r in report}, {"DRDJI077", "GAD001", "DRDJI100"})
        paths = dict(POSTS)
        self.assertEqual(paths["/wp-json/wc/v3/products/batch"]["update"], [
            {"id": 1, "regular_price": "6199998", "sale_price": "3099999"},
            {"id": 2, "regular_price": "545907", "sale_price": ""},
        ])
        self.assertEqual(paths["/wp-json/wc/v3/products/4/variations/batch"]["update"],
                         [{"id": 42, "regular_price": "3999998", "sale_price": "1799999"}])


if __name__ == "__main__":
    unittest.main()
