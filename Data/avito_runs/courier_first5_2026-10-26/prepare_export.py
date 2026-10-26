"""Reproduce this reviewed five-card export offline; never overwrite outputs."""
import csv
import json
import re
from pathlib import Path

RUN = Path(__file__).resolve().parent


def save_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def number(text):
    match = re.search(r"\d+", (text or "").replace("\xa0", ""))
    return match.group() if match else ""


def main():
    queue = json.loads((RUN / "evidence/queue.json").read_text())
    assert len(queue) == len({item["id"] for item in queue}) == 5
    rows, fields = [], []
    for item in queue:
        identity = item["id"]
        raw = json.loads((RUN / f"raw/{identity}.json").read_text())
        assert raw["state"] == "DETAIL" and raw["id"] == identity
        assert raw["description"] and raw["params_blocks"]
        snapshot = (RUN / f"evidence/{identity}.snapshot.txt").read_text()
        location_section = snapshot.split('- heading "Расположение" [level=2]\n', 1)[1]
        location_section = location_section.split('- heading "Описание"', 1)[0]
        location = " ".join(json.loads(line[8:]) for line in location_section.splitlines()
                            if line.startswith('- text: "'))
        assert location.startswith("Москва,")
        public = json.loads((RUN / f"cards/{identity}.public.json").read_text())
        row = dict(id=identity, city="Москва", profession="Курьер",
                   segments="courier_moscow_first5_2026-10-26", title=raw["title"],
                   salary_raw=raw["salary_raw"] or "",
                   employer_name_observed="; ".join(dict.fromkeys(raw["employer_raw"])),
                   location_observed=location, source_url=raw["source_url"],
                   observed_at=raw["observed_at"], description=raw["description"],
                   employer_profile_url=public["employer_profile_url"] or "",
                   date_raw=raw["date_raw"] or "", views_total=number(raw["views_raw"]),
                   views_today=number(raw["today_views_raw"]), full_parameters_read="True",
                   analysis_status="validated", export_eligibility="included")
        for field in raw["fields"]:
            name = " ".join(field["name"].split())
            if name in row:
                raise ValueError(f"Repeated parameter: {identity}:{name}")
            row[name] = field["value"]
            if name not in fields:
                fields.append(name)
        row.update(read_status_parameters="observed", read_status_description="observed",
                   read_status_employer="observed", read_status_location="observed",
                   read_limitations="rendered_DOM_only; employer_profile_not_opened; screenshot_viewport_only",
                   listing_rank=item["rank"], listing_promoted=str(item["promoted"]),
                   enrichment_status="not_run", enriched_fields="", enrichment_version="")
        save_json(RUN / f"cards/{identity}.json", {
            "row": row, "review": {
                "status": "validated", "method": "DOM + full snapshot + viewport screenshot; offline review",
                "parameters": "All rendered Conditions and Requirements blocks reviewed; no collapsed parameter control observed",
                "location_source": f"evidence/{identity}.snapshot.txt#Расположение",
                "raw": f"raw/{identity}.json", "evidence": f"evidence/{identity}.jpg",
                "generic_values_tokens_used": False, "description_enrichment": "not_run"}})
        rows.append(row)
    base = list(rows[0])
    # Preserve every characteristic, including those appearing only in later cards.
    columns = [key for key in base if key not in fields]
    columns[18:18] = fields
    assert len(columns) == len(set(columns))
    with (RUN / "exports/vacancies_flat.csv").open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} reviewed vacancies, {len(columns)} columns")


if __name__ == "__main__":
    main()
