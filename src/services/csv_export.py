"""Shared CSV export helper for flattening ScrapedData records to CSV.

Both the per-job download (/download/job/{job_id}) and the filtered
all-results download (/download/all) web routes need to flatten the same
30-field, JSONB-backed ScrapedData shape into a flat CSV row. This module
holds that single definition so the two routes stay in sync.
"""

import csv
import io

from src.models.scraped_data import ScrapedData

# Column order for the exported CSV. Covers fields from every data_type
# (article, contact, tech_stack, resource, pricing, ...) — most rows will
# leave most columns blank since each data_type only populates a subset.
CSV_FIELDNAMES = [
    "domain",
    "data_type",
    "url",
    "title",
    "published_date",
    "scraped_at",
    "author",
    "excerpt",
    "word_count",
    "categories",
    "content",
    "first_name",
    "last_name",
    "job_title",
    "email",
    "phone",
    "linkedin_url",
    "total_articles",
    "platform",
    "frameworks",
    "js_libraries",
    "analytics",
    "resource_type",
    "description",
    "download_url",
    "plan_name",
    "price",
    "billing_cycle",
    "features",
    "has_free_trial",
    "cta_text",
]


def _join_list(meta: dict, key: str) -> str:
    """Join a list-valued metadata field into a single semicolon-separated string."""
    val = meta.get(key, [])
    return "; ".join(val) if isinstance(val, list) else ""


def _row_for_item(item: ScrapedData) -> dict:
    """Flatten a single ScrapedData record into a dict matching CSV_FIELDNAMES."""
    meta = item.metadata or {}
    return {
        "domain": item.domain,
        "data_type": item.data_type,
        "url": item.url or "",
        "title": item.title or "",
        "published_date": str(item.published_date) if item.published_date else "",
        "scraped_at": item.scraped_at.isoformat() if item.scraped_at else "",
        "author": meta.get("author", ""),
        "excerpt": meta.get("excerpt", ""),
        "word_count": meta.get("word_count", ""),
        "categories": _join_list(meta, "categories"),
        "content": meta.get("content", ""),
        "first_name": meta.get("first_name", ""),
        "last_name": meta.get("last_name", ""),
        "job_title": meta.get("job_title", ""),
        "email": meta.get("email", ""),
        "phone": meta.get("phone", ""),
        "linkedin_url": meta.get("linkedin_url", ""),
        "total_articles": meta.get("total_articles", ""),
        "platform": meta.get("platform", ""),
        "frameworks": _join_list(meta, "frameworks"),
        "js_libraries": _join_list(meta, "js_libraries"),
        "analytics": _join_list(meta, "analytics"),
        "resource_type": meta.get("resource_type", ""),
        "description": meta.get("description", ""),
        "download_url": meta.get("download_url", ""),
        "plan_name": meta.get("plan_name", ""),
        "price": meta.get("price", ""),
        "billing_cycle": meta.get("billing_cycle", ""),
        "features": _join_list(meta, "features"),
        "has_free_trial": meta.get("has_free_trial", ""),
        "cta_text": meta.get("cta_text", ""),
    }


def scraped_data_to_csv(data: list[ScrapedData]) -> str:
    """Flatten a list of ScrapedData records into a CSV string (with header row)."""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDNAMES)
    writer.writeheader()
    for item in data:
        writer.writerow(_row_for_item(item))
    output.seek(0)
    return output.getvalue()
