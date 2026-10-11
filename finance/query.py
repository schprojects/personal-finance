"""Transactions filter: plain text xor SQL-like query (dashboard-specs).

The SQL view is a read-only, temporary sqlite table `t` built from the shown
rows (columns: id, date, particulars, account, deposit, withdrawal, kind,
category, fy ...). Users type either a full `SELECT * FROM t WHERE ...`, a
`SELECT * WHERE ...`, or just `WHERE ...`; only the WHERE condition is used, so
the query can never change data or leave table `t`.
"""
import re
import sqlite3

_WHERE = re.compile(r"\bwhere\b(.*)$", re.I | re.S)
_EMPTY = re.compile(r"\b([A-Za-z_]\w*)\s+is\s+empty\b", re.I)
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|pragma|replace|vacuum)\b", re.I)


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


# Placeholder values that count as "empty" per column: the database never
# stores a blank kind, so `review` is the unreviewed kind; `UNKNOWN` is the
# unclassified category.
EMPTY_VALUES = {"kind": ("", "review"), "category": ("", "UNKNOWN")}


def _empty_sql(m):
    col = m.group(1)
    vals = EMPTY_VALUES.get(col.lower(), ("",))
    return f"({col} IN ({', '.join(repr(v) for v in vals)}))"


def where_clause(sql):
    """The WHERE condition of a user query; ValueError when absent/unsafe."""
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise ValueError("empty query")
    if ";" in sql or _FORBIDDEN.search(sql):
        raise ValueError("only a single SELECT ... WHERE condition is allowed")
    m = _WHERE.search(sql)
    if not m or not m.group(1).strip():
        raise ValueError("query needs a WHERE condition, "
                         "e.g. where kind = '' and particulars like '%x%'")
    # "kind is empty" -> unclassified: blank, or the UNKNOWN placeholder.
    return _EMPTY.sub(_empty_sql, m.group(1).strip())


def run_query(rows, sql, id_key="id"):
    """Rows matching the query's WHERE condition (original dicts, in order).
    `<col> is empty` is shorthand for blank-or-placeholder (EMPTY_VALUES);
    `<col> = ''` matches only blanks."""
    cond = where_clause(sql)
    if not rows:
        return []
    columns = sorted({k for r in rows for k in r})
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE TABLE t (" + ", ".join(_quote(c) for c in columns) + ")")
        conn.executemany(
            f"INSERT INTO t VALUES ({','.join('?' * len(columns))})",
            [[_cell(r.get(c)) for c in columns] for r in rows])
        ids = {r[0] for r in conn.execute(
            f"SELECT {_quote(id_key)} FROM t WHERE {cond}").fetchall()}
    except sqlite3.Error as exc:
        raise ValueError(f"invalid query: {exc}") from exc
    finally:
        conn.close()
    return [r for r in rows if r.get(id_key) in ids]


def _cell(v):
    return "" if v is None else v if isinstance(v, (int, float, str)) else str(v)


def filter_rows(rows, text="", sql=""):
    """Apply the string filter or the SQL query — never both (ValueError)."""
    text, sql = (text or "").strip(), (sql or "").strip()
    if text and sql:
        raise ValueError("use either the text filter or the SQL query, not both")
    if sql:
        return run_query(rows, sql)
    if text:
        t = text.lower()
        return [r for r in rows if t in " ".join(
            str(r.get(k, "")) for k in ("particulars", "kind", "category",
                                        "account")).lower()]
    return list(rows)
