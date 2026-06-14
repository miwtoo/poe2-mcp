"""
Pre-patch trade API payload shape verification.
Tests A-F before any code changes, then imports and patches.

Never prints POESESSID. Logs only length.
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

# Ensure src is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Configuration
LEAGUE = "Standard"
LIMIT = 5
ENV_PATH = Path(__file__).parent.parent / ".env"

# Load .env manually — avoids triggering full server init
def load_env():
    poesessid = None
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line.startswith("POESESSID="):
                poesessid = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not poesessid:
        print("FATAL: No POESESSID found in .env")
        sys.exit(1)
    print(f"POESESSID loaded (len={len(poesessid)})")
    return poesessid

POESESSID = load_env()

# Monkey-patch settings so TradeAPI can init without full mcp init
import types as pytypes
from src.config import settings
settings.POESESSID = POESESSID
settings.ENABLE_TRADE_INTEGRATION = True

from src.api.trade_api import TradeAPI


async def run_test(label: str, query_payload: dict) -> dict:
    """Run single trade search and return summary."""
    api = TradeAPI()
    try:
        items = await api.search_items(LEAGUE, query_payload, limit=LIMIT)
        count = len(items)
        first_name = ""
        if items:
            first_name = items[0].get("name", "") or ""
            first_price = items[0].get("price", {})
        else:
            first_price = {}
        return {
            "label": label,
            "count": count,
            "non_empty": count > 0,
            "first_name": first_name,
            "first_price": first_price,
            "error": None,
        }
    except Exception as e:
        return {
            "label": label,
            "count": 0,
            "non_empty": False,
            "first_name": "",
            "first_price": {},
            "error": str(e),
        }
    finally:
        await api.close()


def print_result(r: dict):
    status = "PASS" if r["non_empty"] else "FAIL"
    err = f" | ERROR: {r['error']}" if r["error"] else ""
    print(f"  [{status}] {r['label']}: count={r['count']}{err}")
    if r["first_name"]:
        print(f"         first_name={r['first_name']!r}")
    if r["first_price"]:
        print(f"         first_price={json.dumps(r['first_price'])}")


async def main():
    results = {}

    # === A: Baseline category weapon.bow online ===
    print("\n=== A: Baseline category weapon.bow (online) ===")
    r = await run_test("A-baseline-weapon-bow", {
        "status": "online",
        "item_filters": {
            "type_filters": {"filters": {"category": {"option": "weapon.bow"}}}
        },
    })
    print_result(r)
    results["A"] = r

    if r["non_empty"]:
        first_name = r.get("first_name", "")
        print(f"    (Extracted name for D/E: {first_name!r})")
    else:
        print("    (Cannot extract name — baseline returned empty)")

    # === B: Correct price shape ===
    print("\n=== B: Correct price shape (option:exalted, min:0, max:1000) ===")
    r = await run_test("B-correct-price-shape", {
        "status": "online",
        "item_filters": {
            "type_filters": {"filters": {"category": {"option": "weapon.bow"}}}
        },
        "trade_filters": {
            "filters": {
                "price": {
                    "option": "exalted",
                    "min": 0,
                    "max": 1000,
                }
            }
        },
    })
    print_result(r)
    results["B"] = r

    # === C: Current wrong price shape ===
    print("\n=== C: Current/wrong price shape (option wrapping object) ===")
    r = await run_test("C-wrong-price-shape", {
        "status": "online",
        "item_filters": {
            "type_filters": {"filters": {"category": {"option": "weapon.bow"}}}
        },
        "trade_filters": {
            "filters": {
                "price": {
                    "option": {
                        "max": 1000,
                        "currency": "exalted",
                    }
                }
            }
        },
    })
    print_result(r)
    results["C"] = r

    # === D: Name as plain string ===
    print("\n=== D: Name as plain string ===")
    known_name = ""
    if results["A"]["non_empty"]:
        known_name = results["A"]["first_name"]
    if known_name:
        r = await run_test("D-name-plain-string", {
            "status": "online",
            "name": known_name,
        })
    else:
        # Fallback: try a known unique name
        known_name = "The Perfect Paradox"
        r = await run_test("D-name-plain-string", {
            "status": "online",
            "name": known_name,
        })
    print_result(r)
    results["D"] = r

    # === E: Name as object shape {option: name} ===
    print("\n=== E: Name as object shape {option: name} ===")
    known_name_e = results["A"]["first_name"] if results["A"]["non_empty"] and results["A"]["first_name"] else "The Perfect Paradox"
    r = await run_test("E-name-object-shape", {
        "status": "online",
        "name": {"option": known_name_e},
    })
    print_result(r)
    results["E"] = r

    # === F: item_filters raw category ===
    print("\n=== F: item_filters raw (category via type_filters) ===")
    r = await run_test("F-item-filters-category", {
        "status": "online",
        "item_filters": {
            "type_filters": {
                "filters": {
                    "category": {"option": "weapon.bow"}
                }
            }
        },
    })
    print_result(r)
    results["F"] = r

    # === Summary ===
    print("\n" + "=" * 60)
    print("PRE-TEST SUMMARY (A-F)")
    print("=" * 60)
    for k in ["A", "B", "C", "D", "E", "F"]:
        r = results.get(k, {})
        status = "PASS" if r.get("non_empty") else "FAIL" if not r.get("error") else f"ERROR({r['error']})"
        print(f"  {k}: {status} (count={r.get('count', 0)})")

    print("\n=== KEY FINDINGS ===")
    a_ok = results["A"].get("non_empty", False)
    b_ok = results["B"].get("non_empty", False)
    c_ok = results["C"].get("non_empty", False)
    d_ok = results["D"].get("non_empty", False)
    e_ok = results["E"].get("non_empty", False)
    f_ok = results["F"].get("non_empty", False)

    print(f"  A (baseline):              {'WORKS' if a_ok else 'BROKEN'}")
    print(f"  B (correct price):         {'WORKS' if b_ok else 'BROKEN'}")
    print(f"  C (wrong price):           {'WORKS' if c_ok else 'BROKEN'}")
    print(f"  D (name plain string):     {'WORKS' if d_ok else 'BROKEN'}")
    print(f"  E (name object shape):     {'WORKS' if e_ok else 'BROKEN'}")
    print(f"  F (item_filters category): {'WORKS' if f_ok else 'BROKEN'}")

    print(f"\n  => Correct price shape {'WORKS' if b_ok and not c_ok else 'INDETERMINATE'}")
    print(f"  => Plain string name {'WORKS' if d_ok else 'BROKEN'}")
    print(f"  => Object name {'WORKS' if e_ok else 'BROKEN'}")
    print(f"  => item_filters {'WORKS' if f_ok else 'BROKEN'}")


if __name__ == "__main__":
    asyncio.run(main())
