"""Load an explicitly selected, hash-pinned project dataset. Standard library only."""
from pathlib import Path
import csv
import hashlib
import json


def load_dataset(passport, root=None):
    root = Path(root or Path(__file__).resolve().parents[1]).resolve()
    passport_path = (root / passport).resolve()
    passport_path.relative_to(root)
    meta = json.loads(passport_path.read_text(encoding="utf-8"))
    tables = {}
    for name in ("vacancies", "segments"):
        spec = meta["files"][name]
        path = (root / spec["path"]).resolve()
        path.relative_to(root)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != spec["sha256"]:
            raise ValueError(f"Dataset changed: {spec['path']}; choose a new passport, do not overwrite its hash")
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != spec["columns"]:
                raise ValueError(f"Field set changed: {spec['path']}")
            tables[name] = list(reader)
        if len(tables[name]) != spec["rows"]:
            raise ValueError(f"Row count changed: {spec['path']}")
    rows = tables["vacancies"]
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)) or len(ids) != meta["coverage"]["unique_main_vacancies"]:
        raise ValueError("Unexpected unique vacancy count")
    memberships = []
    for row in rows:
        observations = json.loads(row["observations_json"])
        for observation in observations:
            # Accepted reserve replacements belong to the main sample; a technical
            # pilot or an unvisited queue entry does not become a training example.
            if observation["selection_role"] in ("main", "reserve") and observation["status"].startswith("VALIDATED"):
                memberships.append({**observation, "id": row["id"]})
    if len(memberships) != meta["coverage"]["accepted_memberships"]:
        raise ValueError("Unexpected accepted membership count")
    if len({(x['slice_id'], x['id']) for x in memberships}) != len(memberships):
        raise ValueError("Duplicate vacancy within a slice")
    for segment in tables["segments"]:
        actual = sum(x["slice_id"] == segment["slice_id"] for x in memberships)
        if actual != int(segment["saved_n"]):
            raise ValueError(f"Slice count mismatch: {segment['slice_id']}")
    return rows, memberships, meta


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("passport", help="Explicit passport path relative to the repository root")
    args = parser.parse_args()
    rows, memberships, meta = load_dataset(args.passport)
    print(json.dumps({"dataset_id": meta["dataset_id"], "vacancies": len(rows),
                      "accepted_memberships": len(memberships),
                      "slices": len({x['slice_id'] for x in memberships}),
                      "training_performed": False}, ensure_ascii=False, indent=2))
