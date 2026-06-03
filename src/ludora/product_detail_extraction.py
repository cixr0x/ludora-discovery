from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

from ludora.listing_extraction import _collapse_text, _extract_availability, _extract_price
from ludora.models import DiscoveryItemCandidateRecord, ItemCandidateType


PLAYER_RANGE_RE = re.compile(
    r"(?:jugadores?\s*:?\s*)?(?:de\s+)?(\d+)\s*(?:-|–|a)\s*(\d+)\s*jugadores?",
    re.IGNORECASE,
)
PLAYER_LABEL_RE = re.compile(r"jugadores?\s*:?\s*(\d+)\s*(?:-|–|a)\s*(\d+)", re.IGNORECASE)
MINUTES_RANGE_RE = re.compile(r"(\d+)\s*(?:-|–|a)\s*(\d+)\s*min", re.IGNORECASE)
MINUTES_SINGLE_RE = re.compile(r"(\d+)\s*min", re.IGNORECASE)
AGE_RE = re.compile(r"(?:edad|años|anos)\D{0,12}(\d+)\s*\+?", re.IGNORECASE)
PUBLISHER_RE = re.compile(r"(?:editorial|publisher)\s*:?\s*(.+)", re.IGNORECASE)
ENGLISH_LANGUAGE_RE = re.compile(r"\b(?:english|ingles)\b", re.IGNORECASE)
SPANISH_LANGUAGE_RE = re.compile(r"\b(?:spanish|espanol|castellano)\b", re.IGNORECASE)
HIGHLIGHTS_LABEL_RE = re.compile(r"\bhighlights?\s*:", re.IGNORECASE)
LANGUAGE_LABEL_RE = re.compile(r"\b(?:idioma|language)\s*:", re.IGNORECASE)
LANGUAGE_SNIPPET_STOPS = (
    " Añadir ",
    " Producto añadido",
    " Entrega ",
    " Productos Relacionados",
    " Related Products",
    " Menu ",
    " Menú ",
    " Contacto ",
)
MENU_CART_CLASSES = {
    "elementor-menu-cart",
    "elementor-menu-cart__toggle",
    "elementor-menu-cart__toggle_button",
    "widget_shopping_cart_content",
}
PRODUCT_IMAGE_CONTAINER_CLASSES = {
    "elementor-widget-woocommerce-product-images",
    "woocommerce-product-gallery",
    "woocommerce-product-gallery__image",
}
PRODUCT_IMAGE_CLASSES = {
    "wp-post-image",
}


@dataclass(frozen=True)
class LanguageDetection:
    language: str = ""
    source: str = ""
    evidence: str = ""


class ProductDetailParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.language = ""
        self.meta: dict[str, str] = {}
        self.text_nodes: list[str] = []
        self.title_parts: list[str] = []
        self.h1_parts: list[str] = []
        self.heading_parts: list[tuple[str, str]] = []
        self.json_ld_scripts: list[str] = []
        self.woocommerce_price_texts: list[str] = []
        self.woocommerce_stock_blocks: list[tuple[str, str]] = []
        self.product_image_urls: list[str] = []
        self._ignored_text_depth = 0
        self._inside_script = False
        self._current_script_type = ""
        self._script_parts: list[str] = []
        self._current_text_tag = ""
        self._price_capture_depth = 0
        self._price_parts: list[str] = []
        self._stock_capture_depth = 0
        self._stock_parts: list[str] = []
        self._stock_classes = ""
        self._product_image_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {name.casefold(): value or "" for name, value in attrs}
        normalized_tag = tag.casefold()
        class_value = attr.get("class", "")
        if self._ignored_text_depth:
            self._ignored_text_depth += 1
            return
        if normalized_tag in {"style", "noscript"} or _is_menu_cart_container(class_value):
            self._ignored_text_depth = 1
            return
        if self._product_image_depth:
            self._product_image_depth += 1
        elif _is_product_image_container(class_value):
            self._product_image_depth = 1
        if self._price_capture_depth:
            self._price_capture_depth += 1
        elif _is_woocommerce_product_price_container(normalized_tag, class_value):
            self._price_capture_depth = 1
            self._price_parts = []
        if self._stock_capture_depth:
            self._stock_capture_depth += 1
        elif _is_woocommerce_stock_container(class_value):
            self._stock_capture_depth = 1
            self._stock_parts = []
            self._stock_classes = class_value
        if normalized_tag == "html":
            self.language = attr.get("lang", "").strip()
        if normalized_tag == "meta":
            key = attr.get("property") or attr.get("name")
            content = attr.get("content", "").strip()
            if key and content:
                self.meta[key.casefold()] = content
        if self._product_image_depth and normalized_tag == "a":
            href = attr.get("href", "").strip()
            if _looks_like_image_url(href):
                self._append_product_image_url(href)
        if normalized_tag == "img" and (self._product_image_depth or _is_product_image_tag(class_value, attr)):
            self._append_product_image_url(_image_url_from_attrs(attr))
        if normalized_tag == "script":
            self._inside_script = True
            self._current_script_type = attr.get("type", "").casefold()
            self._script_parts = []
        if normalized_tag in {"title", "h1", "h2", "h3"}:
            self._current_text_tag = normalized_tag

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if self._ignored_text_depth:
            self._ignored_text_depth -= 1
            return
        if normalized_tag == "script":
            if self._inside_script and "ld+json" in self._current_script_type:
                script = "".join(self._script_parts).strip()
                if script:
                    self.json_ld_scripts.append(script)
            self._inside_script = False
            self._current_script_type = ""
            self._script_parts = []
        if self._price_capture_depth:
            self._price_capture_depth -= 1
            if self._price_capture_depth == 0:
                text = _collapse_text(" ".join(self._price_parts))
                if text:
                    self.woocommerce_price_texts.append(text)
                self._price_parts = []
        if self._stock_capture_depth:
            self._stock_capture_depth -= 1
            if self._stock_capture_depth == 0:
                text = _collapse_text(" ".join(self._stock_parts))
                if text:
                    self.woocommerce_stock_blocks.append((self._stock_classes, text))
                self._stock_parts = []
                self._stock_classes = ""
        if self._product_image_depth:
            self._product_image_depth -= 1
        if normalized_tag == self._current_text_tag:
            self._current_text_tag = ""

    def handle_data(self, data: str) -> None:
        if self._inside_script:
            self._script_parts.append(data)
            return
        if self._ignored_text_depth:
            return
        if self._price_capture_depth:
            self._price_parts.append(data)
            return
        if self._stock_capture_depth:
            self._stock_parts.append(data)
            return
        text = _collapse_text(data)
        if not text:
            return
        self.text_nodes.append(text)
        if self._current_text_tag == "title":
            self.title_parts.append(text)
        if self._current_text_tag == "h1":
            self.h1_parts.append(text)
        if self._current_text_tag in {"h1", "h2", "h3"}:
            self.heading_parts.append((self._current_text_tag, text))

    def _append_product_image_url(self, image_url: str) -> None:
        image_url = image_url.strip()
        if image_url and image_url not in self.product_image_urls:
            self.product_image_urls.append(image_url)


def extract_product_detail_candidate(
    html: str,
    product_url: str,
    store_id: int | None,
    source_listing_url: str,
) -> DiscoveryItemCandidateRecord | None:
    parser = ProductDetailParser(product_url)
    parser.feed(html)

    json_ld_product = _extract_json_ld_product(parser.json_ld_scripts)
    text = " ".join(parser.text_nodes)
    title = _first_text(
        _json_text(json_ld_product, "name"),
        _visible_product_heading(parser.heading_parts),
        " ".join(parser.h1_parts),
        _strip_title_suffix(parser.meta.get("og:title", "")),
        parser.meta.get("og:title", ""),
        _strip_title_suffix(" ".join(parser.title_parts)),
        " ".join(parser.title_parts),
    )
    if not title:
        return None
    description = _first_text(
        _json_text(json_ld_product, "description"),
        parser.meta.get("og:description", ""),
        parser.meta.get("description", ""),
    )

    offer = _first_offer(json_ld_product.get("offers")) if json_ld_product else {}
    raw_price = _json_text(offer, "price")
    price = _normalize_offer_price(raw_price)
    price_source = "json_ld_offer" if price else "none"
    if not price:
        raw_price, price, price_source = _first_price_from_texts(
            parser.woocommerce_price_texts,
            "woocommerce_product_price",
        )
    if not price:
        raw_price, price, price_source = _first_price_from_texts([text], "generic_text")

    currency = _first_text(_json_text(offer, "priceCurrency"), _detect_currency(text), "MXN")
    availability = _availability_from_schema(_json_text(offer, "availability"))
    availability_source = "json_ld_offer" if availability != "unknown" else "none"
    if availability == "unknown":
        availability, availability_source = _availability_from_stock_blocks(parser.woocommerce_stock_blocks)
    if availability == "unknown":
        _, availability = _extract_availability(text)
        availability_source = "generic_text" if availability != "unknown" else "none"

    min_players, max_players = _extract_players(text)
    min_minutes, max_minutes = _extract_minutes(text)
    language_detection = _detect_item_language_detail(title, product_url, description, text)

    raw_payload: dict[str, object] = {
        "meta": parser.meta,
        "text": text,
    }
    if json_ld_product:
        raw_payload["json_ld"] = json_ld_product

    return DiscoveryItemCandidateRecord(
        store_id=store_id,
        source_url=product_url,
        source_listing_url=source_listing_url,
        title=title,
        publisher=_first_text(
            _publisher_from_json(json_ld_product),
            _publisher_from_text_nodes(parser.text_nodes),
        ),
        description=description,
        item_type=_infer_item_type(title, product_url),
        min_players=min_players,
        max_players=max_players,
        min_minutes=min_minutes,
        max_minutes=max_minutes,
        min_age=_extract_min_age(text),
        language=language_detection.language,
        language_source=language_detection.source,
        language_evidence=language_detection.evidence,
        image_url=_resolve_url(
            product_url,
            _first_text(
                _json_image(json_ld_product),
                parser.meta.get("og:image", ""),
                parser.meta.get("twitter:image", ""),
                parser.meta.get("twitter:image:src", ""),
                *parser.product_image_urls,
            ),
        ),
        raw_price=raw_price,
        price=price,
        price_source=price_source,
        currency=currency.upper(),
        availability=availability,
        availability_source=availability_source,
        store_sku=_json_text(json_ld_product, "sku"),
        raw_payload=raw_payload,
    )


def _extract_json_ld_product(scripts: list[str]) -> dict[str, Any]:
    for script in scripts:
        try:
            parsed = json.loads(script)
        except json.JSONDecodeError:
            continue
        product = _find_product(parsed)
        if product:
            return product
    return {}


def _find_product(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        for item in value:
            product = _find_product(item)
            if product:
                return product
        return {}
    if not isinstance(value, dict):
        return {}
    type_value = value.get("@type")
    types = [type_value] if isinstance(type_value, str) else type_value if isinstance(type_value, list) else []
    if any(str(item).casefold() == "product" for item in types):
        return value
    graph = value.get("@graph")
    if graph:
        return _find_product(graph)
    return {}


def _first_offer(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                return item
        return {}
    if isinstance(value, dict):
        return value
    return {}


def _json_text(source: dict[str, Any], key: str) -> str:
    value = source.get(key) if source else ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return ""


def _is_menu_cart_container(class_value: str) -> bool:
    classes = _class_tokens(class_value)
    return bool(classes.intersection(MENU_CART_CLASSES))


def _is_product_image_container(class_value: str) -> bool:
    classes = _class_tokens(class_value)
    return bool(classes.intersection(PRODUCT_IMAGE_CONTAINER_CLASSES))


def _is_product_image_tag(class_value: str, attrs: dict[str, str]) -> bool:
    classes = _class_tokens(class_value)
    itemprop = attrs.get("itemprop", "").casefold()
    return bool(classes.intersection(PRODUCT_IMAGE_CLASSES)) or itemprop == "image"


def _is_woocommerce_product_price_container(tag: str, class_value: str) -> bool:
    classes = _class_tokens(class_value)
    return "elementor-widget-woocommerce-product-price" in classes or (tag == "p" and "price" in classes)


def _is_woocommerce_stock_container(class_value: str) -> bool:
    return "stock" in _class_tokens(class_value)


def _class_tokens(class_value: str) -> set[str]:
    return {token.strip().casefold() for token in class_value.split() if token.strip()}


def _image_url_from_attrs(attrs: dict[str, str]) -> str:
    for key in ("data-large_image", "data-src", "data-lazy-src", "src"):
        value = attrs.get(key, "").strip()
        if value and not value.startswith("data:"):
            return value
    return _largest_srcset_url(attrs.get("srcset", ""))


def _largest_srcset_url(srcset: str) -> str:
    best_url = ""
    best_width = -1
    for part in srcset.split(","):
        pieces = part.strip().split()
        if not pieces:
            continue
        url = pieces[0]
        width = 0
        if len(pieces) > 1 and pieces[1].endswith("w"):
            try:
                width = int(pieces[1][:-1])
            except ValueError:
                width = 0
        if width > best_width:
            best_url = url
            best_width = width
    return best_url


def _looks_like_image_url(value: str) -> bool:
    if not value or value.startswith("#") or value.startswith("data:"):
        return False
    path = urlparse(value).path.casefold()
    return path.endswith((".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"))


def _visible_product_heading(heading_parts: list[tuple[str, str]]) -> str:
    h1 = _first_text(*(text for tag, text in heading_parts if tag == "h1"))
    if h1:
        return h1
    return _first_text(*(text for tag, text in heading_parts if tag == "h2"))


def _strip_title_suffix(value: str) -> str:
    normalized = _collapse_text(value)
    if not normalized:
        return ""
    for separator in (" | ", " - "):
        if separator in normalized:
            return normalized.split(separator, 1)[0].strip()
    return ""


def _first_price_from_texts(values: list[str], source: str) -> tuple[str, str, str]:
    for value in values:
        raw_price, price = _extract_price(value)
        if price:
            return _normalize_raw_price(raw_price), price, source
    return "", "", "none"


def _normalize_raw_price(value: str) -> str:
    return re.sub(r"\$\s+", "$", value)


def _availability_from_stock_blocks(stock_blocks: list[tuple[str, str]]) -> tuple[str, str]:
    for class_value, text in stock_blocks:
        classes = _class_tokens(class_value)
        if "out-of-stock" in classes or "outofstock" in classes:
            return "out_of_stock", "woocommerce_stock"
        if "in-stock" in classes or "instock" in classes:
            return "available", "woocommerce_stock"
        _, availability = _extract_availability(text)
        if availability != "unknown":
            return availability, "woocommerce_stock"
    return "unknown", "none"


def _publisher_from_json(product: dict[str, Any]) -> str:
    for key in ("publisher", "brand", "manufacturer"):
        value = product.get(key) if product else None
        if isinstance(value, dict):
            name = _json_text(value, "name")
            if name:
                return name
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _publisher_from_text_nodes(text_nodes: list[str]) -> str:
    for text in text_nodes:
        match = PUBLISHER_RE.search(text)
        if match:
            return match.group(1).strip()
    return ""


def _json_image(product: dict[str, Any]) -> str:
    image = product.get("image") if product else None
    if isinstance(image, str):
        return image
    if isinstance(image, list):
        for item in image:
            if isinstance(item, str):
                return item
            if isinstance(item, dict):
                url = _json_text(item, "url")
                if url:
                    return url
    if isinstance(image, dict):
        return _json_text(image, "url")
    return ""


def _resolve_url(base_url: str, value: str) -> str:
    return urljoin(base_url, value) if value else ""


def _first_text(*values: str) -> str:
    for value in values:
        normalized = _collapse_text(value)
        if normalized:
            return normalized
    return ""


def _detect_item_language(title: str, product_url: str, description: str, page_text: str) -> str:
    return _detect_item_language_detail(title, product_url, description, page_text).language


def _detect_item_language_detail(title: str, product_url: str, description: str, page_text: str) -> LanguageDetection:
    for source, value in (("title", title), ("source_url", product_url)):
        language = _language_from_text(value)
        if language:
            return LanguageDetection(language=language, source=source, evidence=_collapse_text(value))

    scoped_detection = _detect_product_scoped_language(page_text)
    if scoped_detection.language:
        return scoped_detection

    for source, value in (("description", description), ("page_text", page_text)):
        language = _language_from_text(value)
        if language:
            return LanguageDetection(language=language, source=source, evidence=_language_evidence(value))

    return LanguageDetection()


def _detect_product_scoped_language(page_text: str) -> LanguageDetection:
    text = _collapse_text(page_text)
    for source, label_re in (("product_highlights", HIGHLIGHTS_LABEL_RE), ("product_language_label", LANGUAGE_LABEL_RE)):
        for match in label_re.finditer(text):
            evidence = _bounded_language_snippet(text, match.start())
            language = _language_from_text(evidence)
            if language:
                return LanguageDetection(language=language, source=source, evidence=evidence)
    return LanguageDetection()


def _bounded_language_snippet(text: str, start: int) -> str:
    end = min(len(text), start + 220)
    for stop in LANGUAGE_SNIPPET_STOPS:
        stop_index = text.find(stop, start + 1, end)
        if stop_index >= 0:
            end = min(end, stop_index)
    return _collapse_text(text[start:end])


def _language_evidence(value: str) -> str:
    text = _collapse_text(value)
    normalized = _normalize_language_text(text)
    for term in ("english", "ingles", "spanish", "espanol", "castellano"):
        index = normalized.find(term)
        if index >= 0:
            return _collapse_text(text[max(0, index - 80) : min(len(text), index + 120)])
    return text[:200]


def _language_from_text(value: str) -> str:
    normalized = _normalize_language_text(value)
    has_english = bool(ENGLISH_LANGUAGE_RE.search(normalized))
    has_spanish = bool(SPANISH_LANGUAGE_RE.search(normalized))
    if has_english == has_spanish:
        return ""
    return "en" if has_english else "es"


def _normalize_language_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def _normalize_offer_price(raw_price: str) -> str:
    if not raw_price:
        return ""
    normalized = raw_price.replace(",", "").strip()
    return normalized


def _detect_currency(text: str) -> str:
    if "mxn" in text.casefold():
        return "MXN"
    return ""


def _availability_from_schema(value: str) -> str:
    normalized = value.casefold()
    if "instock" in normalized or "in_stock" in normalized:
        return "available"
    if "outofstock" in normalized or "soldout" in normalized or "out_of_stock" in normalized:
        return "out_of_stock"
    return "unknown"


def _extract_players(text: str) -> tuple[int | None, int | None]:
    match = PLAYER_RANGE_RE.search(text) or PLAYER_LABEL_RE.search(text)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _extract_minutes(text: str) -> tuple[int | None, int | None]:
    range_match = MINUTES_RANGE_RE.search(text)
    if range_match:
        return int(range_match.group(1)), int(range_match.group(2))
    single_match = MINUTES_SINGLE_RE.search(text)
    if single_match:
        minutes = int(single_match.group(1))
        return minutes, minutes
    return None, None


def _extract_min_age(text: str) -> int | None:
    match = AGE_RE.search(text)
    return int(match.group(1)) if match else None


def _infer_item_type(title: str, product_url: str) -> ItemCandidateType:
    value = f"{title} {product_url}".casefold()
    if any(term in value for term in ("expansion", "expansión", "expansiones")):
        return "expansion"
    return "unknown"
