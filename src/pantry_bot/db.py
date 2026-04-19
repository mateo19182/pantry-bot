from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Literal

Action = Literal["add", "remove"]


@dataclass
class Item:
    id: int
    name: str
    quantity: float
    unit: str
    expires_at: date | None
    added_at: datetime
    added_by: int
    notes: str | None


@dataclass
class ShoppingItem:
    id: int
    name: str
    quantity: float
    unit: str
    added_at: datetime
    added_by: int
    notes: str | None


@dataclass
class ItemChange:
    action: Action
    name: str
    quantity: float
    unit: str
    expires_at: date | None = None
    notes: str | None = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    quantity REAL NOT NULL,
    unit TEXT NOT NULL,
    expires_at DATE,
    added_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    added_by INTEGER NOT NULL,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_items_name_unit ON items(name, unit);
CREATE INDEX IF NOT EXISTS idx_items_expires ON items(expires_at);

CREATE TABLE IF NOT EXISTS shopping_list (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    quantity REAL NOT NULL,
    unit TEXT NOT NULL,
    added_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    added_by INTEGER NOT NULL,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_shopping_name_unit ON shopping_list(name, unit);

CREATE TABLE IF NOT EXISTS action_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    user_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
"""


def _connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        path,
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: str) -> sqlite3.Connection:
    conn = _connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _norm(name: str) -> str:
    return name.strip().lower()


def _row_to_item(row: sqlite3.Row) -> Item:
    return Item(
        id=row["id"],
        name=row["name"],
        quantity=row["quantity"],
        unit=row["unit"],
        expires_at=row["expires_at"],
        added_at=row["added_at"],
        added_by=row["added_by"],
        notes=row["notes"],
    )


def list_items(conn: sqlite3.Connection) -> list[Item]:
    cur = conn.execute(
        "SELECT * FROM items "
        "ORDER BY CASE WHEN expires_at IS NULL THEN 1 ELSE 0 END, expires_at ASC, name ASC"
    )
    return [_row_to_item(r) for r in cur.fetchall()]


def apply_changes(
    conn: sqlite3.Connection,
    changes: Iterable[ItemChange],
    user_id: int,
) -> list[str]:
    """Apply changes and return human-readable result lines."""
    results: list[str] = []
    with conn:
        for change in changes:
            name = _norm(change.name)
            unit = change.unit.strip().lower() or "unit"
            if change.action == "add":
                results.append(_apply_add(conn, change, name, unit, user_id))
            else:
                results.append(_apply_remove(conn, change, name, unit))

            conn.execute(
                "INSERT INTO action_log(user_id, action, payload_json) VALUES (?, ?, ?)",
                (
                    user_id,
                    change.action,
                    json.dumps(
                        {
                            "name": name,
                            "quantity": change.quantity,
                            "unit": unit,
                            "expires_at": change.expires_at.isoformat()
                            if change.expires_at
                            else None,
                            "notes": change.notes,
                        }
                    ),
                ),
            )
    return results


def _apply_add(
    conn: sqlite3.Connection,
    change: ItemChange,
    name: str,
    unit: str,
    user_id: int,
) -> str:
    existing = conn.execute(
        "SELECT * FROM items WHERE name = ? AND unit = ?",
        (name, unit),
    ).fetchone()

    if existing is None:
        conn.execute(
            "INSERT INTO items(name, quantity, unit, expires_at, added_by, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (name, change.quantity, unit, change.expires_at, user_id, change.notes),
        )
        return f"+ {_fmt_qty(change.quantity, unit)} {name}"

    new_qty = existing["quantity"] + change.quantity
    new_expiry = _earliest_future(existing["expires_at"], change.expires_at)
    conn.execute(
        "UPDATE items SET quantity = ?, expires_at = ?, notes = COALESCE(?, notes) WHERE id = ?",
        (new_qty, new_expiry, change.notes, existing["id"]),
    )
    return f"+ {_fmt_qty(change.quantity, unit)} {name} (now {_fmt_qty(new_qty, unit)})"


def _apply_remove(
    conn: sqlite3.Connection,
    change: ItemChange,
    name: str,
    unit: str,
) -> str:
    existing = conn.execute(
        "SELECT * FROM items WHERE name = ? AND unit = ?",
        (name, unit),
    ).fetchone()
    if existing is None:
        existing = conn.execute(
            "SELECT * FROM items WHERE name = ? ORDER BY expires_at ASC LIMIT 1",
            (name,),
        ).fetchone()

    if existing is None:
        return f"? {name} — not in pantry"

    new_qty = existing["quantity"] - change.quantity
    if new_qty <= 0:
        conn.execute("DELETE FROM items WHERE id = ?", (existing["id"],))
        return f"- {name} (removed)"

    conn.execute(
        "UPDATE items SET quantity = ? WHERE id = ?",
        (new_qty, existing["id"]),
    )
    return f"- {_fmt_qty(change.quantity, existing['unit'])} {name} (now {_fmt_qty(new_qty, existing['unit'])})"


def _row_to_shopping(row: sqlite3.Row) -> ShoppingItem:
    return ShoppingItem(
        id=row["id"],
        name=row["name"],
        quantity=row["quantity"],
        unit=row["unit"],
        added_at=row["added_at"],
        added_by=row["added_by"],
        notes=row["notes"],
    )


def list_shopping(conn: sqlite3.Connection) -> list[ShoppingItem]:
    cur = conn.execute("SELECT * FROM shopping_list ORDER BY added_at ASC, name ASC")
    return [_row_to_shopping(r) for r in cur.fetchall()]


def apply_shopping_changes(
    conn: sqlite3.Connection,
    changes: Iterable[ItemChange],
    user_id: int,
) -> list[str]:
    """Apply add/remove to the shopping list. Mirrors apply_changes but ignores expiry."""
    results: list[str] = []
    with conn:
        for change in changes:
            name = _norm(change.name)
            unit = change.unit.strip().lower() or "unit"
            if change.action == "add":
                results.append(_shop_add(conn, change, name, unit, user_id))
            else:
                results.append(_shop_remove(conn, change, name, unit))

            conn.execute(
                "INSERT INTO action_log(user_id, action, payload_json) VALUES (?, ?, ?)",
                (
                    user_id,
                    f"shop_{change.action}",
                    json.dumps(
                        {
                            "name": name,
                            "quantity": change.quantity,
                            "unit": unit,
                            "notes": change.notes,
                        }
                    ),
                ),
            )
    return results


def _shop_add(
    conn: sqlite3.Connection,
    change: ItemChange,
    name: str,
    unit: str,
    user_id: int,
) -> str:
    existing = conn.execute(
        "SELECT * FROM shopping_list WHERE name = ? AND unit = ?",
        (name, unit),
    ).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO shopping_list(name, quantity, unit, added_by, notes) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, change.quantity, unit, user_id, change.notes),
        )
        return f"+ {_fmt_qty(change.quantity, unit)} {name}"

    new_qty = existing["quantity"] + change.quantity
    conn.execute(
        "UPDATE shopping_list SET quantity = ?, notes = COALESCE(?, notes) WHERE id = ?",
        (new_qty, change.notes, existing["id"]),
    )
    return f"+ {_fmt_qty(change.quantity, unit)} {name} (now {_fmt_qty(new_qty, unit)})"


def _shop_remove(
    conn: sqlite3.Connection,
    change: ItemChange,
    name: str,
    unit: str,
) -> str:
    existing = conn.execute(
        "SELECT * FROM shopping_list WHERE name = ? AND unit = ?",
        (name, unit),
    ).fetchone()
    if existing is None:
        existing = conn.execute(
            "SELECT * FROM shopping_list WHERE name = ? LIMIT 1",
            (name,),
        ).fetchone()
    if existing is None:
        return f"? {name} — not on shopping list"

    new_qty = existing["quantity"] - change.quantity
    if new_qty <= 0:
        conn.execute("DELETE FROM shopping_list WHERE id = ?", (existing["id"],))
        return f"- {name} (removed from list)"
    conn.execute(
        "UPDATE shopping_list SET quantity = ? WHERE id = ?",
        (new_qty, existing["id"]),
    )
    return f"- {_fmt_qty(change.quantity, existing['unit'])} {name} (now {_fmt_qty(new_qty, existing['unit'])})"


def take_shopping_item(
    conn: sqlite3.Connection,
    shopping_id: int,
    user_id: int,
) -> tuple[ShoppingItem | None, str]:
    """Move a shopping-list item into the pantry. Returns (item_moved, message).
    Returns (None, ...) if the item no longer exists."""
    with conn:
        row = conn.execute(
            "SELECT * FROM shopping_list WHERE id = ?", (shopping_id,)
        ).fetchone()
        if row is None:
            return None, "Item no longer on the list."
        item = _row_to_shopping(row)

        add_change = ItemChange(
            action="add",
            name=item.name,
            quantity=item.quantity,
            unit=item.unit,
            notes=item.notes,
        )
        add_msg = _apply_add(conn, add_change, item.name, item.unit, user_id)
        conn.execute("DELETE FROM shopping_list WHERE id = ?", (shopping_id,))
        conn.execute(
            "INSERT INTO action_log(user_id, action, payload_json) VALUES (?, ?, ?)",
            (
                user_id,
                "shop_bought",
                json.dumps(
                    {
                        "name": item.name,
                        "quantity": item.quantity,
                        "unit": item.unit,
                    }
                ),
            ),
        )
        return item, add_msg


def clear_shopping(conn: sqlite3.Connection, user_id: int) -> int:
    with conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM shopping_list").fetchone()["c"]
        conn.execute("DELETE FROM shopping_list")
        conn.execute(
            "INSERT INTO action_log(user_id, action, payload_json) VALUES (?, ?, ?)",
            (user_id, "shop_clear", json.dumps({"count": count})),
        )
    return count


def clear_all(conn: sqlite3.Connection, user_id: int) -> int:
    with conn:
        cur = conn.execute("SELECT COUNT(*) AS c FROM items")
        count = cur.fetchone()["c"]
        conn.execute("DELETE FROM items")
        conn.execute(
            "INSERT INTO action_log(user_id, action, payload_json) VALUES (?, ?, ?)",
            (user_id, "clear", json.dumps({"count": count})),
        )
    return count


def _earliest_future(a: date | None, b: date | None) -> date | None:
    candidates = [d for d in (a, b) if d is not None]
    if not candidates:
        return None
    today = date.today()
    future = [d for d in candidates if d >= today]
    return min(future) if future else min(candidates)


def _fmt_qty(qty: float, unit: str) -> str:
    if qty == int(qty):
        return f"{int(qty)}{unit if unit and unit != 'unit' else ''}".strip() or str(int(qty))
    return f"{qty:g}{unit if unit and unit != 'unit' else ''}"
