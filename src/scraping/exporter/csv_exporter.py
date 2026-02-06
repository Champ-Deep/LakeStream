import csv
import json
from pathlib import Path


def export_to_csv(records: list[dict], output_path: str) -> int:
    """Export scraped data records to a CSV file."""
    if not records:
        return 0

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = ["domain", "data_type", "url", "title", "metadata"]

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()

        for record in records:
            row = {**record}
            if isinstance(row.get("metadata"), dict):
                row["metadata"] = json.dumps(row["metadata"])
            writer.writerow(row)

    return len(records)
