"""Turn a saved collect-avito CSV into one flat table. Does not collect data."""
import argparse
import csv
import hashlib
import json
from pathlib import Path


# Keep source text intact. Characteristics get their own columns below.
SOURCE_COLUMNS = [
    "id", "title", "salary_raw", "employer_name_observed",
    "location_observed", "source_url", "observed_at", "description",
    "employer_profile_url", "date_raw", "views_total", "views_today",
    "full_parameters_read", "analysis_status", "export_eligibility",
]


def flatten_export(source, destination):
    """Write a new CSV: one vacancy per row; all accepted segments retained."""
    source, destination = Path(source), Path(destination)
    with source.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("Source export is empty")
    ids = [row["id"] for row in rows]
    if any(not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("Expected nonempty, unique vacancy IDs")

    result = []
    characteristics = []
    status_columns = []
    for row in rows:
        accepted = [
            item for item in json.loads(row["observations_json"])
            if item["selection_role"] in ("main", "reserve")
            and item["status"].startswith("VALIDATED")
        ]
        if not accepted:
            raise ValueError(f"No accepted segment for vacancy {row['id']}")
        flat = {name: row[name] for name in SOURCE_COLUMNS}
        for column, key in [("city", "city"), ("profession", "profession"),
                            ("segments", "slice_id")]:
            flat[column] = "; ".join(dict.fromkeys(item[key] for item in accepted))
        for field in json.loads(row["fields_json"]):
            # Labels differ in normal/nonbreaking spaces. Values stay unchanged.
            name = " ".join(field["name"].split())
            if not name or name in flat:
                raise ValueError(f"Duplicate/empty characteristic {name!r}: {row['id']}")
            flat[name] = field["value"]
            if name not in characteristics:
                characteristics.append(name)
        for name, value in json.loads(row["read_status_json"]).items():
            column = f"read_status_{name}"
            if column in flat:
                raise ValueError(f"Column collision: {column}")
            flat[column] = value
            if column not in status_columns:
                status_columns.append(column)
        flat["read_limitations"] = "\n".join(json.loads(row["read_limitations_json"]))
        result.append(flat)

    columns = ["id", "city", "profession", "segments", *SOURCE_COLUMNS[1:],
               *characteristics, *status_columns, "read_limitations"]
    if len(columns) != len(set(columns)):
        raise ValueError("Output column names collide")
    # Exclusive creation protects completed exports from accidental rewriting.
    with destination.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(result)
    return {
        "file": str(destination), "rows": len(result), "columns": len(columns),
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "source": str(source),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Saved vacancies.csv")
    parser.add_argument("destination", type=Path, help="New flat CSV; must not exist")
    args = parser.parse_args()
    print(json.dumps(flatten_export(args.source, args.destination),
                     ensure_ascii=False, indent=2))
