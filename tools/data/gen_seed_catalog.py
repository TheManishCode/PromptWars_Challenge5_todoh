"""Extend the synthetic seed catalogue so a wedding in Jaipur, a birthday in Bengaluru or a community mela in Pune finds vendors, not an empty plan.

Deterministic (no randomness beyond a hash), idempotent, and it never changes a record that is already in the files: the original records (and their ids and dates) stay as they
are, new ones are added under new ids. Everything here is SYNTHETIC: names and prices are invented, the files say so, and nothing may present them as real businesses.
    python tools/data/gen_seed_catalog.py          # writes eventmate/data/catalog_vendors.json and catalog_venues.json (seed_*.json stay the small, fixed test fixture)
"""
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "eventmate" / "data"
START, END = date(2026, 11, 1), date(2027, 12, 31)

# city -> (price index, venue names). The index scales every fee: a metro costs more than a smaller city.
CITIES = {
    "Pune": (1.00, ["Riverside Gardens", "Deccan Courtyard", "Sahyadri Banquets"]),
    "Mumbai": (1.35, ["Marine Lines Hall", "Bandra Terrace", "Juhu Lawns"]),
    "Delhi": (1.25, ["Lutyens Court", "Dwarka Grand", "Chhatarpur Farms"]),
    "Bengaluru": (1.15, ["Indiranagar Atrium", "Whitefield Pavilion", "Nandi Foothills Lawn"]),
    "Hyderabad": (1.05, ["Banjara Hall", "Gachibowli Gardens", "Nizam Courtyard"]),
    "Chennai": (1.00, ["Adyar Hall", "ECR Seaside Lawn", "T Nagar Grand"]),
    "Jaipur": (0.95, ["Amer View Haveli", "Pink City Palace Lawn", "Vaishali Banquets"]),
    "Kolkata": (0.90, ["Park Street Hall", "Salt Lake Pavilion", "Alipore Garden"]),
    "Ahmedabad": (0.90, ["Sabarmati Lawns", "Navrangpura Hall", "SG Highway Banquets"]),
}
CATERERS = [  # (suffix, name, plate price, dietary, min, max)
    ("veg", "Satvik Kitchens", 520, ["vegetarian", "jain", "vegan"], 20, 800),
    ("mix", "Spice Route Catering", 700, ["vegetarian", "non_vegetarian", "halal"], 30, 1000),
    ("fest", "Festive Feasts", 450, ["vegetarian", "jain"], 40, 1500),
    ("small", "Home Table Co", 380, ["vegetarian", "vegan", "non_vegetarian"], 10, 120),
]
PHOTOGRAPHERS = [("candid", "Moment Makers", 18000), ("film", "Reel Stories", 32000), ("lite", "Snap Squad", 9000)]
VENUES = [  # (setting, capacity, base fee)
    ("indoor", 80, 40000), ("indoor", 320, 100000), ("outdoor", 600, 160000),
]


def dates(key: str) -> list:
    out, d = [], START
    while d <= END:
        if d.weekday() >= 4:  # Friday to Sunday
            h = int(hashlib.sha256(f"{key}{d}".encode()).hexdigest()[:4], 16)
            if h % 100 >= 8:  # about one weekend day in twelve is already taken
                out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def load(name):
    return json.loads((DATA / name).read_text())  # always the small seed files: the full catalogue is rebuilt from them, never edited by hand


def main() -> None:
    vendors, venues = load("seed_vendors.json"), load("seed_venues.json")
    have = {r["id"] for r in vendors["records"] + venues["records"]}
    new_v, new_ven = [], []
    for city, (idx, names) in CITIES.items():
        slug = city.lower()
        for (suffix, name, plate, diet, lo, hi) in CATERERS:
            rid = f"cat-{slug}-{suffix}"
            if rid not in have:
                new_v.append({"id": rid, "name": f"{name} {city}", "category": "caterer", "city": city, "price_per_plate": round(plate * idx / 10) * 10, "min_guests": lo,
                              "max_guests": hi, "dietary_options": diet, "rating": round(3.9 + (int(hashlib.sha256(rid.encode()).hexdigest()[:2], 16) % 9) / 10, 1),
                              "available_dates": dates(rid), "booking_status": "available", "source": "database:seed_vendors.json"})
        for (suffix, name, fee) in PHOTOGRAPHERS:
            rid = f"pho-{slug}-{suffix}"
            if rid not in have:
                new_v.append({"id": rid, "name": f"{name} {city}", "category": "photographer", "city": city, "flat_fee": round(fee * idx / 500) * 500,
                              "rating": round(4.0 + (int(hashlib.sha256(rid.encode()).hexdigest()[:2], 16) % 8) / 10, 1), "available_dates": dates(rid),
                              "booking_status": "available", "source": "database:seed_vendors.json"})
        for i, (vname, (setting, cap, fee)) in enumerate(zip(names, VENUES)):
            rid = f"ven-{slug}-{i + 1}"
            if rid not in have:
                new_ven.append({"id": rid, "name": f"{vname}", "city": city, "setting": setting, "capacity": cap, "flat_fee": round(fee * idx / 1000) * 1000,
                                "rating": round(4.0 + (int(hashlib.sha256(rid.encode()).hexdigest()[:2], 16) % 8) / 10, 1), "available_dates": dates(rid),
                                "booking_status": "available", "source": "database:seed_venues.json"})
    vendors["records"] += new_v
    venues["records"] += new_ven
    for name, doc in (("catalog_vendors.json", vendors), ("catalog_venues.json", venues)):
        doc["note"] = "Synthetic demonstration catalogue (the seed records plus generated ones). Not real businesses or prices."
        (DATA / name).write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    print(f"added {len(new_v)} vendors and {len(new_ven)} venues")


if __name__ == "__main__":
    main()
