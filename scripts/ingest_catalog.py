#!/usr/bin/env python3
"""Create the product index (if missing) and bulk-load a catalog JSONL file
into an OpenSearch Serverless (AOSS) collection.

Auth is plain SigV4 using whatever AWS credentials are active in your shell
(same credential chain boto3 uses) -- no API keys, no OpenSearch-native users.
Your IAM principal needs to be in the collection's data-access policy
"write" principals (see tf/opensearch.tf's additional_write_principals, or
just run this as the same role that ran `terraform apply`).

Usage:
    python ingest_catalog.py \
        --endpoint https://xxxxx.us-west-2.aoss.amazonaws.com \
        --index products \
        --catalog ../data/catalog.jsonl \
        --region us-west-2
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection, helpers
from requests_aws4auth import AWS4Auth

# Lexical-only mapping: no knn_vector field. Keyword fields are exact-match
# filterable (category/color/brand); text fields are analyzed for BM25
# relevance search (title/description).
INDEX_MAPPING = {
    "settings": {"index": {"number_of_shards": 2}},
    "mappings": {
        "properties": {
            "sku": {"type": "keyword"},
            "title": {"type": "text"},
            "description": {"type": "text"},
            "category": {"type": "keyword"},
            "color": {"type": "keyword"},
            "brand": {"type": "keyword"},
            "price": {"type": "float"},
            "currency": {"type": "keyword"},
            "in_stock": {"type": "boolean"},
        }
    },
}


def build_client(endpoint: str, region: str) -> OpenSearch:
    host = endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    credentials = boto3.Session().get_credentials()
    if credentials is None:
        raise RuntimeError("No AWS credentials found -- configure a profile or env vars first.")
    awsauth = AWS4Auth(
        credentials.access_key,
        credentials.secret_key,
        region,
        "aoss",
        session_token=credentials.token,
    )
    return OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=awsauth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=60,
    )


def ensure_index(client: OpenSearch, index: str, recreate: bool) -> None:
    exists = client.indices.exists(index=index)
    if exists and recreate:
        print(f"Deleting existing index '{index}'...")
        client.indices.delete(index=index)
        exists = False
    if not exists:
        print(f"Creating index '{index}'...")
        client.indices.create(index=index, body=INDEX_MAPPING)
    else:
        print(f"Index '{index}' already exists, reusing it.")


def iter_actions(catalog_path: Path, index: str):
    with open(catalog_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            product = json.loads(line)
            yield {
                "_op_type": "index",
                "_index": index,
                "_id": product["sku"],
                "_source": product,
            }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True, help="AOSS collection endpoint (terraform output opensearch_collection_endpoint)")
    parser.add_argument("--index", default="products")
    parser.add_argument("--catalog", type=Path, default=Path(__file__).parent / "../data/catalog.jsonl")
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--recreate", action="store_true", help="Delete and recreate the index before loading")
    args = parser.parse_args()

    if not args.catalog.exists():
        sys.exit(f"Catalog file not found: {args.catalog} (run scripts/data_gen/generate_catalog.py first)")

    client = build_client(args.endpoint, args.region)
    ensure_index(client, args.index, args.recreate)

    print(f"Bulk-indexing from {args.catalog}...")
    success, errors = 0, []
    for ok, item in helpers.streaming_bulk(
        client,
        iter_actions(args.catalog, args.index),
        chunk_size=args.batch_size,
        raise_on_error=False,
    ):
        if ok:
            success += 1
        else:
            errors.append(item)
        if success % 5000 == 0 and success:
            print(f"  indexed {success}...")

    print(f"Done. Indexed {success} documents, {len(errors)} errors.")
    if errors:
        print("First few errors:")
        for e in errors[:5]:
            print(" ", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
