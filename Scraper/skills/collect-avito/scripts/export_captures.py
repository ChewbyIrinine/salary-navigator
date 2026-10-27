"""Export reviewed raw captures to a new flat CSV; never navigates or overwrites."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

from check_capture import check


def export(raw_dir, review_path, output):
    reviews = json.loads(Path(review_path).read_text(encoding="utf-8"))
    if not isinstance(reviews, dict) or not reviews:
        raise ValueError("Нужен непустой review.json: объект ID -> проверенные условия отбора.")
    rows = []
    for identity, review in reviews.items():
        if not identity.isdecimal() or not isinstance(review, dict):
            raise ValueError("Ожидается числовой ID и объект проверки.")
        if review.get("reviewed") is not True or not str(review.get("city", "")).strip():
            raise ValueError(f"Подтвердите карточку и город размещения: {identity}")
        raw = json.loads((Path(raw_dir) / f"{identity}.json").read_text(encoding="utf-8"))
        if not check(raw, identity)["structural_check_passed"]:
            raise ValueError(f"Не прошла проверка структуры/ID: {identity}")
        locations = raw.get("location_raw", [])
        if not locations or not all(isinstance(x, str) and x.strip() for x in locations):
            raise ValueError(f"Расположение не прочитано: {identity}; проверьте видимый интерфейс до экспорта.")
        row = {key: raw.get(key) or "" for key in (
            "id", "title", "source_url", "observed_at", "salary_raw", "description", "date_raw")}
        for key in ("city", "profession", "segments", "listing_rank", "listing_promoted"):
            row[key] = review.get(key, "")
        row.update(location_observed="; ".join(dict.fromkeys(locations)),
                   employer_name_observed="; ".join(dict.fromkeys(raw.get("employer_raw", []))),
                   analysis_status="validated", export_eligibility="included",
                   enrichment_status="not_run", enriched_fields="", enrichment_version="",
                   full_parameters_read=review.get("full_parameters_read", False),
                   read_limitations="; ".join(raw.get("read_limitations", [])))
        for field in raw["fields"]:
            name = " ".join(field["name"].split())
            if not name or name in row or name.startswith("read_status_"):
                raise ValueError(f"Конфликт колонок: {identity}")
            row[name] = field["value"]
        for key, value in raw.get("read_status", {}).items():
            row[f"read_status_{key}"] = value
        rows.append(row)
    columns = list(dict.fromkeys(key for row in rows for key in row))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return {"rows": len(rows), "columns": len(columns),
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(args.raw_dir, args.review, args.output), ensure_ascii=False))
