import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ludora.product_detail_extraction import extract_product_detail_candidate


class ProductDetailExtractionTests(unittest.TestCase):
    def test_extracts_json_ld_product_offer(self):
        html = """
        <html>
          <head>
            <script type="application/ld+json">
            {
              "@context": "https://schema.org",
              "@type": "Product",
              "name": "Catan",
              "description": "Trade, build, settle.",
              "brand": {"name": "Devir"},
              "sku": "CATAN-ES",
              "image": ["https://example.mx/catan.jpg"],
              "offers": {
                "@type": "Offer",
                "price": "899.00",
                "priceCurrency": "MXN",
                "availability": "https://schema.org/InStock"
              }
            }
            </script>
          </head>
          <body>
            <p>De 3 a 4 jugadores. 60-90 min. Edad 10+</p>
          </body>
        </html>
        """

        record = extract_product_detail_candidate(
            html,
            "https://example.mx/products/catan",
            12,
            "https://example.mx/collections/juegos",
        )

        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.store_id, 12)
        self.assertEqual(record.source_url, "https://example.mx/products/catan")
        self.assertEqual(record.source_listing_url, "https://example.mx/collections/juegos")
        self.assertEqual(record.title, "Catan")
        self.assertEqual(record.publisher, "Devir")
        self.assertEqual(record.description, "Trade, build, settle.")
        self.assertEqual(record.image_url, "https://example.mx/catan.jpg")
        self.assertEqual(record.raw_price, "899.00")
        self.assertEqual(record.price, "899.00")
        self.assertEqual(record.price_source, "json_ld_offer")
        self.assertEqual(record.currency, "MXN")
        self.assertEqual(record.availability, "available")
        self.assertEqual(record.availability_source, "json_ld_offer")
        self.assertEqual(record.store_sku, "CATAN-ES")
        self.assertEqual(record.min_players, 3)
        self.assertEqual(record.max_players, 4)
        self.assertEqual(record.min_minutes, 60)
        self.assertEqual(record.max_minutes, 90)
        self.assertEqual(record.min_age, 10)
        self.assertEqual(record.raw_payload["json_ld"]["name"], "Catan")

    def test_extracts_html_meta_fallback_fields(self):
        html = """
        <html lang="es-MX">
          <head>
            <meta property="og:title" content="Dixit">
            <meta property="og:description" content="Juego de imaginacion.">
            <meta property="og:image" content="/images/dixit.jpg">
          </head>
          <body>
            <h1>Dixit</h1>
            <p>Editorial: Libellud</p>
            <p>Jugadores: 3-6</p>
            <p>Duracion: 30 min</p>
            <p>Edad: 8+</p>
            <p>Precio $650 MXN</p>
            <p>Disponible</p>
          </body>
        </html>
        """

        record = extract_product_detail_candidate(
            html,
            "https://example.mx/products/dixit",
            12,
            "https://example.mx/",
        )

        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.title, "Dixit")
        self.assertEqual(record.publisher, "Libellud")
        self.assertEqual(record.description, "Juego de imaginacion.")
        self.assertEqual(record.image_url, "https://example.mx/images/dixit.jpg")
        self.assertEqual(record.language, "")
        self.assertEqual(record.min_players, 3)
        self.assertEqual(record.max_players, 6)
        self.assertEqual(record.min_minutes, 30)
        self.assertEqual(record.max_minutes, 30)
        self.assertEqual(record.min_age, 8)
        self.assertEqual(record.price, "650.00")
        self.assertEqual(record.price_source, "generic_text")
        self.assertEqual(record.currency, "MXN")
        self.assertEqual(record.availability, "available")
        self.assertEqual(record.availability_source, "generic_text")

    def test_prefers_rendered_product_body_when_page_meta_is_stale(self):
        html = """
        <html lang="es-MX">
          <head>
            <title>7-Die Set Opaque Light Blue/white Chessex 25416</title>
            <meta property="og:title" content="7-Die Set Opaque Light Blue/white Chessex 25416">
            <meta property="og:description" content="Los dados opacos Chessex han sido el estandar durante decadas.">
            <meta property="og:image" content="/images/blue-dice.jpg">
          </head>
          <body>
            <h1>Catan</h1>
            <img alt="Catan" src="/images/catan.jpg">
            <p>$850.00 MXN</p>
            <p>Almost Gone!</p>
            <p>Quantity</p>
            <p>Add to Cart</p>
            <p>Idioma: Espa\u00f1ol</p>
            <p>Jugadores: 3-4</p>
            <p>Duraci\u00f3n: 75 minutos</p>
            <p>Edad: 10+</p>
            <p>Editorial: Devir / Kosmos</p>
            <p>Sois los primeros colonos en llegar a la isla de Cat\u00e1n.</p>
            <p>Pero enseguida el espacio en la isla empieza a escasear.</p>
            <p>Copyright \u00a9 2020 AVALON - Todos los derechos reservados.</p>
          </body>
        </html>
        """

        record = extract_product_detail_candidate(
            html,
            "https://avalonstore.com.mx/tienda/ols/products/catan",
            5,
            "https://avalonstore.com.mx/sitemap.xml",
        )

        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.title, "Catan")
        self.assertIn("primeros colonos", record.description)
        self.assertNotIn("dados opacos", record.description)
        self.assertEqual(record.image_url, "https://avalonstore.com.mx/images/catan.jpg")

    def test_prefers_woocommerce_product_price_over_mini_cart_total(self):
        html = """
        <html lang="es-MX">
          <head>
            <title>Exploding Kittens - Juegos de Mesa | Caravana Gamelab</title>
          </head>
          <body>
            <div class="elementor-menu-cart__toggle">
              <span class="woocommerce-Price-amount amount">
                <bdi><span class="woocommerce-Price-currencySymbol">$</span>0.00</bdi>
              </span>
            </div>
            <div class="elementor-widget elementor-widget-heading">
              <h2>Exploding Kittens</h2>
            </div>
            <div class="elementor-widget elementor-widget-woocommerce-product-price">
              <p class="price">
                <span class="woocommerce-Price-amount amount">
                  <bdi><span class="woocommerce-Price-currencySymbol">$</span>470.00</bdi>
                </span>
                <small class="woocommerce-price-suffix">IVA</small>
              </p>
            </div>
            <div class="elementor-widget elementor-widget-woocommerce-product-add-to-cart">
              <p class="stock in-stock">Solo quedan 1 disponibles</p>
              <button type="submit" class="single_add_to_cart_button button alt">Anadir al carrito</button>
            </div>
          </body>
        </html>
        """

        record = extract_product_detail_candidate(
            html,
            "https://caravanagameshop.com/producto/exploding-kittens/",
            4,
            "https://caravanagameshop.com/sitemap.xml",
        )

        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.raw_price, "$470.00")
        self.assertEqual(record.price, "470.00")
        self.assertEqual(record.price_source, "woocommerce_product_price")
        self.assertEqual(record.availability, "available")
        self.assertEqual(record.availability_source, "woocommerce_stock")

    def test_extracts_woocommerce_product_gallery_image(self):
        html = """
        <html lang="es-MX">
          <head>
            <title>Kitchen Rush - Tienda y Restaurante con Juegos de Mesa | Caravana Game Shop</title>
          </head>
          <body>
            <h1>Kitchen Rush</h1>
            <div class="woocommerce-product-gallery woocommerce-product-gallery--with-images images">
              <div class="woocommerce-product-gallery__wrapper">
                <div class="woocommerce-product-gallery__image">
                  <a href="https://caravanagameshop.com/wp-content/uploads/2024/05/kitchen_rush_1.png">
                    <img
                      width="600"
                      height="600"
                      src="https://caravanagameshop.com/wp-content/uploads/2024/05/kitchen_rush_1-600x600.png"
                      class="wp-post-image"
                      data-src="https://caravanagameshop.com/wp-content/uploads/2024/05/kitchen_rush_1-600x600.png"
                      data-large_image="https://caravanagameshop.com/wp-content/uploads/2024/05/kitchen_rush_1.png"
                    />
                  </a>
                </div>
              </div>
            </div>
          </body>
        </html>
        """

        record = extract_product_detail_candidate(
            html,
            "https://caravanagameshop.com/producto/kitchen-rush/",
            4,
            "https://caravanagameshop.com/product-sitemap.xml",
        )

        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(
            record.image_url,
            "https://caravanagameshop.com/wp-content/uploads/2024/05/kitchen_rush_1.png",
        )

    def test_prefers_visible_product_heading_over_suffixed_meta_title(self):
        html = """
        <html lang="es-MX">
          <head>
            <title>Smash Burguer - Tienda y Restaurante con Juegos de Mesa | Caravana Game Shop</title>
            <meta property="og:title" content="Smash Burguer - Tienda y Restaurante con Juegos de Mesa | Caravana Game Shop">
          </head>
          <body>
            <div class="elementor-widget elementor-widget-heading">
              <h2>Smash Burguer</h2>
            </div>
            <div class="elementor-widget elementor-widget-woocommerce-product-price">
              <p class="price">
                <span class="woocommerce-Price-amount amount">
                  <bdi><span class="woocommerce-Price-currencySymbol">$</span>390.00</bdi>
                </span>
              </p>
            </div>
          </body>
        </html>
        """

        record = extract_product_detail_candidate(
            html,
            "https://caravanagameshop.com/producto/smash-burguer/",
            4,
            "https://caravanagameshop.com/sitemap.xml",
        )

        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.title, "Smash Burguer")

    def test_detects_english_edition_from_product_title(self):
        html = """
        <html lang="es-MX">
          <head>
            <meta property="og:title" content="Wingspan (Ingles)">
          </head>
          <body>
            <h1>Wingspan (Ingles)</h1>
            <p>Jugadores: 1-5</p>
          </body>
        </html>
        """

        record = extract_product_detail_candidate(
            html,
            "https://example.mx/products/wingspan-ingles",
            12,
            "https://example.mx/",
        )

        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.language, "en")

    def test_detects_language_from_product_highlights_before_related_products(self):
        html = """
        <html lang="es-MX">
          <head>
            <title>Kitchen Rush - Tienda y Restaurante con Juegos de Mesa | Caravana Game Shop</title>
          </head>
          <body>
            <h1>Kitchen Rush</h1>
            <p>Highlights: 8+ 2-4 jugadores 20-60 min Inglés</p>
            <section>
              <h2>Productos Relacionados</h2>
              <a>Dixit Español</a>
            </section>
          </body>
        </html>
        """

        record = extract_product_detail_candidate(
            html,
            "https://caravanagameshop.com/producto/kitchen-rush/",
            4,
            "https://caravanagameshop.com/product-sitemap.xml",
        )

        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.language, "en")
        self.assertEqual(record.language_source, "product_highlights")
        self.assertEqual(record.language_evidence, "Highlights: 8+ 2-4 jugadores 20-60 min Inglés")

    def test_detects_spanish_edition_from_product_title(self):
        html = """
        <html lang="en">
          <head>
            <meta property="og:title" content="Catan Espanol">
          </head>
          <body>
            <h1>Catan Espanol</h1>
            <p>Players: 3-4</p>
          </body>
        </html>
        """

        record = extract_product_detail_candidate(
            html,
            "https://example.mx/products/catan-espanol",
            12,
            "https://example.mx/",
        )

        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record.language, "es")

    def test_returns_none_when_product_title_is_missing(self):
        html = "<html><body><p>Producto sin titulo claro.</p></body></html>"

        record = extract_product_detail_candidate(html, "https://example.mx/products/empty", 12, "")

        self.assertIsNone(record)


if __name__ == "__main__":
    unittest.main()
