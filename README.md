# Goodreads → Zotero Sync

Import and sync your Goodreads library into [Zotero](https://www.zotero.org/), the open-source reference manager. Built for readers who use Goodreads for discovery and tracking but want their reading library in a tool they actually own.

## Why?

Goodreads is great for social book discovery. Zotero is great for organizing, annotating, and citing. This script bridges them — turning your Goodreads CSV export into a well-structured Zotero library with collections, tags, and metadata that support real knowledge work.

**What it creates:**

- **Book items** with full metadata (title, author, ISBN, publisher, pages, year)
- **Collections** mirroring your Goodreads status shelves (`read`, `currently-reading`, `to-read`, etc.) under a `Goodreads/` parent
- **Tags** for ratings (`rating:4/5`), read dates (`date-read:2024/03/15`), thematic shelves, and reading modes (`mode:immersion-reading`)
- **Provenance tracking** via `source:goodreads` tag and Goodreads ID in the Extra field

## Quick Start

### Prerequisites

- Python 3.10+
- A [Zotero account](https://www.zotero.org/user/register) (free)
- A Zotero API key ([create one here](https://www.zotero.org/settings/keys/new)) with library read/write access

### Install

```bash
pip install pyzotero
```

### Export Your Goodreads Library

1. Go to [goodreads.com/review/import](https://www.goodreads.com/review/import)
2. Click **Export Library** (top of page)
3. Wait for the CSV to generate, then download it

### Run

```bash
# Preview what would happen (no changes made)
python goodreads_to_zotero.py \
  --csv goodreads_library_export.csv \
  --library-id YOUR_USER_ID \
  --api-key YOUR_API_KEY \
  --dry-run

# Import for real
python goodreads_to_zotero.py \
  --csv goodreads_library_export.csv \
  --library-id YOUR_USER_ID \
  --api-key YOUR_API_KEY
```

> **Finding your User ID:** Go to [zotero.org/settings/keys](https://www.zotero.org/settings/keys). Your user ID is displayed at the top: *"Your userID for use in API calls is XXXXXXX"*.

## Modes

### Import (default)

Creates new Zotero items for every book in the CSV. Use `--skip-existing` to avoid duplicates on re-runs.

```bash
python goodreads_to_zotero.py --csv library.csv --library-id ID --api-key KEY --skip-existing
```

### Sync (`--sync`)

Compares your current Goodreads export against existing Zotero items (matched by Goodreads ID in the Extra field) and:

- **Creates** new books not yet in Zotero
- **Updates** items where shelf, rating, or metadata changed
- **Flags orphans** — books in Zotero that are no longer in your Goodreads export (for manual review, never auto-deleted)

```bash
python goodreads_to_zotero.py --csv library.csv --library-id ID --api-key KEY --sync --dry-run
```

Sync preserves any tags or notes you've added manually in Zotero. Only Goodreads-managed tags (ratings, shelves, read dates) are touched.

## Options

| Flag | Description | Default |
|------|-------------|---------|
| `--csv` | Path to Goodreads CSV export | *(required)* |
| `--library-id` | Your Zotero user ID | *(required)* |
| `--api-key` | Your Zotero API key | *(required)* |
| `--dry-run` | Preview without writing to Zotero | off |
| `--sync` | Full sync mode (create/update/flag) | off |
| `--skip-existing` | Skip books already in Zotero (import mode) | off |
| `--shelf-filter` | Only process books on a specific shelf | all |
| `--batch-size` | Items per API call (max 50) | 25 |
| `--delay` | Seconds between API calls | 1.0 |

## How Shelves Map to Zotero

| Goodreads Shelf Type | Zotero Representation |
|----------------------|-----------------------|
| Status shelves (`read`, `currently-reading`, `to-read`, custom status) | **Collections** under `Goodreads/` parent |
| Thematic shelves (`narrative-nonfiction-biography`, `economics`, etc.) | **Tags** |
| Reading mode shelves (`immersion-reading`, `study-reading`, etc.) | **Tags** with `mode:` prefix |

## Data Mapping

| Goodreads Field | Zotero Field |
|-----------------|-------------|
| Title | Title |
| Author / Author l-f | Creators (first/last parsed) |
| Additional Authors | Additional creators |
| ISBN13 / ISBN | ISBN (13 preferred) |
| Publisher | Publisher |
| Number of Pages | Num Pages |
| Original Publication Year | Date |
| Exclusive Shelf | Collection |
| Bookshelves (thematic) | Tags |
| My Rating | Tag (`rating:N/5`) |
| Date Read | Tag (`date-read:YYYY/MM/DD`) |
| Goodreads ID, Avg Rating, Date Added, Read Count, Binding | Extra field (structured for sync) |

## Example Output

After import, a book in Zotero looks like:

```
Title:       Building a Second Brain
Author:      Tiago Forte
ISBN:        9781982167387
Publisher:   Atria Books
Pages:       272
Date:        2022
Collections: Goodreads / read
Tags:        rating:5/5, date-read:2024/12/01, productivity, mode:study-reading, source:goodreads
Extra:       Goodreads-ID: 59616977
             Goodreads-Avg-Rating: 3.98
             Goodreads-Date-Added: 2024/11/15
             Goodreads-Read-Count: 1
             Goodreads-Binding: Hardcover
```

## Customization

The script defines two sets of shelf classifications near the top of the file:

- `STATUS_SHELVES` — shelves that become Zotero collections (e.g., `read`, `to-read`, `dnf-paused`)
- `READING_MODE_SHELVES` — shelves that get a `mode:` tag prefix (e.g., `immersion-reading`, `study-reading`)

Edit these sets to match your own Goodreads shelf taxonomy before running.

## Built With

- [pyzotero](https://github.com/urschrei/pyzotero) — Python wrapper for the Zotero API
- [Claude Code](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview) — AI pair programming (this script was developed collaboratively with Claude)

## License

MIT
