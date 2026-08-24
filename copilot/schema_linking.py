"""Catalog introspection and schema linking.

The catalog is loaded once per process and re-used across questions; linking is
a pure function of (question, catalog), which is what makes it testable without
a database. Everything the model sees about the schema flows through
LinkedSchema.render, including column comments, which matters because comments
are untrusted content: the safety suite plants an instruction in one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import psycopg


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    data_type: str
    comment: str = ""


@dataclass(frozen=True)
class TableInfo:
    name: str
    kind: str  # table or view
    columns: tuple[ColumnInfo, ...]
    comment: str = ""

    def token_set(self) -> set[str]:
        words = {self.name}
        words.update(c.name for c in self.columns)
        if self.comment:
            words.update(re.findall(r"[a-zA-Z]{3,}", self.comment.lower()))
        return words


@dataclass(frozen=True)
class LinkedSchema:
    tables: tuple[TableInfo, ...]

    def render(self) -> str:
        blocks = []
        for table in self.tables:
            lines = [f"{table.name} ({table.kind})"]
            for col in table.columns:
                line = f"  {col.name} {col.data_type}"
                if col.comment:
                    line += f" -- {col.comment}"
                lines.append(line)
            if table.comment:
                lines.append(f"  -- table note: {table.comment}")
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def table_names(self) -> list[str]:
        return [t.name for t in self.tables]


# Words a question uses that do not appear in table or column names. Kept short
# on purpose: every entry here is a claim that this synonym maps to exactly one
# area of the warehouse, and a wrong mapping is worse than a miss because the
# model never sees the right table at all.
SYNONYMS: dict[str, str] = {
    "rider": "delivery_partners",
    "driver": "delivery_partners",
    "courier": "delivery_partners",
    "dish": "menu_items",
    "food": "menu_items",
    "cuisine": "restaurants",
    "restaurant": "restaurants",
    "revenue": "orders",
    "gmv": "orders",
    "sales": "orders",
    "refund": "refunds",
    "payment": "payments",
    "tip": "deliveries",
    "late": "deliveries",
    "review": "ratings",
    "rating": "ratings",
    "complaint": "support_tickets",
    "ticket": "support_tickets",
    "coupon": "coupons",
    "promo": "coupons",
    "discount": "coupon_redemptions",
    "payout": "payouts",
    "shift": "partner_shifts",
    "zone": "zones",
    "monthly": "v_monthly_city_summary",
    "trend": "daily_city_metrics",
    "rain": "daily_city_metrics",
    "festival": "daily_city_metrics",
}


def _tokens(text: str) -> list[str]:
    raw = re.findall(r"[a-zA-Z]{3,}", text.lower())
    out = list(raw)
    # crude singularisation so "orders" matches the columns of "orders" and
    # "customers" still matches customer_id.
    out.extend(t[:-1] for t in raw if t.endswith("s") and len(t) > 4)
    return out


def link(question: str, catalog: dict[str, TableInfo], max_tables: int = 6) -> LinkedSchema:
    tokens = set(_tokens(question))
    synonyms_hit = {SYNONYMS[t] for t in tokens if t in SYNONYMS}

    scored: list[tuple[float, str]] = []
    for name, info in catalog.items():
        score = 0.0
        if name in synonyms_hit:
            score += 4.0
        own = {w.lower() for w in _tokens(" ".join(info.token_set()))}
        for token in tokens:
            if token == name.rstrip("s"):
                score += 3.0
            elif any(token == c.name.lower() for c in info.columns):
                score += 2.0
            elif token in own:
                score += 0.5
        if score > 0:
            scored.append((score, name))

    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    chosen = {name for _, name in scored[:max_tables]}

    # A question about orders almost always needs customers or restaurants to be
    # useful; pull in direct neighbours of whatever ranked highest so joins stay
    # possible within the cap.
    for _, name in scored[:2]:
        for other in catalog.values():
            if len(chosen) >= max_tables:
                break
            joined = f"{name}_id"
            if other.name != name and any(c.name == joined for c in other.columns):
                chosen.add(other.name)

    ordered = [catalog[name] for name in sorted(chosen)]
    return LinkedSchema(tables=tuple(ordered))


INTROSPECT_SQL = """
SELECT c.relname,
       CASE WHEN c.relkind IN ('v', 'm') THEN 'view' ELSE 'table' END AS kind,
       obj_description(c.oid, 'pg_class') AS table_comment,
       a.attname,
       format_type(a.atttypid, a.atttypmod) AS data_type,
       col_description(a.attrelid, a.attnum) AS col_comment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = 'warehouse'
  AND c.relkind IN ('r', 'p', 'v', 'm')
ORDER BY c.relname, a.attnum
"""


async def load_catalog(ro_url: str) -> dict[str, TableInfo]:
    """One connection, one query, then closed: the cache lives above this."""

    tables: dict[str, TableInfo] = {}
    conn = await psycopg.AsyncConnection.connect(ro_url)
    try:
        async with conn.cursor() as cur:
            await cur.execute(INTROSPECT_SQL)
            rows = await cur.fetchall()
    finally:
        await conn.close()

    for relname, kind, table_comment, attname, data_type, col_comment in rows:
        info = tables.setdefault(
            relname,
            TableInfo(name=relname, kind=kind, columns=(), comment=table_comment or ""),
        )
        cols = list(info.columns)
        cols.append(ColumnInfo(name=attname, data_type=data_type, comment=col_comment or ""))
        tables[relname] = TableInfo(
            name=info.name, kind=info.kind, columns=tuple(cols), comment=info.comment
        )
    return tables
