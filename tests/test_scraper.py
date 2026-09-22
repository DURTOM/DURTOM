"""Prueba la extracción sobre páginas simuladas tipo Bidcom (tests/mock)."""
import configparser
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bidcom_sync  # noqa: E402

MOCK = Path(__file__).resolve().parent / "mock" / "p1.html"


class TestScraper(unittest.TestCase):
    def test_extract(self):
        cfg = configparser.ConfigParser()
        cfg.read_dict({"scraper": {"espera_ms": "400"}})
        got = {p["sku"]: (p["normal"], p["sale"]) for p in bidcom_sync.scrape_all([MOCK.as_uri()], cfg)}
        self.assertEqual(got, {
            "DRDJI077": (6199998, 3099999),   # tachado + cuotas + sin impuestos
            "DRDJI090": (6399998, 3199999),   # clase "price-old"
            "GAD001": (545907, None),         # sin rebaja
            "DRDJI100": (3999998, 1799999),   # carga diferida al hacer scroll
            "DRDJI200": (5999998, 2999999),   # página 2, sin tachado pero con % OFF
        })


if __name__ == "__main__":
    unittest.main()
