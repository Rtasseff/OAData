# Handoff: Read-Only Access to the Central Publication Database

**Audience:** a Claude Code session setting up a *different* app that needs to read the
same internal publication database used by the OA Archive Tracker (`oa`).

**Scope:** this document covers **access only** — how to connect and where the
credentials live. It deliberately does **not** describe the schema (tables, columns,
relationships). If the new app needs schema details, read `src/oa_tracker/pub_db.py`
and `docs/mandate_classification.md` in the OAData project, which document the queries
already in use.

---

## What you're connecting to

- **Engine:** MariaDB 5.5.x (MySQL wire protocol, `mysql_native_password` auth)
- **Host:** `intranet.cicbiomagune.es`
- **Port:** `3306`
- **Database:** `publications`
- **User:** `rtasseff`
- **Access level:** **read-only.** The account is used for `SELECT` only. Do not issue
  `INSERT`/`UPDATE`/`DELETE`/DDL — this is a shared production database that other people
  and systems depend on. Treat it like the OAData scanner treats the folder tree:
  observe, never modify.

This host is on the institution's internal network. The connection only works from a
machine that can reach `intranet.cicbiomagune.es:3306` — i.e. on-site or over VPN.
Off-network it will fail with a connection/timeout error; that's expected, not a
misconfiguration.

---

## Where the credentials live

Credentials are **not** in any repository, environment variable, or code. They live in a
single MySQL option file in the user's home directory:

```
~/.my.cnf      (mode 600 — readable/writable by the owner only)
```

The file's `600` permissions **are** the security boundary. Do not loosen them, do not
copy the file into a project directory, and do not print or log its contents.

The file contains these keys (values omitted here — read them from the file itself):

```
host
port
user
password
database
```

The `password` value exists **only** in this file. Never commit it, echo it, or write it
into the new app's config, logs, or error messages.

---

## How to connect

The OA Tracker uses **PyMySQL** and lets the client read credentials directly from
`~/.my.cnf` via `read_default_file`, so no secret ever appears in code. Mirror that exact
pattern in the new app.

**Dependency:**

```
pymysql>=1.1
```

**Connection helper** (this is the proven pattern from
`OAData/src/oa_tracker/pub_db.py`):

```python
import os
import pymysql
import pymysql.cursors

_CNF_PATH = os.path.expanduser("~/.my.cnf")


def get_connection() -> pymysql.connections.Connection:
    """Open a read-only connection using ~/.my.cnf for credentials."""
    return pymysql.connect(
        read_default_file=_CNF_PATH,
        user="rtasseff",
        database="publications",
        cursorclass=pymysql.cursors.DictCursor,
    )
```

Notes on the pattern:
- `read_default_file=_CNF_PATH` makes PyMySQL pull host/port/password from `~/.my.cnf`.
  Passing `user` and `database` explicitly is belt-and-suspenders; they also exist in the
  file.
- `DictCursor` returns rows as dicts (`row["column_name"]`) rather than tuples — convenient,
  optional.
- Open per-task and close when done (or use a `with` block). The OAData scanner opens one
  connection per run.

**Graceful failure:** the database may be unreachable (off VPN, intranet down, maintenance).
Wrap the connect call and degrade rather than crash — that's what OAData does on each scan:

```python
try:
    conn = get_connection()
except Exception as e:
    # log and continue with cached/empty data; do not abort the whole run
    ...
```

---

## Verify access (run this to confirm setup works)

Once `pymysql` is installed and `~/.my.cnf` is present on the machine, this one-liner
confirms connectivity, auth, and read access without touching any data:

```bash
python -c "import pymysql, os; \
c=pymysql.connect(read_default_file=os.path.expanduser('~/.my.cnf'), \
user='rtasseff', database='publications'); \
cur=c.cursor(); cur.execute('SELECT 1'); print('OK', cur.fetchone()); c.close()"
```

Expected output: `OK (1,)`.

If you get a connection error, check (in this order): on-network/VPN reachability of
`intranet.cicbiomagune.es:3306`, that `~/.my.cnf` exists with mode 600, and that the
password in it is current.

---

## Rules for the new app

1. **Read-only.** `SELECT` only. No writes of any kind to `publications`.
2. **Never expose the credentials.** No secrets in code, config, logs, env vars, or commits.
   The only home for the password is `~/.my.cnf` at mode 600.
3. **Reuse the connection pattern**, don't reinvent it — `read_default_file` keeps secrets
   out of code by design.
4. **Fail soft** when the DB is unreachable; assume the network won't always be there.
5. **Schema lives elsewhere.** For table/column details, consult OAData's `pub_db.py` and
   `docs/mandate_classification.md` rather than re-deriving them.
