#!/usr/bin/env python3
"""
Goodreads CSV -> Zotero Import Script
=====================================
Imports your Goodreads library export into Zotero, creating:
  - Book items with metadata (title, author, ISBN, publisher, pages, year)
  - Collections for each Goodreads shelf
  - Tags for reading status, rating, and shelves
  - Goodreads metadata in the Extra field for future sync reference

Prerequisites:
  pip install pyzotero --break-system-packages

Usage:
  1. Get your Zotero API key:  https://www.zotero.org/settings/keys/new
     - Check "Allow library access" and "Allow write access"
  2. Get your User ID:         https://www.zotero.org/settings/keys
     - Shown at the top: "Your userID for use in API calls is XXXXXXX"
  3. Run:
     python goodreads_to_zotero.py --csv goodreads_library_export.csv \
                                    --library-id YOUR_USER_ID \
                                    --api-key YOUR_API_KEY

Options:
  --dry-run         Preview what would be imported without writing to Zotero
  --batch-size N    Items per API call (default: 25, max 50)
  --delay N         Seconds between API calls (default: 1)
  --shelf-filter S  Only import books on this shelf (e.g. "read", "currently-reading")
"""

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

try:
    from pyzotero import zotero
except ImportError:
    print("ERROR: pyzotero not installed. Run: pip install pyzotero --break-system-packages")
    sys.exit(1)


# -- Goodreads Shelf -> Zotero Mapping -----------------------------------------

# Status shelves become Zotero collections (folders in the left pane).
# Thematic shelves become tags for filtering/search.
STATUS_SHELVES = {
    "read", "currently-reading", "to-read", "want-to-read-soon", "dnf-paused"
}

# Reading-mode shelves get a "mode:" prefix to distinguish from thematic tags.
READING_MODE_SHELVES = {
    "immersion-reading", "practice-systems-reading", "study-reading"
}


def shelf_tag(shelf: str) -> str:
    """Return the tag name for a thematic shelf, prefixing reading-mode shelves."""
    if shelf in READING_MODE_SHELVES:
        return f"mode:{shelf}"
    return shelf


# Map Goodreads rating (1-5) to a tag
def rating_tag(rating: int) -> str | None:
    if rating and rating > 0:
        return f"rating:{rating}/5"
    return None


# -- CSV Parsing ---------------------------------------------------------------

def clean_isbn(raw: str) -> str:
    """Strip the =\"...\" wrapper Goodreads puts around ISBNs."""
    return raw.strip().replace('="', '').replace('"', '').strip()


def parse_shelves(raw: str) -> list[str]:
    """Parse comma-separated shelf string into clean list."""
    return [s.strip() for s in raw.split(",") if s.strip()]


def parse_goodreads_csv(csv_path: str, shelf_filter: str = None) -> list[dict]:
    """Parse Goodreads CSV into normalized book dicts."""
    books = []
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            exclusive_shelf = row.get("Exclusive Shelf", "").strip()

            # Apply shelf filter if specified
            if shelf_filter and exclusive_shelf != shelf_filter:
                all_shelves = parse_shelves(row.get("Bookshelves", ""))
                if shelf_filter not in all_shelves:
                    continue

            isbn13 = clean_isbn(row.get("ISBN13", ""))
            isbn10 = clean_isbn(row.get("ISBN", ""))

            # Parse author into first/last
            author_lf = row.get("Author l-f", "").strip()
            if author_lf:
                parts = [p.strip() for p in author_lf.split(",", 1)]
                last_name = parts[0] if parts else ""
                first_name = parts[1] if len(parts) > 1 else ""
            else:
                # Fallback: split "First Last" 
                author = row.get("Author", "").strip()
                name_parts = author.rsplit(" ", 1)
                first_name = name_parts[0] if len(name_parts) > 1 else ""
                last_name = name_parts[-1]

            # Parse additional authors
            additional_authors = []
            add_auth_raw = row.get("Additional Authors", "").strip()
            if add_auth_raw:
                for auth in add_auth_raw.split(","):
                    auth = auth.strip()
                    if auth:
                        parts = auth.rsplit(" ", 1)
                        additional_authors.append({
                            "first": parts[0] if len(parts) > 1 else "",
                            "last": parts[-1]
                        })

            shelves = parse_shelves(row.get("Bookshelves", ""))
            thematic_shelves = [s for s in shelves if s not in STATUS_SHELVES]

            book = {
                "goodreads_id": row.get("Book Id", "").strip(),
                "title": row.get("Title", "").strip(),
                "first_name": first_name,
                "last_name": last_name,
                "additional_authors": additional_authors,
                "isbn13": isbn13,
                "isbn10": isbn10,
                "publisher": row.get("Publisher", "").strip(),
                "binding": row.get("Binding", "").strip(),
                "num_pages": row.get("Number of Pages", "").strip(),
                "year_published": row.get("Year Published", "").strip(),
                "original_year": row.get("Original Publication Year", "").strip(),
                "my_rating": int(row.get("My Rating", "0") or "0"),
                "avg_rating": row.get("Average Rating", "").strip(),
                "date_read": row.get("Date Read", "").strip(),
                "date_added": row.get("Date Added", "").strip(),
                "exclusive_shelf": exclusive_shelf,
                "thematic_shelves": thematic_shelves,
                "all_shelves": shelves,
                "read_count": row.get("Read Count", "0").strip(),
            }
            books.append(book)

    return books


# -- Zotero Item Construction -------------------------------------------------

def build_zotero_item(
    template: dict,
    book: dict,
    collection_map: dict[str, str]
) -> dict:
    """Build a Zotero book item from a parsed Goodreads book dict."""
    item = template.copy()

    item["itemType"] = "book"
    item["title"] = book["title"]

    # Primary author
    creators = [{
        "creatorType": "author",
        "firstName": book["first_name"],
        "lastName": book["last_name"],
    }]
    # Additional authors
    for auth in book["additional_authors"]:
        creators.append({
            "creatorType": "author",
            "firstName": auth["first"],
            "lastName": auth["last"],
        })
    item["creators"] = creators

    # Core metadata
    item["ISBN"] = book["isbn13"] or book["isbn10"]
    item["publisher"] = book["publisher"]
    item["numPages"] = book["num_pages"]
    item["date"] = book["original_year"] or book["year_published"]

    # Tags: thematic shelves + rating + date read
    tags = []
    rt = rating_tag(book["my_rating"])
    if rt:
        tags.append({"tag": rt})
    if book["date_read"]:
        tags.append({"tag": f"date-read:{book['date_read']}"})
    # Thematic shelves become tags (reading-mode shelves get "mode:" prefix)
    for shelf in book["thematic_shelves"]:
        tags.append({"tag": shelf_tag(shelf)})
    # Source tag for tracking provenance
    tags.append({"tag": "source:goodreads"})
    item["tags"] = tags

    # Collections: map status shelf (exclusive shelf) to Zotero collection key
    collections = []
    if book["exclusive_shelf"] and book["exclusive_shelf"] in collection_map:
        collections.append(collection_map[book["exclusive_shelf"]])
    item["collections"] = collections

    # Extra field: structured metadata for future sync
    extra_lines = [
        f"Goodreads-ID: {book['goodreads_id']}",
    ]
    if book["avg_rating"]:
        extra_lines.append(f"Goodreads-Avg-Rating: {book['avg_rating']}")
    if book["date_added"]:
        extra_lines.append(f"Goodreads-Date-Added: {book['date_added']}")
    if book["read_count"]:
        extra_lines.append(f"Goodreads-Read-Count: {book['read_count']}")
    if book["binding"]:
        extra_lines.append(f"Goodreads-Binding: {book['binding']}")
    item["extra"] = "\n".join(extra_lines)

    return item


# -- Duplicate Detection ------------------------------------------------------

def normalize_title(title: str) -> str:
    """Normalize a title for comparison: lowercase, strip subtitles and punctuation."""
    title = title.lower().strip()
    # Compare on the part before the first colon (main title)
    title = title.split(":")[0].strip()
    # Remove common punctuation
    for ch in ".,;!?'\"()[]{}":
        title = title.replace(ch, "")
    # Collapse whitespace
    return " ".join(title.split())


def get_existing_zotero_titles(zot: zotero.Zotero) -> set[str]:
    """Fetch all book titles already in Zotero, normalized for matching."""
    items = zot.everything(zot.items(itemType="book"))
    titles = set()
    for item in items:
        titles.add(normalize_title(item["data"].get("title", "")))
    return titles


def filter_existing(books: list[dict], existing_titles: set[str]) -> tuple[list[dict], list[dict]]:
    """Split books into new and already-existing lists."""
    new_books = []
    skipped = []
    for book in books:
        if normalize_title(book["title"]) in existing_titles:
            skipped.append(book)
        else:
            new_books.append(book)
    return new_books, skipped


# -- Sync Support -------------------------------------------------------------

def get_existing_zotero_items_by_gr_id(zot: zotero.Zotero) -> dict[str, dict]:
    """
    Fetch all Zotero items tagged 'source:goodreads' and index them by
    Goodreads ID (parsed from the Extra field).
    Returns: {goodreads_id: full_zotero_item}
    """
    items = zot.everything(zot.items(itemType="book", tag="source:goodreads"))
    by_id = {}
    for item in items:
        extra = item["data"].get("extra", "")
        match = re.search(r"Goodreads-ID:\s*(\S+)", extra)
        if match:
            by_id[match.group(1)] = item
    return by_id


def compute_expected_tags(book: dict) -> list[dict]:
    """Compute the set of Goodreads-managed tags a book should have."""
    tags = []
    rt = rating_tag(book["my_rating"])
    if rt:
        tags.append({"tag": rt})
    if book["date_read"]:
        tags.append({"tag": f"date-read:{book['date_read']}"})
    for shelf in book["thematic_shelves"]:
        tags.append({"tag": shelf_tag(shelf)})
    tags.append({"tag": "source:goodreads"})
    return tags


def is_goodreads_managed_tag(tag_name: str, all_thematic_shelves: set[str]) -> bool:
    """Check if a tag is managed by this script (vs. user-added in Zotero)."""
    if tag_name == "source:goodreads":
        return True
    if tag_name.startswith("rating:"):
        return True
    if tag_name.startswith("date-read:"):
        return True
    if tag_name.startswith("mode:"):
        return True
    if tag_name.startswith("status:"):
        return True  # legacy tag from earlier imports
    # Check if it's a known thematic shelf name
    if tag_name in all_thematic_shelves:
        return True
    return False


def build_extra_field(book: dict) -> str:
    """Build the Extra field content for a book."""
    extra_lines = [f"Goodreads-ID: {book['goodreads_id']}"]
    if book["avg_rating"]:
        extra_lines.append(f"Goodreads-Avg-Rating: {book['avg_rating']}")
    if book["date_added"]:
        extra_lines.append(f"Goodreads-Date-Added: {book['date_added']}")
    if book["read_count"]:
        extra_lines.append(f"Goodreads-Read-Count: {book['read_count']}")
    if book["binding"]:
        extra_lines.append(f"Goodreads-Binding: {book['binding']}")
    return "\n".join(extra_lines)


def diff_item(
    book: dict,
    zotero_item: dict,
    collection_map: dict[str, str],
    all_thematic_shelves: set[str],
) -> dict | None:
    """
    Compare a Goodreads book against its existing Zotero item.
    Returns a dict describing changes, or None if no changes needed.
    Preserves user-added tags that aren't Goodreads-managed.
    """
    data = zotero_item["data"]
    changes = {}

    # 1. Collection (status shelf)
    expected_colls = []
    if book["exclusive_shelf"] and book["exclusive_shelf"] in collection_map:
        expected_colls = [collection_map[book["exclusive_shelf"]]]
    current_colls = data.get("collections", [])
    if set(expected_colls) != set(current_colls):
        # Find old status name for reporting
        reverse_map = {v: k for k, v in collection_map.items()}
        old_status = next((reverse_map[c] for c in current_colls if c in reverse_map), "(none)")
        new_status = book["exclusive_shelf"] or "(none)"
        if old_status != new_status:
            changes["status"] = f"{old_status} -> {new_status}"

    # 2. Tags (smart merge: only touch Goodreads-managed tags)
    current_tags = data.get("tags", [])
    user_tags = [t for t in current_tags
                 if not is_goodreads_managed_tag(t["tag"], all_thematic_shelves)]
    expected_gr_tags = compute_expected_tags(book)
    current_gr_tags = [t for t in current_tags
                       if is_goodreads_managed_tag(t["tag"], all_thematic_shelves)]

    current_gr_set = {t["tag"] for t in current_gr_tags}
    expected_gr_set = {t["tag"] for t in expected_gr_tags}

    if current_gr_set != expected_gr_set:
        added = expected_gr_set - current_gr_set
        removed = current_gr_set - expected_gr_set
        tag_changes = []
        if added:
            tag_changes.append(f"+{', '.join(sorted(added))}")
        if removed:
            tag_changes.append(f"-{', '.join(sorted(removed))}")
        if tag_changes:
            changes["tags"] = "; ".join(tag_changes)

    # 3. Extra field
    expected_extra = build_extra_field(book)
    # Preserve any non-Goodreads lines the user/Zotero may have added
    current_extra = data.get("extra", "")
    current_gr_lines = []
    current_other_lines = []
    for line in current_extra.split("\n"):
        if line.strip().startswith("Goodreads-"):
            current_gr_lines.append(line)
        elif line.strip():
            current_other_lines.append(line)  # Citation Key, user notes, etc.

    expected_gr_lines = expected_extra.split("\n")
    if [l.strip() for l in current_gr_lines] != [l.strip() for l in expected_gr_lines]:
        changes["extra"] = "metadata updated"

    if not changes:
        return None

    # Build the merged updates to apply
    changes["_updates"] = {}

    # Merged tags = user tags + expected goodreads tags
    changes["_updates"]["tags"] = user_tags + expected_gr_tags

    # Collections
    changes["_updates"]["collections"] = expected_colls

    # Merged extra = expected goodreads lines + other lines (Citation Key, user notes)
    merged_extra_lines = expected_gr_lines + current_other_lines
    changes["_updates"]["extra"] = "\n".join(merged_extra_lines)

    return changes


def sync_books(
    zot: zotero.Zotero,
    books: list[dict],
    existing_map: dict[str, dict],
    collection_map: dict[str, str],
    all_thematic_shelves: set[str],
    batch_size: int = 50,
    delay: float = 1.0,
    dry_run: bool = False,
) -> dict:
    """
    Full sync: create new items, update changed items, flag orphans.
    Returns summary stats.
    """
    stats = {
        "created": 0, "updated": 0, "unchanged": 0,
        "orphaned": 0, "failed": 0, "errors": [],
    }

    new_books = []
    to_update = []  # list of (book, zotero_item, changes_dict)
    gr_ids_in_csv = set()

    for book in books:
        gr_id = book["goodreads_id"]
        gr_ids_in_csv.add(gr_id)

        if gr_id not in existing_map:
            new_books.append(book)
        else:
            zotero_item = existing_map[gr_id]
            changes = diff_item(book, zotero_item, collection_map, all_thematic_shelves)
            if changes:
                to_update.append((book, zotero_item, changes))
            else:
                stats["unchanged"] += 1

    # Orphans: in Zotero but not in CSV
    orphaned = []
    for gr_id, zitem in existing_map.items():
        if gr_id not in gr_ids_in_csv:
            orphaned.append(zitem)
    stats["orphaned"] = len(orphaned)

    # Report
    print(f"\n[Sync] Comparison results:")
    print(f"   New books:    {len(new_books)}")
    print(f"   Changed:      {len(to_update)}")
    print(f"   Unchanged:    {stats['unchanged']}")
    print(f"   Orphaned:     {len(orphaned)}")

    # -- Detail: changes --
    if to_update:
        print(f"\n[Changes]")
        for book, zitem, changes in to_update:
            parts = []
            if "status" in changes:
                parts.append(f"status: {changes['status']}")
            if "tags" in changes:
                parts.append(f"tags: {changes['tags']}")
            if "extra" in changes:
                parts.append(f"{changes['extra']}")
            print(f"   ~ {book['title'][:55]} ({', '.join(parts)})")

    # -- Detail: new --
    if new_books:
        print(f"\n[New books]")
        for book in new_books:
            print(f"   + {book['title'][:60]} -> {book['exclusive_shelf']}")

    # -- Detail: orphans --
    if orphaned:
        print(f"\n[!] Orphaned (in Zotero but not in Goodreads CSV):")
        for zitem in orphaned:
            print(f"   ? {zitem['data']['title'][:60]} -- review manually")

    if dry_run:
        stats["created"] = len(new_books)
        stats["updated"] = len(to_update)
        return stats

    # -- Apply updates --
    if to_update:
        print(f"\n[Updating] {len(to_update)} changed items...")
        items_to_push = []
        for book, zitem, changes in to_update:
            updates = changes["_updates"]
            zitem["data"]["tags"] = updates["tags"]
            zitem["data"]["collections"] = updates["collections"]
            zitem["data"]["extra"] = updates["extra"]
            items_to_push.append(zitem)

        for i in range(0, len(items_to_push), batch_size):
            batch = items_to_push[i:i + batch_size]
            try:
                zot.update_items(batch)
                stats["updated"] += len(batch)
                for item in batch:
                    print(f"   [ok] {item['data']['title'][:60]}")
            except Exception as e:
                stats["failed"] += len(batch)
                stats["errors"].append(f"Update batch: {e}")
                print(f"   [FAIL] Batch update failed: {e}")
            if i + batch_size < len(items_to_push):
                time.sleep(delay)

    # -- Create new items --
    if new_books:
        print(f"\n[Creating] {len(new_books)} new items...")
        create_stats = import_books(
            zot, new_books, collection_map,
            batch_size=batch_size, delay=delay,
        )
        stats["created"] = create_stats["created"]
        stats["failed"] += create_stats["failed"]
        stats["errors"].extend(create_stats["errors"])

    return stats


# -- Zotero API Operations ----------------------------------------------------

def ensure_collections(
    zot: zotero.Zotero,
    shelf_names: list[str],
    parent_key: str = None,
    dry_run: bool = False
) -> dict[str, str]:
    """
    Create Zotero collections for each thematic shelf.
    Returns a mapping of shelf_name -> collection_key.
    
    Creates a parent "Goodreads" collection, with each shelf as a sub-collection.
    """
    collection_map = {}

    if dry_run:
        print("\n[Collections] Collections that would be created:")
        print(f"   [+] Goodreads (parent)")
        for name in sorted(shelf_names):
            print(f"       +-- {name}")
        return {name: f"FAKE_{name}" for name in shelf_names}

    # Get existing collections to avoid duplicates
    existing = zot.collections()
    existing_by_name = {}
    for c in existing:
        existing_by_name[c["data"]["name"]] = c["data"]["key"]

    # Create or find parent "Goodreads" collection
    parent_name = "Goodreads"
    if parent_name in existing_by_name:
        parent_key = existing_by_name[parent_name]
        print(f"[+] Found existing '{parent_name}' collection: {parent_key}")
    else:
        resp = zot.create_collections([{"name": parent_name}])
        if resp and "successful" in resp:
            parent_key = list(resp["successful"].values())[0]["data"]["key"]
            print(f"[+] Created '{parent_name}' collection: {parent_key}")
        else:
            print(f"[!] Failed to create parent collection, using library root")
            parent_key = None

    # Build sub-collection mapping for existing children
    existing_children = {}
    for c in existing:
        if c["data"].get("parentCollection") == parent_key:
            existing_children[c["data"]["name"]] = c["data"]["key"]

    # Create missing sub-collections in batches
    to_create = []
    for name in sorted(shelf_names):
        if name in existing_children:
            collection_map[name] = existing_children[name]
            print(f"   [ok] Found existing: {name}")
        else:
            payload = {"name": name}
            if parent_key:
                payload["parentCollection"] = parent_key
            to_create.append(payload)

    if to_create:
        print(f"   Creating {len(to_create)} new sub-collections...")
        resp = zot.create_collections(to_create)
        if resp and "successful" in resp:
            for idx_str, data in resp["successful"].items():
                name = data["data"]["name"]
                key = data["data"]["key"]
                collection_map[name] = key
                print(f"   [ok] Created: {name} -> {key}")
        if resp and "failed" in resp:
            for idx_str, err in resp["failed"].items():
                print(f"   [FAIL] Failed: {to_create[int(idx_str)]['name']} -> {err}")

    return collection_map


def import_books(
    zot: zotero.Zotero,
    books: list[dict],
    collection_map: dict[str, str],
    batch_size: int = 25,
    delay: float = 1.0,
    dry_run: bool = False,
) -> dict:
    """Import books into Zotero in batches. Returns summary stats."""
    if zot is not None:
        template = zot.item_template("book")
    else:
        # Minimal template for dry-run preview
        template = {
            "itemType": "", "title": "", "creators": [], "ISBN": "",
            "publisher": "", "numPages": "", "date": "", "tags": [],
            "collections": [], "extra": "",
        }

    stats = {"created": 0, "failed": 0, "skipped": 0, "errors": []}

    # Build all items
    items = []
    for book in books:
        item = build_zotero_item(template, book, collection_map)
        items.append((book, item))

    if dry_run:
        print(f"\n[Books] Would import {len(items)} books. First 5 previews:\n")
        for book, item in items[:5]:
            print(f"  * {item['title']}")
            print(f"     Author: {item['creators'][0]['firstName']} {item['creators'][0]['lastName']}")
            print(f"     ISBN: {item['ISBN'] or '(none - will use title/author)'}")
            print(f"     Tags: {', '.join(t['tag'] for t in item['tags'])}")
            coll = book['exclusive_shelf'] if book['exclusive_shelf'] in collection_map else ''
            print(f"     Collection: {coll or '(none)'}")
            print(f"     Extra: {item['extra'][:80]}...")
            print()
        return stats

    # Upload in batches
    total_batches = (len(items) + batch_size - 1) // batch_size
    for batch_idx in range(total_batches):
        start = batch_idx * batch_size
        end = min(start + batch_size, len(items))
        batch_books = items[start:end]
        batch_items = [item for _, item in batch_books]
        batch_titles = [book["title"] for book, _ in batch_books]

        print(f"\n[Batch] {batch_idx + 1}/{total_batches} ({len(batch_items)} items)...")

        try:
            resp = zot.create_items(batch_items)

            if resp and "successful" in resp:
                for idx_str, data in resp["successful"].items():
                    idx = int(idx_str)
                    stats["created"] += 1
                    print(f"   [ok] {batch_titles[idx]}")

            if resp and "unchanged" in resp:
                for idx_str in resp["unchanged"]:
                    stats["skipped"] += 1
                    print(f"   [skip] {batch_titles[int(idx_str)]} (unchanged)")

            if resp and "failed" in resp:
                for idx_str, err in resp["failed"].items():
                    idx = int(idx_str)
                    stats["failed"] += 1
                    stats["errors"].append(f"{batch_titles[idx]}: {err}")
                    print(f"   [FAIL] {batch_titles[idx]}: {err}")

        except Exception as e:
            print(f"   [FAIL] Batch failed: {e}")
            stats["failed"] += len(batch_items)
            stats["errors"].append(f"Batch {batch_idx + 1}: {e}")

        # Rate limiting
        if batch_idx < total_batches - 1:
            time.sleep(delay)

    return stats


# -- Main ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Import Goodreads library CSV into Zotero",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("--csv", required=True, help="Path to Goodreads CSV export")
    parser.add_argument("--library-id", required=True, help="Your Zotero user ID")
    parser.add_argument("--api-key", required=True, help="Your Zotero API key")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview import without writing to Zotero")
    parser.add_argument("--batch-size", type=int, default=25,
                        help="Items per API call (default: 25, max: 50)")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Seconds between API batches (default: 1)")
    parser.add_argument("--shelf-filter",
                        help="Only import books from this shelf")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip books already in your Zotero library")
    parser.add_argument("--sync", action="store_true",
                        help="Full sync: create new, update changed, flag orphaned")

    args = parser.parse_args()

    if args.sync and args.skip_existing:
        print("ERROR: --sync and --skip-existing are mutually exclusive")
        sys.exit(1)

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"ERROR: CSV not found: {csv_path}")
        sys.exit(1)

    mode_label = "Sync" if args.sync else "Import"
    print("=" * 60)
    print(f"  Goodreads -> Zotero {mode_label}")
    print("=" * 60)
    if args.dry_run:
        print("  [!] DRY RUN MODE -- no changes will be made")
    print()

    # Parse CSV
    print(f"[CSV] Parsing {csv_path.name}...")
    books = parse_goodreads_csv(str(csv_path), args.shelf_filter)
    print(f"   Found {len(books)} books")

    if args.shelf_filter:
        print(f"   (filtered to shelf: {args.shelf_filter})")

    # Summarize what we're about to do
    shelves_all = set()
    thematic_all = set()
    isbn_count = sum(1 for b in books if b["isbn13"] or b["isbn10"])
    for b in books:
        shelves_all.update(b["all_shelves"])
        thematic_all.update(b["thematic_shelves"])

    print(f"   ISBN coverage: {isbn_count}/{len(books)} ({100*isbn_count/len(books):.0f}%)")
    print(f"   Unique shelves: {len(shelves_all)} total, {len(thematic_all)} thematic")

    status_summary = {}
    for b in books:
        s = b["exclusive_shelf"]
        status_summary[s] = status_summary.get(s, 0) + 1
    print(f"   By status: {', '.join(f'{k}: {v}' for k,v in sorted(status_summary.items()))}")

    # Check for duplicates against existing Zotero library
    if args.skip_existing:
        print(f"\n[Dedup] Checking for existing books in Zotero...")
        dedup_zot = zotero.Zotero(args.library_id, "user", args.api_key)
        existing_titles = get_existing_zotero_titles(dedup_zot)
        books, skipped_books = filter_existing(books, existing_titles)
        if skipped_books:
            print(f"   Skipping {len(skipped_books)} books already in Zotero:")
            for b in skipped_books:
                print(f"     - {b['title'][:70]}")
        else:
            print(f"   No duplicates found")
        print(f"   Remaining: {len(books)} books to import")

    # Connect to Zotero
    if not args.dry_run:
        print(f"\n[Connect] Connecting to Zotero (user {args.library_id})...")
        zot = zotero.Zotero(args.library_id, "user", args.api_key)
        # Quick validation
        try:
            zot.items(limit=1)
            print("   [ok] Connected successfully")
        except Exception as e:
            print(f"   [FAIL] Connection failed: {e}")
            sys.exit(1)
    else:
        zot = None

    # Create collections for status shelves
    # For sync mode, always use real Zotero connection for collections
    # (even in dry-run) so diff can compare real collection keys
    status_shelves_used = set(b["exclusive_shelf"] for b in books if b["exclusive_shelf"])
    if args.sync:
        sync_zot = zot if not args.dry_run else zotero.Zotero(args.library_id, "user", args.api_key)
        if status_shelves_used:
            print(f"\n[Collections] Setting up collections for {len(status_shelves_used)} status shelves...")
            collection_map = ensure_collections(sync_zot, list(status_shelves_used))
        else:
            collection_map = {}
    else:
        if status_shelves_used:
            print(f"\n[Collections] Setting up collections for {len(status_shelves_used)} status shelves...")
            if args.dry_run:
                collection_map = ensure_collections(None, list(status_shelves_used), dry_run=True)
            else:
                collection_map = ensure_collections(zot, list(status_shelves_used))
        else:
            collection_map = {}

    # -- Sync mode --
    if args.sync:
        print(f"\n[Sync] Fetching existing Goodreads items from Zotero...")
        existing_map = get_existing_zotero_items_by_gr_id(sync_zot)
        print(f"   Found {len(existing_map)} existing Goodreads items in Zotero")

        stats = sync_books(
            zot, books, existing_map, collection_map,
            all_thematic_shelves=thematic_all,
            batch_size=args.batch_size,
            delay=args.delay,
            dry_run=args.dry_run,
        )

        # Sync summary
        print("\n" + "=" * 60)
        print("  Sync Summary")
        print("=" * 60)
        if args.dry_run:
            print(f"  Would create:  {stats['created']}")
            print(f"  Would update:  {stats['updated']}")
            print(f"  Unchanged:     {stats['unchanged']}")
            print(f"  Orphaned:      {stats['orphaned']}")
        else:
            print(f"  Created:   {stats['created']}")
            print(f"  Updated:   {stats['updated']}")
            print(f"  Unchanged: {stats['unchanged']}")
            print(f"  Orphaned:  {stats['orphaned']}")
            print(f"  Failed:    {stats['failed']}")
            if stats["errors"]:
                print(f"\n  Errors:")
                for e in stats["errors"]:
                    print(f"    - {e}")

    # -- Import mode --
    else:
        print(f"\n[Import] Importing {len(books)} books...")
        if args.dry_run:
            stats = import_books(None, books, collection_map, dry_run=True)
        else:
            stats = import_books(
                zot, books, collection_map,
                batch_size=args.batch_size,
                delay=args.delay,
            )

        # Import summary
        print("\n" + "=" * 60)
        print("  Import Summary")
        print("=" * 60)
        if args.dry_run:
            print(f"  Would create: {len(books)} book items")
            print(f"  Would create: {len(status_shelves_used)} collections under 'Goodreads/'")
            no_isbn = [b for b in books if not b["isbn13"] and not b["isbn10"]]
            if no_isbn:
                print(f"\n  [!] {len(no_isbn)} books have no ISBN (will use title/author only):")
                for b in no_isbn:
                    print(f"     - {b['title'][:60]}")
        else:
            print(f"  Created:  {stats['created']}")
            print(f"  Skipped:  {stats['skipped']}")
            print(f"  Failed:   {stats['failed']}")
            if stats["errors"]:
                print(f"\n  Errors:")
                for e in stats["errors"]:
                    print(f"    - {e}")

    print()


if __name__ == "__main__":
    main()
