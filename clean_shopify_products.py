"""
Shopify Product CSV Cleaning Script -- prepares data for Dify Knowledge Base

Usage:
    python clean_shopify_products.py products_export_1.csv

Outputs (written to ./output/):
    1. products_chunks.json  -- flat list of chunk records, one per text block
                                 (a product with a long Body may produce multiple
                                 chunks; all of them carry the same metadata)
    2. products_kb.txt       -- the actual knowledge-base text, chunks separated
                                 by "\n\n=====\n\n"
    3. review_flags.json     -- products where the variant table was auto-summarized
                                 (worth a quick human check)

Edit the CONFIG section below as needed.
"""

import sys
import json
import re
import html
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

# ============== CONFIG ==============

STORE_BASE_URL = "https://camilleelisa.myshopify.com"  # TODO: replace with your real store domain

ONLY_ACTIVE = True
ONLY_PUBLISHED = True

# Variants beyond this count get summarized (price range + possible option values)
# instead of being listed one by one, to avoid an unreadably long variant table.
MANY_VARIANTS_THRESHOLD = 12

# Target max size (characters) for a single Body chunk. Long Body (HTML) text gets
# split into multiple chunks of roughly this size; every chunk still repeats all
# the other product fields (Title, Variant Price, options, metafields, etc.)
BODY_CHUNK_MAX_CHARS = 900

# Split the final output into multiple smaller files of this many chunks each,
# so you can import them gradually instead of all at once (helps avoid hitting
# the embedding provider's rate limit during indexing).
BATCH_SIZE = 40

# Product-level fields that Shopify only fills on the first row of each Handle
# group; everything else in the group is blank and needs to be forward-filled.
PRODUCT_LEVEL_COLUMNS = [
    "Title", "Body (HTML)", "Vendor", "Product Category", "Type", "Tags",
    "Published", "Status", "SEO Title", "SEO Description",
    "Option1 Name", "Option2 Name", "Option3 Name",
    "Jewelry material (product.metafields.shopify.jewelry-material)",
    "Jewelry type (product.metafields.shopify.jewelry-type)",
    "Color (product.metafields.shopify.color-pattern)",
    "Ring size (product.metafields.shopify.ring-size)",
    "Target gender (product.metafields.shopify.target-gender)",
    "Necklace design (product.metafields.shopify.necklace-design)",
    "Bracelet design (product.metafields.shopify.bracelet-design)",
    "Earring design (product.metafields.shopify.earring-design)",
    "Ring design (product.metafields.shopify.ring-design)",
]

# Metafield columns to pass through as-is (just basic formatting, no validation),
# shown with their short label (the part before the parenthesis).
METAFIELD_COLUMNS = {
    "Jewelry material (product.metafields.shopify.jewelry-material)": "Jewelry material",
    "Jewelry type (product.metafields.shopify.jewelry-type)": "Jewelry type",
    "Color (product.metafields.shopify.color-pattern)": "Color",
    "Ring size (product.metafields.shopify.ring-size)": "Ring size",
    "Target gender (product.metafields.shopify.target-gender)": "Target gender",
    "Necklace design (product.metafields.shopify.necklace-design)": "Necklace design",
    "Bracelet design (product.metafields.shopify.bracelet-design)": "Bracelet design",
    "Earring design (product.metafields.shopify.earring-design)": "Earring design",
    "Ring design (product.metafields.shopify.ring-design)": "Ring design",
}

# ============== Helpers ==============


def clean_html_text(raw_html: str) -> str:
    """Convert Body (HTML) to clean plain text (tags stripped, entities decoded)."""
    if not raw_html or pd.isna(raw_html):
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for p in soup.find_all(["p", "li"]):
        p.append("\n")
    text = soup.get_text()
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def split_body_into_chunks(text: str, max_chars: int) -> list[str]:
    """Split long Body text into pieces of roughly max_chars, breaking on
    paragraph boundaries first, falling back to sentence boundaries for any
    single paragraph that's still too long on its own."""
    if not text:
        return [""]
    if len(text) <= max_chars:
        return [text]

    paragraphs = [p for p in text.split("\n") if p.strip()]
    chunks, current = [], ""

    def flush():
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for para in paragraphs:
        pieces = [para]
        if len(para) > max_chars:
            pieces = re.split(r"(?<=[.!?])\s+", para)

        for piece in pieces:
            if len(current) + len(piece) + 1 > max_chars and current:
                flush()
            current = f"{current} {piece}".strip() if current else piece

    flush()
    return chunks if chunks else [text]


def normalize_type(t: str) -> str:
    if not t or pd.isna(t):
        return ""
    return t.strip().title()


def format_metafield_value(raw: str) -> str:
    """Light formatting only: split the ';'-separated list, trim whitespace.
    No attempt to validate or fix the underlying values."""
    if not raw or pd.isna(raw):
        return ""
    parts = [p.strip() for p in raw.split(";") if p.strip()]
    return ", ".join(parts)


def load_and_forward_fill(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path, dtype=str)
    df[PRODUCT_LEVEL_COLUMNS] = df.groupby("Handle")[PRODUCT_LEVEL_COLUMNS].ffill()
    return df


def is_variant_row(row) -> bool:
    return pd.notna(row.get("Variant Price"))


def build_variant_list(group: pd.DataFrame) -> list[dict]:
    variants = []
    option_cols = [
        ("Option1 Name", "Option1 Value"),
        ("Option2 Name", "Option2 Value"),
        ("Option3 Name", "Option3 Value"),
    ]
    for _, row in group.iterrows():
        if not is_variant_row(row):
            continue
        options = {}
        for name_col, value_col in option_cols:
            name = row.get(name_col)
            value = row.get(value_col)
            if pd.notna(name) and pd.notna(value) and name.lower() != "title":
                clean_name = name.strip().rstrip(":：").strip()
                options[clean_name] = value.strip()
        variants.append({
            "options": options,
            "sku": row.get("Variant SKU") if pd.notna(row.get("Variant SKU")) else None,
            "price": float(row["Variant Price"]),
            "compare_at_price": (
                float(row["Variant Compare At Price"])
                if pd.notna(row.get("Variant Compare At Price")) else None
            ),
        })
    return variants


def build_metafields(first) -> dict:
    out = {}
    for col, label in METAFIELD_COLUMNS.items():
        val = format_metafield_value(first.get(col))
        if val:
            out[label] = val
    return out


def build_product_record(handle: str, group: pd.DataFrame) -> dict | None:
    first = group.iloc[0]

    if ONLY_ACTIVE and str(first.get("Status", "")).lower() != "active":
        return None
    if ONLY_PUBLISHED and str(first.get("Published", "")).lower() != "true":
        return None

    variants = build_variant_list(group)
    if not variants:
        return None

    prices = [v["price"] for v in variants]
    body_clean = clean_html_text(first.get("Body (HTML)", ""))
    images = group["Image Src"].dropna().tolist()

    record = {
        "Handle": handle,
        "Title": first.get("Title", "").strip() if pd.notna(first.get("Title")) else "",
        "URL": f"{STORE_BASE_URL}/products/{handle}",
        "Product Category": first.get("Product Category") if pd.notna(first.get("Product Category")) else "",
        "Type": normalize_type(first.get("Type")),
        "Tags": [t.strip() for t in first.get("Tags", "").split(",")] if pd.notna(first.get("Tags")) else [],
        "Vendor": first.get("Vendor") if pd.notna(first.get("Vendor")) else "",
        "SEO Description": first.get("SEO Description") if pd.notna(first.get("SEO Description")) else "",
        "metafields": build_metafields(first),
        "main_image": images[0] if images else None,
        "variants": variants,
        "price_min": min(prices),
        "price_max": max(prices),
        "variant_count": len(variants),
        "body_chunks": split_body_into_chunks(body_clean, BODY_CHUNK_MAX_CHARS),
    }
    return record


def format_variant_section(record: dict) -> list[str]:
    """Lines describing price/options, using CSV column terminology."""
    variants = record["variants"]
    lines = []

    if record["variant_count"] == 1 and not variants[0]["options"]:
        lines.append(f"Variant Price: CAD {variants[0]['price']:.2f}")
        if variants[0]["sku"]:
            lines.append(f"Variant SKU: {variants[0]['sku']}")
        return lines

    if record["variant_count"] <= MANY_VARIANTS_THRESHOLD:
        for v in variants:
            opt_str = ", ".join(f"{k}: {val}" for k, val in v["options"].items())
            line = f"{opt_str} -> Variant Price: CAD {v['price']:.2f}"
            if v["sku"]:
                line += f" (Variant SKU: {v['sku']})"
            lines.append(line)
        return lines

    # many variants -> summarize instead of listing every combination
    option_dims = {}
    for v in variants:
        for k, val in v["options"].items():
            option_dims.setdefault(k, set()).add(val)
    lines.append(
        f"Variant Price range: CAD {record['price_min']:.2f} - CAD {record['price_max']:.2f} "
        f"(across {record['variant_count']} option combinations)"
    )
    for k, vals in option_dims.items():
        sorted_vals = sorted(vals, key=lambda x: (len(x), x))
        lines.append(f"{k} possible values: {', '.join(sorted_vals)}")
    lines.append("Note: exact price for a specific combination should be confirmed on the live site.")
    return lines


def build_text_blocks(record: dict) -> list[str]:
    """One block per Body chunk; all other fields repeated identically."""
    header_lines = [f"Handle: {record['Handle']}", f"Title: {record['Title']}"]
    header_lines += format_variant_section(record)

    for label, val in record["metafields"].items():
        header_lines.append(f"{label}: {val}")

    if record["Type"]:
        header_lines.append(f"Type: {record['Type']}")
    if record["Product Category"]:
        header_lines.append(f"Product Category: {record['Product Category']}")
    if record["Tags"]:
        header_lines.append(f"Tags: {', '.join(record['Tags'])}")
    if record["Vendor"]:
        header_lines.append(f"Vendor: {record['Vendor']}")
    if record["main_image"]:
        header_lines.append(f"Image Src: {record['main_image']}")
    if record["SEO Description"]:
        header_lines.append(f"SEO Description: {record['SEO Description']}")
    header_lines.append(f"URL: {record['URL']}")

    header = "\n".join(header_lines)

    chunks = record["body_chunks"]
    total = len(chunks)
    blocks = []
    for i, body_part in enumerate(chunks, start=1):
        if not body_part:
            blocks.append(header)
            continue
        part_label = f"Body (HTML) [part {i}/{total}]:" if total > 1 else "Body (HTML):"
        blocks.append(f"{header}\n\n{part_label}\n{body_part}")
    return blocks


def main():
    if len(sys.argv) < 2:
        print("Usage: python clean_shopify_products.py <csv_path>")
        sys.exit(1)

    csv_path = sys.argv[1]
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)

    df = load_and_forward_fill(csv_path)
    handles = df["Handle"].unique()

    products = []
    skipped = []
    for handle in handles:
        group = df[df["Handle"] == handle]
        record = build_product_record(handle, group)
        if record is None:
            skipped.append(handle)
        else:
            products.append(record)

    all_chunk_records = []
    text_blocks = []
    review_flags = []
    split_count = 0

    for r in products:
        blocks = build_text_blocks(r)
        total_parts = len(blocks)
        if total_parts > 1:
            split_count += 1
        if r["variant_count"] > MANY_VARIANTS_THRESHOLD:
            review_flags.append({
                "Handle": r["Handle"], "Title": r["Title"], "variant_count": r["variant_count"],
                "reason": "Variant table auto-summarized instead of listed individually; worth a quick check."
            })

        for i, block in enumerate(blocks, start=1):
            text_blocks.append(block)
            all_chunk_records.append({
                "Handle": r["Handle"],
                "Title": r["Title"],
                "URL": r["URL"],
                "Type": r["Type"],
                "Product Category": r["Product Category"],
                "Tags": r["Tags"],
                "Vendor": r["Vendor"],
                "SEO Description": r["SEO Description"],
                "metafields": r["metafields"],
                "variants": r["variants"],
                "price_min": r["price_min"],
                "price_max": r["price_max"],
                "variant_count": r["variant_count"],
                "main_image": r["main_image"],
                "body_part_index": i,
                "body_part_total": total_parts,
                "body_text": r["body_chunks"][i - 1],
                "chunk_text": block,
            })

    (out_dir / "products_chunks.json").write_text(
        json.dumps(all_chunk_records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "products_kb.txt").write_text(
        "\n\n=====\n\n".join(text_blocks), encoding="utf-8"
    )
    (out_dir / "review_flags.json").write_text(
        json.dumps(review_flags, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Also write smaller batch files so you can import gradually instead of
    # all at once, to avoid hitting the embedding provider's rate limit.
    batch_dir = out_dir / "batches"
    batch_dir.mkdir(exist_ok=True)
    num_batches = 0
    for i in range(0, len(text_blocks), BATCH_SIZE):
        batch = text_blocks[i: i + BATCH_SIZE]
        num_batches += 1
        (batch_dir / f"products_batch_{num_batches:03d}.txt").write_text(
            "\n\n=====\n\n".join(batch), encoding="utf-8"
        )

    print(f"Products read: {len(handles)}")
    print(f"Products generated: {len(products)}")
    print(f"Skipped (draft/unpublished/no price): {len(skipped)} -> {skipped}")
    print(f"Products split into multiple Body chunks: {split_count}")
    print(f"Total chunk records / text blocks: {len(text_blocks)}")
    print(f"Flagged for review: {len(review_flags)}")
    print(f"Written {num_batches} batch files of up to {BATCH_SIZE} chunks each, in {batch_dir}")
    print(f"Output dir: {out_dir.resolve()}")


if __name__ == "__main__":
    main()