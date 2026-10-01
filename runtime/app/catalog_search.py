"""Direct OpenSearch (AOSS) lexical search tool for the orchestrator agent.

Deliberately simple: BM25 multi_match over title/description plus structured
filters (category tags, color, price range) that the agent is expected to
derive itself from the user's request -- see prompt.py for the taxonomy the
agent is taught so it can map "tan leather loafers" -> categories=["mens",
"shoes"], colors=["tan"] even though "loafers" never appears in the catalog
text itself.
"""

from __future__ import annotations

import os
import threading

import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection
from requests_aws4auth import AWS4Auth
from strands import tool

_client_lock = threading.Lock()
_client: OpenSearch | None = None


def _get_client() -> OpenSearch:
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        endpoint = os.environ["OPENSEARCH_ENDPOINT"]
        region = os.environ.get("AWS_REGION", "us-west-2")
        host = endpoint.replace("https://", "").replace("http://", "").rstrip("/")
        credentials = boto3.Session().get_credentials()
        awsauth = AWS4Auth(
            credentials.access_key,
            credentials.secret_key,
            region,
            "aoss",
            session_token=credentials.token,
        )
        _client = OpenSearch(
            hosts=[{"host": host, "port": 443}],
            http_auth=awsauth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            timeout=10,
        )
        return _client


def _build_query(
    query: str,
    categories: list[str] | None,
    colors: list[str] | None,
    min_price: float | None,
    max_price: float | None,
) -> dict:
    must: list[dict] = []
    if query:
        must.append({
            "multi_match": {
                "query": query,
                "fields": ["title^3", "description", "category^2", "brand"],
                "fuzziness": "AUTO",
            }
        })
    else:
        must.append({"match_all": {}})

    filters: list[dict] = []
    for tag in categories or []:
        filters.append({"term": {"category": tag.lower()}})
    if colors:
        filters.append({"terms": {"color": [c.lower() for c in colors]}})
    price_range = {}
    if min_price is not None:
        price_range["gte"] = min_price
    if max_price is not None:
        price_range["lte"] = max_price
    if price_range:
        filters.append({"range": {"price": price_range}})

    return {"bool": {"must": must, "filter": filters}}


# Process-wide cache of the last search results, keyed by sku, so the
# orchestrator can reference a product by sku (via a <product sku="..."/>
# tag in its answer) without re-querying OpenSearch for display data.
_result_cache: dict[str, dict] = {}
_result_cache_lock = threading.Lock()


def get_cached_product(sku: str) -> dict | None:
    with _result_cache_lock:
        return _result_cache.get(sku)


@tool
def search_catalog(
    query: str,
    categories: list[str] | None = None,
    colors: list[str] | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    max_results: int = 8,
) -> list[dict]:
    """Search the product catalog.

    Args:
        query: Free-text description of what the shopper wants (used for
            relevance ranking against product titles/descriptions).
        categories: Structured category tags to filter on, ALL of which must
            be present on a matching product (e.g. ["mens", "pants"]). Use the
            segment/category vocabulary you were given in your instructions,
            not raw words from the user's message.
        colors: Structured color filter. A matching product's color must be
            ONE OF these (e.g. ["green", "tan"]). Use only colors from your
            instructions' color list.
        min_price: Minimum price in USD, inclusive.
        max_price: Maximum price in USD, inclusive.
        max_results: Max number of products to return (default 8, max 24).

    Returns:
        A list of product dicts (sku, title, description, category, color,
        brand, price, currency, in_stock). Empty list means no matches --
        say so honestly rather than inventing a product.
    """
    max_results = max(1, min(max_results, 24))
    index = os.environ.get("OPENSEARCH_INDEX", "products")
    body = {
        "size": max_results,
        "query": _build_query(query, categories, colors, min_price, max_price),
    }

    client = _get_client()
    response = client.search(index=index, body=body)
    hits = response.get("hits", {}).get("hits", [])

    products = []
    for hit in hits:
        source = hit["_source"]
        with _result_cache_lock:
            _result_cache[source["sku"]] = source
        products.append(source)
    return products
