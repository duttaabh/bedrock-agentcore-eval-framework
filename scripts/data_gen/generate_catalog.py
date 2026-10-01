#!/usr/bin/env python3
"""Generate a synthetic product catalog for the eval runtime's OpenSearch index.

No live LLM calls -- a fixed vocabulary of segments/categories/colors/title
adjectives/materials is combined and expanded (with light randomization) into
however many product records you ask for. Deterministic given the same
--seed, so re-running produces the same catalog.

The taxonomy below (which segment carries which category, the 15 colors, the
15 title adjectives) is deliberately not arbitrary: it matches what
scripts/eval/stress_cases/*.yaml expect to exist (ported from the shopping-
assistant eval suite), e.g. "kids has shirts/pants/jackets/shoes only, no
tshirts", "sweaters exist for mens and womens, not kids", 15 named colors
including green/red/tan/navy/charcoal/beige/cream. Changing the taxonomy here
without updating the stress cases (or vice versa) will desync the two.

Usage:
    python generate_catalog.py --count 100000 --out ../../data/catalog.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import uuid
from pathlib import Path

# Segment -> categories available in that segment. This *is* the taxonomy
# scripts/eval/stress_cases/*.yaml are grounded against -- see e.g.
# category_kids_pants.yaml ("kids/pants exists"), search_kids_shirts.yaml
# ("kids segment has shirts, pants, jackets and shoes only, no tshirts").
SEGMENT_CATEGORIES: dict[str, list[str]] = {
    "mens": ["shirts", "tshirts", "pants", "jackets", "sweaters", "shoes"],
    "womens": ["shirts", "tshirts", "pants", "dresses", "skirts", "jackets", "sweaters", "shoes"],
    "kids": ["shirts", "pants", "jackets", "shoes"],
    "accessories": ["belts", "jewelry"],
}

# Exactly 15, matching the stress-case comments (green/red/tan/navy/charcoal/
# beige/cream are all referenced by name in specific cases).
COLORS = [
    "black", "white", "gray", "navy", "brown", "tan", "beige", "cream",
    "charcoal", "green", "red", "blue", "pink", "purple", "burgundy",
]

# Exactly 15, matching stress-case comments (Casual/Relaxed/Elegant/Tailored
# are referenced by name). Deliberately generic style words, not specific
# sub-styles (no "loafer", "sneaker", "button-down", etc. -- stress cases
# assert those do NOT exist in the catalog).
TITLE_ADJECTIVES = [
    "Casual", "Relaxed", "Elegant", "Tailored", "Classic", "Modern",
    "Slim-Fit", "Comfort", "Everyday", "Premium", "Essential", "Signature",
    "Refined", "Sporty", "Lightweight",
]

# Materials live in the description text only -- not a structured/filterable
# field (several stress cases rely on this: "material is in the description
# only, so it is left to the judge"). Picked per-category so a "pants" item
# doesn't get described as "suede".
CATEGORY_MATERIALS: dict[str, list[str]] = {
    "shirts": ["cotton", "linen", "poplin"],
    "tshirts": ["cotton", "jersey knit"],
    "pants": ["cotton", "denim", "twill"],
    "dresses": ["silk", "cotton", "chiffon"],
    "skirts": ["silk", "cotton", "wool blend"],
    "jackets": ["leather", "suede", "denim", "wool"],
    "sweaters": ["wool", "cashmere blend", "cotton knit"],
    "shoes": ["leather", "suede", "canvas"],
    "belts": ["leather", "suede"],
    "jewelry": ["sterling silver", "gold-plated", "stainless steel"],
}

SINGULAR = {
    "shirts": "shirt", "tshirts": "t-shirt", "pants": "pants", "dresses": "dress",
    "skirts": "skirt", "jackets": "jacket", "sweaters": "sweater", "shoes": "shoes",
    "belts": "belt", "jewelry": "jewelry piece",
}

# (min, max) price band per category, in dollars. jackets explicitly span
# $15-$299 per combined_mens_casual_under_200.yaml / price_jackets_under_150.yaml.
PRICE_BANDS: dict[str, tuple[float, float]] = {
    "shirts": (12, 60), "tshirts": (10, 35), "pants": (20, 90),
    "dresses": (25, 120), "skirts": (20, 90), "jackets": (15, 299),
    "sweaters": (25, 110), "shoes": (25, 180), "belts": (15, 60),
    "jewelry": (10, 150),
}

BRAND_NAMES = [
    "Northfield", "Carraway", "Linden & Co.", "Alder Supply", "Meridian",
    "Wrenfield", "Harbor Row", "Birchwood", "Dalton Lane", "Juniper Trail",
]

OCCASION_FLAVOR = [
    "for everyday wear", "for the office", "for weekend outings",
    "for layering", "built to last", "designed for easy care", "",
]


def _build_product(segment: str, category: str, rng: random.Random) -> dict:
    color = rng.choice(COLORS)
    adjective = rng.choice(TITLE_ADJECTIVES)
    material = rng.choice(CATEGORY_MATERIALS[category])
    brand = rng.choice(BRAND_NAMES)
    singular = SINGULAR[category]
    lo, hi = PRICE_BANDS[category]
    price = round(rng.uniform(lo, hi), 2)
    flavor = rng.choice(OCCASION_FLAVOR)

    title = f"{adjective} {color.title()} {singular.title()}" if segment == "accessories" \
        else f"{adjective} {segment.title()} {color.title()} {singular.title()}"

    description = (
        f"{material.capitalize()} {singular} in {color} from {brand}. "
        f"{flavor}".strip()
    )

    return {
        "sku": str(uuid.uuid4()),
        "title": title,
        "description": description,
        "category": [segment, category],
        "color": color,
        "brand": brand,
        "price": price,
        "currency": "USD",
        "in_stock": rng.random() > 0.05,
    }


def generate(count: int, seed: int) -> list[dict]:
    rng = random.Random(seed)

    # Build the flat list of (segment, category) combos, then sample from it
    # uniformly so every combo gets a roughly even share of `count` products.
    combos = [
        (segment, category)
        for segment, categories in SEGMENT_CATEGORIES.items()
        for category in categories
    ]

    products = []
    for i in range(count):
        segment, category = combos[i % len(combos)]
        products.append(_build_product(segment, category, rng))
    rng.shuffle(products)
    return products


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "../../data/catalog.jsonl")
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    products = generate(args.count, args.seed)

    with open(args.out, "w") as f:
        for product in products:
            f.write(json.dumps(product) + "\n")

    print(f"Wrote {len(products)} products to {args.out}")


if __name__ == "__main__":
    main()
