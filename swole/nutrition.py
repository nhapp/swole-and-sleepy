"""Nutrition import. MyFitnessPal CSV today; the MFP API slots in here once access is granted.

    git pull
    SWOLE_PASSPHRASE=... python -m swole.nutrition ~/Downloads/Nutrition-Summary.csv
    git add site && git commit -m "Import food log" && git push

The CSV is read into the encrypted data file and never committed in plain text.
Every provider returns the same daily shape, so the dashboard doesn't care where it came from:
    {date: {"kcal", "protein_g", "carbs_g", "fat_g", "source"}}
"""

import csv
import sys
from datetime import date, datetime

# Header names vary by export; match loosely and case-insensitively.
COLUMNS = {
    "kcal": ("calories", "energy (kcal)", "kcal"),
    "protein_g": ("protein (g)", "protein"),
    "carbs_g": ("carbohydrates (g)", "carbs (g)", "carbohydrates", "carbs"),
    "fat_g": ("fat (g)", "total fat (g)", "fat"),
}
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y")


def _col(header: list[str], names) -> int | None:
    low = [h.strip().lower() for h in header]
    for n in names:
        if n in low:
            return low.index(n)
    return None


def _date(text: str) -> str:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"Unrecognised date {text!r}")


def _num(text: str) -> float:
    text = (text or "").strip().replace(",", "")
    return float(text) if text else 0.0


def parse_mfp_csv(path: str) -> dict[str, dict]:
    """Sum an MFP 'Nutrition Summary' export (one row per meal) into daily totals."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if not rows:
        return {}
    header = rows[0]
    di = _col(header, ("date",))
    idx = {k: _col(header, names) for k, names in COLUMNS.items()}
    if di is None or idx["kcal"] is None:
        raise ValueError(f"Expected Date and Calories columns; found: {', '.join(header)}")
    out: dict[str, dict] = {}
    for r in rows[1:]:
        if not r or not r[di].strip():
            continue
        day = out.setdefault(_date(r[di]), {"kcal": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0})
        for k, i in idx.items():
            if i is not None and i < len(r):
                day[k] += _num(r[i])
    return {d: {**{k: round(v) for k, v in t.items()}, "source": "mfp-csv"} for d, t in out.items()}


def main(argv: list[str]) -> int:
    from . import crypto, run

    if len(argv) != 1:
        print(__doc__)
        return 1
    days = parse_mfp_csv(argv[0])
    cfg, pw = run.load_config(), run.passphrase()
    store = crypto.read_file(run.DATA_PATH, pw, default={})
    store.setdefault("raw", {}).setdefault("nutrition", {}).update(days)
    meta = store.get("meta", {})
    status = meta.get("sources", {})
    status["nutrition"] = {"ok": True, "error": None, "last_success": datetime.now().astimezone().isoformat()}
    today = date.fromisoformat(meta["today"]) if meta.get("today") else date.today()
    for k in ("days", "activities", "body"):
        store["raw"].setdefault(k, {})
    crypto.write_file(run.DATA_PATH, run.build(store, cfg, status, today), pw)
    span = f"{min(days)} to {max(days)}" if days else "no rows"
    print(f"Imported {len(days)} days ({span}) into {run.DATA_PATH}. Commit and push site/data.enc.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
