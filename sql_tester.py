#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQLite GUI Tester (pure Python 3.10, standard library only)

Features
--------
- Protected settings database (settings.db) that stores ONLY app settings:
  - db_dir: directory containing test databases (default: ./test_dbs)
  - last_db: last selected database path
  This DB is never shown in the left DB tree and never affected by destructive actions.
- Templates store (templates.db) with two tables:
  - query_templates(id, name, category, description, sql)
  - db_templates(id, name, category, description, schema_sql, data_sql)
  First run seeds a handful of examples. You can import thousands later.
- Tab #1 (Tester):
  Left:
    - DB schema template combobox + "Include data" checkbutton + "Apply"
    - Tree: Databases → Tables → Columns (type)
  Right:
    - Query template combobox + "Apply"
    - Query editor (save/open/run)
    - Results grid (SELECT shows rows; otherwise a status message).
- Tab #2 (SQL Templates):
  Left: Hierarchical tree of categories (DDL, DQL/SELECT, DML, DCL, TCL ... with subpaths like "DDL/Create")
  Right: Table of templates (Name, Description). Double-click or press "Load to Tester" to send SQL to Tab #1.
- Tab #3 (DB Templates):
  Left: Category list
  Right: Table of DB templates (Name, Description) + buttons:
        "Preview schema", "Preview data", "Apply to active DB..."
        (Large SQL shown in a separate window when previewing.)
- Resizable layout: every element expands proportionally with window resize.
- File menu: New Test DB..., Refresh, Exit
- Settings menu: Choose DB Directory… (stored in settings.db)
- Templates menu: Import from JSON / SQL / TXT directory (optional bulk import)

Limitations by design
---------------------
- No third-party libraries; no network; SQLite only.
- Multi-statement execution is supported; the final SELECT (if any) is displayed.
- For very wide result sets, Treeview shows the first 200 columns (configurable).

Author’s notes
--------------
- Keep this file next to the generated settings.db/templates.db. You can move test DBs anywhere; just point the app at the folder.
- Import formats are documented in the code (search for "IMPORT FORMAT").
"""

from __future__ import annotations

import re
import os
import sys
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Tuple, Dict

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter import scrolledtext

import formatter

APP_TITLE = "SQLite GUI Tester (Pure Python)"
APP_DIR = Path(__file__).resolve().parent
SETTINGS_DB = APP_DIR / "settings.db"
TEMPLATES_DB = APP_DIR / "templates.db"
DEFAULT_DB_DIR = APP_DIR / "test_dbs"
RESULT_MAX_ROWS = 10000
RESULT_MAX_COLS = 200


# ------------------------ Utilities ------------------------ #

def safe_connect(db_path: Path) -> sqlite3.Connection:
    """Connect with sane defaults: foreign keys on, row factory to tuples."""
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys = ON;")
    return con


def is_sqlite_file(path: Path) -> bool:
    """Rough filter for .db/.sqlite files."""
    if not path.is_file():
        return False
    return path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}


def split_statements(sql: str) -> List[str]:
    """
    Minimal SQL splitter for SQLite. Handles semicolons in strings poorly by design,
    but is adequate for regular scripts. For advanced parsing, prefer a dedicated parser.
    """
    out, buf, in_s, qt = [], [], False, ""
    for ch in sql:
        if ch in ("'", '"'):
            if not in_s:
                in_s, qt = True, ch
            elif qt == ch:
                in_s = False
            buf.append(ch)
        elif ch == ";" and not in_s:
            seg = "".join(buf).strip()
            if seg:
                out.append(seg)
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


def confirm(parent, title: str, text: str) -> bool:
    return messagebox.askyesno(title=title, message=text, parent=parent)


# ------------------------ Settings Manager ------------------------ #

class Settings:
    """Wrapper over settings.db (protected)."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init()

    def _init(self) -> None:
        con = safe_connect(self.db_path)
        with con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
        con.close()
        if not DEFAULT_DB_DIR.exists():
            DEFAULT_DB_DIR.mkdir(parents=True, exist_ok=True)
        if self.get("db_dir") is None:
            self.set("db_dir", str(DEFAULT_DB_DIR))

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        con = safe_connect(self.db_path)
        try:
            cur = con.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
            row = cur.fetchone()
            return row[0] if row else default
        finally:
            con.close()

    def set(self, key: str, value: str) -> None:
        con = safe_connect(self.db_path)
        with con:
            con.execute("INSERT INTO app_settings(key, value) VALUES(?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, value))
        con.close()

    # Helpers
    def db_dir(self) -> Path:
        v = self.get("db_dir", str(DEFAULT_DB_DIR))
        return Path(v)


# ------------------------ Templates Store ------------------------ #

class TemplatesStore:
    """Wrapper over templates.db; holds both query and DB templates."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init()

    def _init(self) -> None:
        con = safe_connect(self.db_path)
        with con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS query_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL, -- e.g., "DDL/Create", "DQL/Select/Joins"
                    description TEXT,
                    sql TEXT NOT NULL
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS db_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL, -- e.g., "Learning", "Star Schema", "Demo/Shop"
                    description TEXT,
                    schema_sql TEXT NOT NULL,
                    data_sql TEXT
                )
            """)
        # Seed minimal examples once
        cur = con.execute("SELECT COUNT(*) FROM query_templates")
        qt_count = cur.fetchone()[0]
        if qt_count == 0:
            self._seed(con)
        con.close()

    def _seed(self, con: sqlite3.Connection) -> None:
        # --- a few query templates
        con.executemany("""
            INSERT INTO query_templates(name, category, description, sql)
            VALUES(?, ?, ?, ?)
        """, [
            ("Create table people", "DDL/Create",
             "Simple CREATE TABLE with constraints",
             "CREATE TABLE IF NOT EXISTS people(\n"
             "  id INTEGER PRIMARY KEY,\n"
             "  name TEXT NOT NULL,\n"
             "  age INTEGER CHECK(age >= 0),\n"
             "  city TEXT\n"
             ");"),
            ("Insert sample people", "DML/Insert",
             "Insert a few sample rows",
             "INSERT INTO people(name, age, city) VALUES\n"
             "('Alice', 30, 'NYC'),\n"
             "('Bob', 24, 'LA'),\n"
             "('Carol', 42, 'Chicago');"),
            ("Select all people", "DQL/Select",
             "Read all rows from people",
             "SELECT id, name, age, city FROM people;"),
            ("Transaction example", "TCL/Transaction",
             "BEGIN/COMMIT with two inserts",
             "BEGIN;\n"
             "INSERT INTO people(name, age) VALUES('TX A', 20);\n"
             "INSERT INTO people(name, age) VALUES('TX B', 21);\n"
             "COMMIT;"),
            ("Grant (noop)", "DCL/Grant",
             "SQLite has no GRANT; kept for completeness.",
             "-- SQLite does not implement GRANT/REVOKE. This is a placeholder."),
        ])
        # --- a couple DB templates
        con.executemany("""
            INSERT INTO db_templates(name, category, description, schema_sql, data_sql)
            VALUES(?, ?, ?, ?, ?)
        """, [
            ("Tiny People DB", "Learning",
             "Single table 'people' + sample data.",
             "DROP TABLE IF EXISTS people;\n"
             "CREATE TABLE people(\n"
             "  id INTEGER PRIMARY KEY,\n"
             "  name TEXT NOT NULL,\n"
             "  age INTEGER CHECK(age>=0),\n"
             "  city TEXT\n"
             ");",
             "INSERT INTO people(name, age, city) VALUES\n"
             "('Ada', 36, 'London'),\n"
             "('Linus', 28, 'Helsinki'),\n"
             "('Guido', 45, 'Amsterdam');"),
            ("Shop (Star-ish)", "Demo/Shop",
             "Products, Customers, Orders (minimal).",
             "DROP TABLE IF EXISTS order_items;\n"
             "DROP TABLE IF EXISTS orders;\n"
             "DROP TABLE IF EXISTS products;\n"
             "DROP TABLE IF EXISTS customers;\n"
             "CREATE TABLE products(\n"
             "  product_id INTEGER PRIMARY KEY,\n"
             "  name TEXT NOT NULL,\n"
             "  price REAL NOT NULL\n"
             ");\n"
             "CREATE TABLE customers(\n"
             "  customer_id INTEGER PRIMARY KEY,\n"
             "  name TEXT NOT NULL,\n"
             "  email TEXT UNIQUE\n"
             ");\n"
             "CREATE TABLE orders(\n"
             "  order_id INTEGER PRIMARY KEY,\n"
             "  customer_id INTEGER NOT NULL,\n"
             "  created_at TEXT NOT NULL,\n"
             "  FOREIGN KEY(customer_id) REFERENCES customers(customer_id)\n"
             ");\n"
             "CREATE TABLE order_items(\n"
             "  order_id INTEGER NOT NULL,\n"
             "  product_id INTEGER NOT NULL,\n"
             "  qty INTEGER NOT NULL CHECK(qty>0),\n"
             "  PRIMARY KEY(order_id, product_id),\n"
             "  FOREIGN KEY(order_id) REFERENCES orders(order_id),\n"
             "  FOREIGN KEY(product_id) REFERENCES products(product_id)\n"
             ");",
             "INSERT INTO products(name, price) VALUES\n"
             "('Keyboard', 49.9), ('Mouse', 25.0), ('Monitor', 199.0);\n"
             "INSERT INTO customers(name, email) VALUES\n"
             "('Alice', 'a@example.com'), ('Bob', 'b@example.com');\n"
             "INSERT INTO orders(customer_id, created_at) VALUES\n"
             "(1, datetime('now')), (2, datetime('now'));\n"
             "INSERT INTO order_items(order_id, product_id, qty) VALUES\n"
             "(1, 1, 1), (1, 2, 2), (2, 3, 1);")
        ])

    # --- Query templates API ---
    def query_categories(self) -> List[str]:
        con = safe_connect(self.db_path)
        cur = con.execute("SELECT DISTINCT category FROM query_templates ORDER BY category")
        rows = [r[0] for r in cur.fetchall()]
        con.close()
        return rows

    def query_templates_by_category(self, category: str) -> List[Tuple[int, str, str, str]]:
        con = safe_connect(self.db_path)
        cur = con.execute(
            "SELECT id, name, description, sql FROM query_templates WHERE category=? ORDER BY name",
            (category,))
        rows = cur.fetchall()
        con.close()
        return rows

    def all_query_templates(self) -> List[Tuple[int, str, str, str, str]]:
        con = safe_connect(self.db_path)
        cur = con.execute(
            "SELECT id, name, category, description, sql FROM query_templates ORDER BY category, name")
        rows = cur.fetchall()
        con.close()
        return rows

    def get_query_sql(self, template_id: int) -> str:
        con = safe_connect(self.db_path)
        cur = con.execute("SELECT sql FROM query_templates WHERE id=?", (template_id,))
        row = cur.fetchone()
        con.close()
        return row[0] if row else ""

    # --- DB templates API ---
    def db_categories(self) -> List[str]:
        con = safe_connect(self.db_path)
        cur = con.execute("SELECT DISTINCT category FROM db_templates ORDER BY category")
        rows = [r[0] for r in cur.fetchall()]
        con.close()
        return rows

    def db_templates_by_category(self, category: str) -> List[Tuple[int, str, str]]:
        con = safe_connect(self.db_path)
        cur = con.execute(
            "SELECT id, name, description FROM db_templates WHERE category=? ORDER BY name",
            (category,))
        rows = cur.fetchall()
        con.close()
        return rows

    def get_db_template(self, template_id: int) -> Tuple[str, str, str, str, str]:
        con = safe_connect(self.db_path)
        cur = con.execute(
            "SELECT id, name, category, description, schema_sql, data_sql FROM db_templates WHERE id=?",
            (template_id,))
        row = cur.fetchone()
        con.close()
        if not row:
            raise KeyError(f"DB template id {template_id} not found")
        return row  # id, name, category, description, schema_sql, data_sql

    # --- Import helpers (bulk) ---
    # IMPORT FORMAT (JSON):
    #   {
    #     "query_templates": [{"name": "...", "category": "...", "description": "...", "sql": "..."}, ...],
    #     "db_templates": [{"name": "...", "category": "...", "description": "...", "schema_sql": "...", "data_sql": "..."}, ...]
    #   }
    # IMPORT FORMAT (folder of .sql/.txt):
    #   - Query templates: files under folders named as categories, e.g. ./import/DDL/Create/Create_people.sql
    #   - DB templates: put schema and optional data files next to each other:
    #       ./import_db/Learning/Tiny People DB.schema.sql
    #       ./import_db/Learning/Tiny People DB.data.sql  (optional)
    #
    def import_from_json(self, json_path: Path) -> int:
        import json
        con = safe_connect(self.db_path)
        count = 0
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        with con:
            for it in data.get("query_templates", []):
                con.execute("""INSERT INTO query_templates(name, category, description, sql)
                               VALUES(?, ?, ?, ?)""",
                            (it["name"], it["category"], it.get("description"), it["sql"]))
                count += 1
            for it in data.get("db_templates", []):
                con.execute("""INSERT INTO db_templates(name, category, description, schema_sql, data_sql)
                               VALUES(?, ?, ?, ?, ?)""",
                            (it["name"], it["category"], it.get("description"),
                             it["schema_sql"], it.get("data_sql")))
                count += 1
        con.close()
        return count


# ------------------------ Main Application ------------------------ #

class App(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title(APP_TITLE)
        self.geometry("1200x700")
        self.minsize(900, 520)

        # Managers
        self.settings = Settings(SETTINGS_DB)   # Protected DB
        self.templates = TemplatesStore(TEMPLATES_DB)

        # State
        self.active_db: Optional[Path] = None
        self.db_nodes: Dict[str, Path] = {}  # tree id -> db path

        # UI
        self._build_menu()
        self._build_tabs()
        self._load_initial_state()

    # ---------- Menus ---------- #

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="New Test DB...", command=self._menu_new_test_db)
        file_menu.add_command(label="Refresh", command=self._refresh_all)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="Choose DB Directory…", command=self._choose_db_dir)
        menubar.add_cascade(label="Settings", menu=settings_menu)

        import_menu = tk.Menu(menubar, tearoff=0)
        import_menu.add_command(label="Import Templates from JSON...", command=self._import_json)
        menubar.add_cascade(label="Templates", menu=import_menu)

        self.config(menu=menubar)

    # ---------- Tabs ---------- #

    def _build_tabs(self) -> None:
        nb = ttk.Notebook(self)
        nb.grid(row=0, column=0, sticky="nsew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        # Tab 1: Tester
        self.tab_tester = ttk.Frame(nb)
        nb.add(self.tab_tester, text="Tester")

        # Tab 2: SQL Templates
        self.tab_sql_tpl = ttk.Frame(nb)
        nb.add(self.tab_sql_tpl, text="Templates of SQL queries")

        # Tab 3: DB Templates
        self.tab_db_tpl = ttk.Frame(nb)
        nb.add(self.tab_db_tpl, text="Templates of databases")

        self._build_tab_tester(self.tab_tester)
        self._build_tab_sql_templates(self.tab_sql_tpl)
        self._build_tab_db_templates(self.tab_db_tpl)

    # ---------- Tab 1 (Tester) ---------- #

    def _build_tab_tester(self, parent: ttk.Frame) -> None:
        root_pane = ttk.Panedwindow(parent, orient=tk.HORIZONTAL)
        root_pane.grid(row=0, column=0, sticky="nsew")
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)

        # Left side (templates + DB tree)
        left = ttk.Frame(root_pane)
        root_pane.add(left, weight=1)

        # Top: DB schema template combobox + include data + apply
        lf = ttk.LabelFrame(left, text="Database template")
        lf.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        for i in range(4):
            lf.columnconfigure(i, weight=1)

        self.var_db_template = tk.StringVar()
        self.cmb_db_template = ttk.Combobox(lf, textvariable=self.var_db_template, state="readonly")
        self.cmb_db_template.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=6)

        self.var_include_data = tk.BooleanVar(value=True)
        chk = ttk.Checkbutton(lf, text="Include data", variable=self.var_include_data)
        chk.grid(row=0, column=2, sticky="w", padx=6, pady=6)

        btn_apply_db_tpl = ttk.Button(lf, text="Apply to selected DB", command=self._apply_db_template_to_active)
        btn_apply_db_tpl.grid(row=0, column=3, sticky="e", padx=6, pady=6)


        # DB Tree
        db_frame = ttk.LabelFrame(left, text="Databases / Tables / Columns")
        db_frame.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        # Current values
        current_db_frame = ttk.LabelFrame(db_frame, text="Current values")
        current_db_frame.grid(row=0, column=0, sticky="ew", padx=6, pady=(0, 6))

        # ---- Row 0: Current database name ----
        lbl_db = ttk.Label(current_db_frame, text="Database:")
        lbl_db.grid(row=0, column=0, sticky="w", padx=6, pady=6)

        self.var_db = tk.StringVar()
        self.ent_db = ttk.Entry(current_db_frame, textvariable=self.var_db)
        self.ent_db.grid(row=0, column=1, sticky="ew", padx=6, pady=6)

        # ---- Row 1: Current table ----
        lbl_table = ttk.Label(current_db_frame, text="Table:")
        lbl_table.grid(row=1, column=0, sticky="w", padx=6, pady=6)

        self.var_table = tk.StringVar()
        self.ent_table = ttk.Entry(current_db_frame, textvariable=self.var_table)
        self.ent_table.grid(row=1, column=1, sticky="ew", padx=6, pady=6)

        # ---- Row 2: Current column ----
        lbl_column = ttk.Label(current_db_frame, text="Column:")
        lbl_column.grid(row=2, column=0, sticky="w", padx=6, pady=6)

        self.var_column = tk.StringVar()
        self.ent_column = ttk.Entry(current_db_frame, textvariable=self.var_column)
        self.ent_column.grid(row=2, column=1, sticky="ew", padx=6, pady=6)

        # Expand column 1
        current_db_frame.rowconfigure(0, weight=0)
        current_db_frame.rowconfigure(1, weight=0)
        current_db_frame.rowconfigure(2, weight=0)
        current_db_frame.columnconfigure(0, weight=0)
        current_db_frame.columnconfigure(1, weight=1)

        self.tree = ttk.Treeview(db_frame, columns=("info",), show="tree")
        self.tree.grid(row=1, column=0, sticky="nsew")

        db_frame.rowconfigure(0, weight=0)
        db_frame.rowconfigure(1, weight=1)
        db_frame.columnconfigure(0, weight=1)

        sb = ttk.Scrollbar(db_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscroll=sb.set)
        sb.grid(row=0, column=1, sticky="ns")

        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        # Right side (query template + editor + results)
        right = ttk.Frame(root_pane)
        root_pane.add(right, weight=3)
        right.rowconfigure(1, weight=1)
        right.rowconfigure(2, weight=1)
        right.columnconfigure(0, weight=1)

        # Top: query templates combobox + apply
        qlf = ttk.LabelFrame(right, text="Query template")
        qlf.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        qlf.columnconfigure(0, weight=1)

        self.var_q_template = tk.StringVar()
        self.cmb_q_template = ttk.Combobox(qlf, textvariable=self.var_q_template, state="readonly")
        self.cmb_q_template.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        ttk.Button(qlf, text="Apply", command=self._apply_query_template).grid(row=0, column=1, padx=6, pady=6)

        # Query editor + buttons
        editor_frame = ttk.LabelFrame(right, text="SQL")
        editor_frame.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))

        # bottom row for buttons
        editor_frame.rowconfigure(0, weight=1)
        editor_frame.rowconfigure(1, weight=0)
        # columns: [0][1]  [2=spacer]  [3][4]
        editor_frame.columnconfigure(0, weight=0)
        editor_frame.columnconfigure(1, weight=0)
        editor_frame.columnconfigure(2, weight=1)   # <— spacer grows
        editor_frame.columnconfigure(3, weight=0)
        editor_frame.columnconfigure(4, weight=0)

        self.txt_sql = scrolledtext.ScrolledText(editor_frame, wrap="none", height=10)
        self.txt_sql.grid(row=0, column=0, columnspan=4, sticky="nsew", padx=6, pady=6)

        ttk.Button(editor_frame, text="Save", command=self._save_sql).grid(row=1, column=0, sticky="w", padx=6, pady=6)
        ttk.Button(editor_frame, text="Open", command=self._open_sql).grid(row=1, column=1, sticky="w", padx=6, pady=6)
        ttk.Button(editor_frame, text="Format", command=self._format_txt_sql).grid(row=1, column=2, sticky="e", padx=6, pady=6)
        ttk.Button(editor_frame, text="Run", command=self._run_sql).grid(row=1, column=3, sticky="e", padx=6, pady=6)

        # Results area
        res_frame = ttk.LabelFrame(right, text="Result")
        res_frame.grid(row=2, column=0, sticky="nsew", padx=6, pady=(0, 6))
        res_frame.rowconfigure(0, weight=1)
        res_frame.columnconfigure(0, weight=1)

        self.res_tree = ttk.Treeview(res_frame, columns=(), show="headings")
        self.res_tree.grid(row=0, column=0, sticky="nsew")
        rsb_y = ttk.Scrollbar(res_frame, orient="vertical", command=self.res_tree.yview)
        self.res_tree.configure(yscroll=rsb_y.set)
        rsb_y.grid(row=0, column=1, sticky="ns")
        rsb_x = ttk.Scrollbar(res_frame, orient="horizontal", command=self.res_tree.xview)
        self.res_tree.configure(xscroll=rsb_x.set)
        rsb_x.grid(row=1, column=0, sticky="ew")

        self.res_msg = scrolledtext.ScrolledText(res_frame, wrap="word", height=6)
        # Shown only when needed (error or non-SELECT info).
        self.res_msg.grid_remove()

        # Status
        self.status = tk.StringVar(value="Ready.")
        ttk.Label(parent, textvariable=self.status, anchor="w").grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 6))

    # ---------- Tab 2 (SQL Templates) ---------- #

    def _build_tab_sql_templates(self, parent: ttk.Frame) -> None:

        pane = ttk.Panedwindow(parent, orient=tk.HORIZONTAL)
        pane.grid(row=0, column=0, sticky="nsew")
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)

        left = ttk.Frame(pane)
        pane.add(left, weight=1)
        right = ttk.Frame(pane)
        pane.add(right, weight=3)

        # Left: category tree
        lf = ttk.LabelFrame(left, text="Categories")
        lf.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self.tree_qcat = ttk.Treeview(lf, show="tree")
        self.tree_qcat.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.tree_qcat.yview)
        self.tree_qcat.configure(yscroll=sb.set)
        sb.grid(row=0, column=1, sticky="ns")

        self.tree_qcat.bind("<<TreeviewSelect>>", self._on_qcat_select)

        lf.rowconfigure(0, weight=1)
        lf.columnconfigure(0, weight=1)

        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        # Right: table of templates
        rf = ttk.LabelFrame(right, text="Templates")
        rf.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)

        bbar = ttk.Frame(rf)
        bbar.grid(row=1, column=0, sticky="ew", padx=6, pady=6)
        ttk.Button(bbar, text="Load to Tester", command=self._load_selected_qtpl_to_tester).pack(side="left")

        self.tbl_qtpl = ttk.Treeview(rf, columns=("name", "desc", "id"), show="headings")
        self.tbl_qtpl.heading("name", text="Name")
        self.tbl_qtpl.heading("desc", text="Description")
        self.tbl_qtpl.heading("id", text="ID")
        self.tbl_qtpl.column("name", width=280, anchor="w")
        self.tbl_qtpl.column("desc", width=600, anchor="w")
        self.tbl_qtpl.column("id", width=60, anchor="e")
        self.tbl_qtpl.grid(row=0, column=0, sticky="nsew")

        sb2 = ttk.Scrollbar(rf, orient="vertical", command=self.tbl_qtpl.yview)
        self.tbl_qtpl.configure(yscroll=sb2.set)
        sb2.grid(row=0, column=1, sticky="ns")

        self.tbl_qtpl.bind("<Double-1>", self._on_qtpl_double)

        rf.rowconfigure(0, weight=1)
        rf.columnconfigure(0, weight=1)

        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

    # ---------- Tab 3 (DB Templates) ---------- #

    def _build_tab_db_templates(self, parent: ttk.Frame) -> None:
        pane = ttk.Panedwindow(parent, orient=tk.HORIZONTAL)
        pane.grid(row=0, column=0, sticky="nsew")
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)

        left = ttk.Frame(pane)
        pane.add(left, weight=1)
        right = ttk.Frame(pane)
        pane.add(right, weight=3)

        lf = ttk.LabelFrame(left, text="DB Template Categories")
        lf.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        self.lst_dbcat = tk.Listbox(lf)
        self.lst_dbcat.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(lf, orient="vertical", command=self.lst_dbcat.yview)
        self.lst_dbcat.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky="ns")
        self.lst_dbcat.bind("<<ListboxSelect>>", self._on_dbcat_select)

        lf.rowconfigure(0, weight=1)
        lf.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)

        rf = ttk.LabelFrame(right, text="DB Templates")
        rf.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        self.tbl_dbtpl = ttk.Treeview(rf, columns=("name", "desc", "id"), show="headings")
        self.tbl_dbtpl.heading("name", text="Name")
        self.tbl_dbtpl.heading("desc", text="Description")
        self.tbl_dbtpl.heading("id", text="ID")
        self.tbl_dbtpl.column("name", width=280, anchor="w")
        self.tbl_dbtpl.column("desc", width=600, anchor="w")
        self.tbl_dbtpl.column("id", width=60, anchor="e")
        self.tbl_dbtpl.grid(row=0, column=0, sticky="nsew")

        sb2 = ttk.Scrollbar(rf, orient="vertical", command=self.tbl_dbtpl.yview)
        self.tbl_dbtpl.configure(yscroll=sb2.set)
        sb2.grid(row=0, column=1, sticky="ns")

        bar = ttk.Frame(rf)
        bar.grid(row=1, column=0, sticky="ew", padx=6, pady=6)
        ttk.Button(bar, text="Preview schema", command=lambda: self._preview_db_template(part="schema")).pack(side="left")
        ttk.Button(bar, text="Preview data", command=lambda: self._preview_db_template(part="data")).pack(side="left")
        ttk.Button(bar, text="Apply to active DB...", command=self._apply_selected_db_template_to_active).pack(side="left")

        rf.rowconfigure(0, weight=1)
        rf.columnconfigure(0, weight=1)

        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

    # ---------- Load initial ---------- #

    def _load_initial_state(self) -> None:
        self._reload_db_tree()
        self._reload_db_template_combobox()
        self._reload_query_template_combobox()
        self._reload_query_category_tree()
        self._reload_db_categories_list()

        # Restore last DB if present
        last_db = self.settings.get("last_db")
        if last_db and Path(last_db).exists():
            self._set_active_db(Path(last_db))

    # ---------- Helpers: DB listing ---------- #

    def _db_dir(self) -> Path:
        return self.settings.db_dir()

    def _is_protected(self, path: Path) -> bool:
        # Never touch or list these
        return path.resolve() in {SETTINGS_DB.resolve(), TEMPLATES_DB.resolve()}

    def _reload_db_tree(self) -> None:

        selected = []
        all_select = self.tree.selection()
        for  k in all_select:
            # Collect texts going upward
            texts = []
            current = k
            while current:  # climb up parents
                texts.insert(0, self.tree.item(current, "text"))  # prepend text
                current = self.tree.parent(current)
            selected.append(texts)

        
        curr_open_nodes = {}
        def collect_open( open_nodes, node="" ):
            for child in self.tree.get_children(node):
                vals = self.tree.item(child, "values")
                # If expanded or it’s a column, remember it
                if self.tree.item(child, "open") or (vals and vals[0] == "col"):
                    #open_nodes.append(self.tree.item(child, "text"))
                    open_nodes[self.tree.item(child, "text")] = {}
                    collect_open(open_nodes[self.tree.item(child, "text")],child)
        collect_open(curr_open_nodes)
        
        self._addition_reload_db_tree()

        def restore_open(saved_dict, node=""):
            for child in self.tree.get_children(node):
                text = self.tree.item(child, "text")
                if text in saved_dict:
                    # open the node
                    self.tree.item(child, open=True)
                    vals = self.tree.item(child, "values")
                    
                    if vals and vals[0] == "table":
                        self._populate_columns(child,vals)
                            
                    # if this is a DB node, make sure tables are populated
                    if child in self.db_nodes:
                        self._populate_tables(child)

                    # recurse into children with the saved sub-dict
                    restore_open(saved_dict[text], child)
                # else: not in saved state → skip going deeper


        restore_open(curr_open_nodes)

        result_selection = []
        for selectpath in selected:
            current = ""
            stop = False
            for t in selectpath:
                founded = False
                for item_id in self.tree.get_children(current):
                    if self.tree.item(item_id, "text") == t:
                        current = item_id
                        founded = True
                if not founded:
                    stop = True
                    break
            
            if current != "" :
                result_selection.append(current)
        self.tree.selection_set(result_selection)    

    def _populate_columns(self,node,vals):
        # It’s a table node → force populate its columns
        parent_db_node = self.tree.parent(node)
        if parent_db_node in self.db_nodes:
            db_path = self.db_nodes[parent_db_node]
            table_name = vals[1]
            # remove placeholder if present
            for gc in self.tree.get_children(node):
                if self.tree.item(gc, "values")[0] == "placeholder":
                    self.tree.delete(gc)
            # add real columns
            con = safe_connect(db_path)
            try:
                cur = con.execute(f'PRAGMA table_info({table_name})')
                for cid, name, ctype, notnull, dflt, pk in cur.fetchall():
                    self.tree.insert(
                        node, "end",
                        text=f"{name} : {ctype if ctype else 'UNKNOWN'}",
                        values=("col", name)
                    )
            finally:
                con.close()

        # --- rebuild ---
        self._addition_reload_db_tree()

        # --- restore ---
        def restore(node="", prefix=""):
            for child in self.tree.get_children(node):
                text = self.tree.item(child, "text")
                path = prefix + "/" + text if prefix else text
                if path in open_paths:
                    self.tree.item(child, open=True)
                    # if it’s a DB node, populate its tables now
                    if child in self.db_nodes:
                        self._populate_tables(child)
                        # recurse into those tables
                        restore(child, path)
                else:
                    restore(child, path)
        restore()
    
    def _addition_reload_db_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.db_nodes.clear()

        root_dir = self._db_dir()
        root_id = self.tree.insert("", "end", text=f"{root_dir} (catalog)", open=True)

        for p in sorted(root_dir.glob("*")):
            if is_sqlite_file(p) and not self._is_protected(p):
                node = self.tree.insert(root_id, "end", text=p.name, values=("db",))
                self.db_nodes[node] = p
                # lazy load tables upon open
                self.tree.insert(node, "end", text="Loading...", values=("placeholder",))                

    def _on_tree_open(self, _event=None) -> None:
        sel = self.tree.focus()
        if not sel:
            return
        if sel in self.db_nodes:
            # populate tables
            self._populate_tables(sel)

    def _on_tree_select(self, _event=None) -> None:

        current_items = []
        current_items.append(self.var_db)
        current_items.append(self.var_table)
        current_items.append(self.var_column)
        for i in current_items:
            i.set("")
            
        sel = self.tree.focus()
        if not sel:
            return

        texts = []
        current = sel
        while current:  # climb up parents
            texts.insert(0, self.tree.item(current, "text"))  # prepend text
            current = self.tree.parent(current)
        #level = len(texts) - 1

        for j in range(1,len(texts)):
            current_items[j-1].set(texts[j])

        if sel in self.db_nodes:
            self._set_active_db(self.db_nodes[sel])

    def _populate_tables(self, db_node: str) -> None:
        db_path = self.db_nodes[db_node]
        # Clear children first
        for c in self.tree.get_children(db_node):
            self.tree.delete(c)

        try:
            con = safe_connect(db_path)
            cur = con.execute(
                "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY type, name")
            tables = cur.fetchall()
            for name, ttype in tables:
                tnode = self.tree.insert(db_node, "end", text=f"{name} ({ttype})", values=("table", name))
                # placeholder for columns
                self.tree.insert(tnode, "end", text="Loading...", values=("placeholder",))
            # Bind on open for columns
            self.tree.bind("<<TreeviewOpen>>", self._on_open_maybe_columns, add="+")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to list tables for {db_path.name}:\n{e}", parent=self)
        finally:
            con.close()

    def _on_open_maybe_columns(self, _event=None) -> None:
        item = self.tree.focus()
        if not item:
            return

        # Find the DB node ancestor
        node = item
        db_node = None
        while node:
            if node in self.db_nodes:
                db_node = node
                break
            node = self.tree.parent(node)
        if not db_node:
            return
        db_path = self.db_nodes[db_node]

        vals = self.tree.item(item, "values")
        if not vals:
            return

        # We get the open event on the TABLE node, not the placeholder.
        if vals[0] == "table":
            table_node = item
            children = self.tree.get_children(table_node)
            if not children:
                return
            # Only load once: do it if the first child is the placeholder.
            first = children[0]
            fvals = self.tree.item(first, "values")
            if fvals and fvals[0] == "placeholder":
                self.tree.delete(first)  # remove "Loading..."
                table_name = vals[1]
                con = safe_connect(db_path)
                try:
                    cur = con.execute(f'PRAGMA table_info({table_name})')
                    for cid, name, ctype, notnull, dflt, pk in cur.fetchall():
                        self.tree.insert(
                            table_node, "end",
                            text=f"{name} : {ctype or 'UNKNOWN'}",
                            values=("col", name)
                        )
                finally:
                    con.close()

    def _set_active_db(self, path: Path) -> None:
        if self._is_protected(path):
            messagebox.showwarning("Protected", "This database is reserved for the app and cannot be used as a test DB.", parent=self)
            return
        self.active_db = path
        self.settings.set("last_db", str(path))
        self.status.set(f"Active DB: {path}")

    # ---------- DB template combobox ---------- #

    def _reload_db_template_combobox(self) -> None:
        all_tpl = self.templates.db_categories()
        names = []
        name_to_id = {}
        for cat in all_tpl:
            for tid, nm, _desc in self.templates.db_templates_by_category(cat):
                label = f"[{cat}] {nm}"
                names.append(label)
                name_to_id[label] = tid
        self._dbtpl_name_to_id = name_to_id
        self.cmb_db_template["values"] = names
        if names:
            self.cmb_db_template.current(0)

    def _apply_db_template_to_active(self) -> None:
        if not self.active_db:
            messagebox.showinfo("No DB selected", "Select a database on the left tree first.", parent=self)
            return
        label = self.var_db_template.get()
        if not label:
            messagebox.showinfo("No template", "Choose a database template.", parent=self)
            return
        tid = self._dbtpl_name_to_id.get(label)
        if not tid:
            return
        _id, name, _cat, _desc, schema_sql, data_sql = self.templates.get_db_template(tid)

        if not confirm(self, "Apply DB Template",
                       f"This will DROP all user tables in:\n{self.active_db}\n\nand create the schema from '{name}'.\nProceed?"):
            return

        try:
            con = safe_connect(self.active_db)
            with con:
                # Drop all user tables
                cur = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
                for (tname,) in cur.fetchall():
                    con.execute(f"DROP TABLE IF EXISTS {tname}")
                # Create schema
                for stmt in split_statements(schema_sql):
                    con.execute(stmt)
                # Optional data
                if self.var_include_data.get() and data_sql:
                    con.executescript(data_sql)
            self.status.set(f"Applied DB template '{name}' to {self.active_db.name}.")
            self._reload_db_tree()
        except Exception as e:
            messagebox.showerror("Error applying template", str(e), parent=self)

    # ---------- Query template combobox ---------- #

    def _reload_query_template_combobox(self) -> None:
        items = []
        name_to_id = {}
        for _id, nm, cat, _desc, _sql in self.templates.all_query_templates():
            label = f"[{cat}] {nm}"
            items.append(label)
            name_to_id[label] = _id
        self.cmb_q_template["values"] = items
        self._qtpl_name_to_id = name_to_id
        if items:
            self.cmb_q_template.current(0)

    def _apply_query_template(self) -> None:
        label = self.var_q_template.get()
        if not label:
            return
        tid = self._qtpl_name_to_id.get(label)
        if not tid:
            return
        sql = self.templates.get_query_sql(tid)
        self.txt_sql.delete("1.0", "end")
        self.txt_sql.insert("1.0", sql)

    # ---------- Run / Save / Open ---------- #

    def _run_sql(self) -> None:
        if not self.active_db:
            messagebox.showinfo("No DB selected", "Select a database on the left tree first.", parent=self)
            return
        sql = self.txt_sql.get("1.0", "end").strip()
        if not sql:
            return
        con = safe_connect(self.active_db)
        try:
            stmts = split_statements(sql)
            last_result = None
            cols = None
            changed_total = 0
            with con:
                for i, stmt in enumerate(stmts):
                    cur = con.execute(stmt)
                    try:
                        # Try to fetch; if fails, it's a non-SELECT statement.
                        cols = [d[0] for d in cur.description] if cur.description else None
                        if cols:
                            rows = cur.fetchall()
                            last_result = (cols, rows)
                        else:
                            changed_total += con.total_changes
                    except sqlite3.Error:
                        pass
            if last_result and last_result[0]:
                self._show_table(*last_result)
                self._show_message("")  # hide
                self.status.set(f"{len(last_result[1])} rows returned.")
            else:
                self._clear_table()
                self._show_message("OK. Statements executed successfully.")
                self.status.set("Statements executed successfully.")
            self._reload_db_tree()  # schema might have changed
        except Exception as e:
            self._clear_table()
            self._show_message(f"ERROR: {e}")
            self.status.set("Error during execution (see message).")
        finally:
            con.close()

    def _save_sql(self) -> None:
        default_dir = self._db_dir()
        default_name = f"query_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sql"
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Save SQL",
            defaultextension=".sql",
            initialdir=str(default_dir),
            initialfile=default_name,
            filetypes=[("SQL Files", "*.sql"), ("All Files", "*.*")]
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.txt_sql.get("1.0", "end"))
        self.status.set(f"Saved to {path}")

    def _open_sql(self) -> None:
        path = filedialog.askopenfilename(
            parent=self,
            title="Open SQL",
            initialdir=str(self._db_dir()),
            filetypes=[("SQL Files", "*.sql"), ("All Files", "*.*")]
        )
        if not path:
            return
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        self.txt_sql.delete("1.0", "end")
        self.txt_sql.insert("1.0", text)
        self.status.set(f"Loaded {path}")

    def _format_txt_sql(self) -> None:
        previous_sql_text = self.txt_sql.get("1.0", "end")
        self.txt_sql.delete("1.0", "end")
        fsql = formatter.SQLFormatter(previous_sql_text)
        formatted, errors = fsql.format_and_check()
        #print("errors: " + str(errors))
        #print("formatted: " + str(formatted))
        highlight_sql(self.txt_sql, formatted)
        #self.txt_sql.insert("1.0", formatted)

    # ---------- Result presentation ---------- #

    def _clear_table(self) -> None:
        self.res_tree["columns"] = ()
        for c in self.res_tree.get_children():
            self.res_tree.delete(c)

    def _show_table(self, columns: List[str], rows: List[Tuple]) -> None:
        # Limit columns if huge
        columns = columns[:RESULT_MAX_COLS]
        self.res_tree["columns"] = columns
        self.res_tree["show"] = "headings"
        for col in columns:
            self.res_tree.heading(col, text=col)
            self.res_tree.column(col, width=120, anchor="w")
        for c in self.res_tree.get_children():
            self.res_tree.delete(c)
        # Insert rows up to cap
        for r in rows[:RESULT_MAX_ROWS]:
            self.res_tree.insert("", "end", values=r[:RESULT_MAX_COLS])
        self.res_tree.grid()    # ensure visible
        self.res_msg.grid_remove()

    def _show_message(self, msg: str) -> None:
        if msg:
            self.res_msg.grid(row=0, column=0, sticky="nsew")
            self.res_msg.delete("1.0", "end")
            self.res_msg.insert("1.0", msg)
            self.res_tree.grid_remove()
        else:
            self.res_msg.grid_remove()
            self.res_tree.grid()

    # ---------- SQL Templates tab logic ---------- #

    def _reload_query_category_tree(self) -> None:
        self.tree_qcat.delete(*self.tree_qcat.get_children())
        cats = self.templates.query_categories()
        # Build a hierarchical tree from "A/B/C"
        roots: Dict[str, str] = {}  # path -> node id
        for cat in cats:
            parts = cat.split("/")
            path = ""
            parent = ""
            for part in parts:
                path = f"{path}/{part}" if path else part
                if path not in roots:
                    nid = self.tree_qcat.insert(parent, "end", text=part, open=True, values=(path,))
                    roots[path] = nid
                    parent = nid
                else:
                    parent = roots[path]

    def _on_qcat_select(self, _event=None) -> None:
        sel = self.tree_qcat.focus()
        if not sel:
            return
        vals = self.tree_qcat.item(sel, "values")
        if not vals:
            return
        cat = vals[0]
        # load all templates whose category startswith the selected path
        rows = []
        for _id, nm, ccat, desc, _sql in self.templates.all_query_templates():
            if ccat.startswith(cat):
                rows.append((_id, nm, desc))
        self.tbl_qtpl.delete(*self.tbl_qtpl.get_children())
        for _id, nm, desc in rows:
            self.tbl_qtpl.insert("", "end", values=(nm, desc or "", _id))

    def _on_qtpl_double(self, _event=None) -> None:
        self._load_selected_qtpl_to_tester()

    def _load_selected_qtpl_to_tester(self) -> None:
        sel = self.tbl_qtpl.focus()
        if not sel:
            return
        vals = self.tbl_qtpl.item(sel, "values")
        if not vals:
            return
        _name, _desc, _id = vals
        sql = self.templates.get_query_sql(int(_id))
        # Switch to tester tab and load
        self.txt_sql.delete("1.0", "end")
        self.txt_sql.insert("1.0", sql)
        self.status.set(f"Loaded query template '{_name}' to editor.")

    # ---------- DB Templates tab logic ---------- #

    def _reload_db_categories_list(self) -> None:
        self.lst_dbcat.delete(0, "end")
        for cat in self.templates.db_categories():
            self.lst_dbcat.insert("end", cat)
        if self.lst_dbcat.size() > 0:
            self.lst_dbcat.selection_set(0)
            self._on_dbcat_select()

    def _on_dbcat_select(self, _event=None) -> None:
        sel = self.lst_dbcat.curselection()
        if not sel:
            return
        cat = self.lst_dbcat.get(sel[0])
        rows = self.templates.db_templates_by_category(cat)
        self.tbl_dbtpl.delete(*self.tbl_dbtpl.get_children())
        for _id, nm, desc in rows:
            self.tbl_dbtpl.insert("", "end", values=(nm, desc or "", _id))

    def _selected_dbtpl_id(self) -> Optional[int]:
        sel = self.tbl_dbtpl.focus()
        if not sel:
            return None
        vals = self.tbl_dbtpl.item(sel, "values")
        if not vals:
            return None
        return int(vals[2])

    def _preview_db_template(self, part: str) -> None:
        tid = self._selected_dbtpl_id()
        if not tid:
            return
        _id, name, _cat, _desc, schema_sql, data_sql = self.templates.get_db_template(tid)
        text = schema_sql if part == "schema" else (data_sql or "-- (no data_sql)")
        self._show_big_text(f"{name} — {part.upper()} preview", text)

    def _apply_selected_db_template_to_active(self) -> None:
        tid = self._selected_dbtpl_id()
        if not tid:
            return
        _id, name, _cat, _desc, schema_sql, data_sql = self.templates.get_db_template(tid)
        if not self.active_db:
            messagebox.showinfo("No DB selected", "Select a database in the Tester tab first.", parent=self)
            return
        if not confirm(self, "Apply DB Template",
                       f"This will DROP all user tables in:\n{self.active_db}\n\nand create the schema from '{name}'.\nProceed?"):
            return
        try:
            con = safe_connect(self.active_db)
            with con:
                cur = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
                for (tname,) in cur.fetchall():
                    con.execute(f"DROP TABLE IF EXISTS {tname}")
                for stmt in split_statements(schema_sql):
                    con.execute(stmt)
                if self.var_include_data.get() and data_sql:
                    con.executescript(data_sql)
            self.status.set(f"Applied DB template '{name}' to {self.active_db.name}.")
            self._reload_db_tree()
        except Exception as e:
            messagebox.showerror("Error applying template", str(e), parent=self)

    # ---------- Misc UI helpers ---------- #

    def _show_big_text(self, title: str, text: str) -> None:
        win = tk.Toplevel(self)
        win.title(title)
        win.geometry("800x600")
        txt = scrolledtext.ScrolledText(win, wrap="none")
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", text)
        txt.configure(state="disabled")

    # ---------- Menu actions ---------- #

    def _menu_new_test_db(self) -> None:
        db_dir = self._db_dir()
        db_dir.mkdir(parents=True, exist_ok=True)
        name = filedialog.asksaveasfilename(
            parent=self, title="Create new SQLite database",
            defaultextension=".db",
            initialdir=str(db_dir),
            filetypes=[("SQLite DB", "*.db *.sqlite *.sqlite3"), ("All files", "*.*")]
        )
        if not name:
            return
        path = Path(name)
        if self._is_protected(path):
            messagebox.showwarning("Protected name", "Choose another filename.", parent=self)
            return
        # touch the file and open/close to ensure it's a valid SQLite DB
        con = safe_connect(path)
        con.close()
        self._reload_db_tree()
        self.status.set(f"Created {path.name}.")
        self._set_active_db(path)

    def _refresh_all(self) -> None:
        self._reload_db_tree()
        self._reload_query_template_combobox()
        self._reload_db_template_combobox()
        self._reload_query_category_tree()
        self._reload_db_categories_list()
        self.status.set("Refreshed.")

    def _choose_db_dir(self) -> None:
        new_dir = filedialog.askdirectory(
            parent=self,
            title="Choose directory for test databases (catalog)",
            initialdir=str(self._db_dir())
        )
        if not new_dir:
            return
        self.settings.set("db_dir", new_dir)
        self._reload_db_tree()
        self.status.set(f"Catalog set to: {new_dir}")

    def _import_json(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="Import templates from JSON",
            initialdir=str(APP_DIR),
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            count = self.templates.import_from_json(Path(path))
            messagebox.showinfo("Import", f"Imported {count} templates.", parent=self)
            self._refresh_all()
        except Exception as e:
            messagebox.showerror("Import failed", str(e), parent=self)


############################################################


# ---------------- SQL syntax highlighting ---------------- #

def setup_sql_tags(text_widget):
    """Configure tags for SQL syntax highlighting."""
    text_widget.tag_configure("keyword", foreground="darkred", font=("TkDefaultFont", 10, "bold"))
    text_widget.tag_configure("string", foreground="darkgreen")
    text_widget.tag_configure("comment", foreground="gray", font=("TkDefaultFont", 10, "italic"))
    text_widget.tag_configure("number", foreground="blue")
    text_widget.tag_configure("ident", foreground="black")
    text_widget.tag_configure("op", foreground="purple")


def highlight_sql(text_widget, sql_text: str):

    """
    Highlight SQL text in a Tkinter Text/ScrolledText widget.
    It assumes the SQLFormatter has .tokenize() method.
    """
    # remove existing text + tags
    text_widget.delete("1.0", "end")
    for tag in text_widget.tag_names():
        text_widget.tag_remove(tag, "1.0", "end")

    # insert plain text
    text_widget.insert("1.0", sql_text)

    # apply tags
    tokens = highlight_sql_tokenize(sql_text)
    setup_sql_tags(text_widget)
        
    pos = "1.0"
    for typ, val in tokens:
        length = len(val)
        end = text_widget.index(f"{pos}+{length}c")
        if typ in text_widget.tag_names():
            text_widget.tag_add(typ, pos, end)
        pos = end

def highlight_sql_tokenize(s: str):

    _space = r"[ \t\r\n\f]+"
    _line_comment = r"--[^\n]*"
    _block_comment = r"/\*.*?\*/"
    _sq_string = r"'(?:''|[^'])*'"
    _dq_string = r'"(?:[""]|[^"])*"'
    _bt_string = r"`(?:``|[^`])*`"
    _param = r"(?:\?|:[A-Za-z_]\w*|@[A-Za-z_]\w*|\$[A-Za-z_]\w*)"
    _number = r"(?:\d+(?:\.\d+)?|\.\d+)"
    _ident = r"[A-Za-z_][A-Za-z0-9_$]*"
    _op = r"(?:<>|!=|<=|>=|\|\||[=<>+\-*/%])"
    _punct = r"[(),.;]"

    master = re.compile(
        "|".join([
            f"(?P<ws>{_space})",
            f"(?P<lc>{_line_comment})",
            f"(?P<bc>{_block_comment})",
            f"(?P<sq>{_sq_string})",
            f"(?P<dq>{_dq_string})",
            f"(?P<bt>{_bt_string})",
            f"(?P<param>{_param})",
            f"(?P<number>{_number})",
            f"(?P<op>{_op})",
            f"(?P<punct>{_punct})",
            f"(?P<ident>{_ident})",
        ]),
        re.DOTALL
    )

    # ---------- keyword & clause catalogs ----------
    KEYWORDS = {
        "ABORT","ABS","ABSOLUTE","ACCESS","ACTION","ADD","AFTER","ALL","ALTER","ALWAYS","ANALYZE","AND","AS","ASC",
        "ATTACH","AUTOINCREMENT","BEFORE","BEGIN","BETWEEN","BINARY","BLOB","BY","CASCADE","CASE","CAST","CHECK",
        "COLLATE","COLUMN","COMMIT","CONFLICT","CONSTRAINT","CREATE","CROSS","CURRENT","CURRENT_DATE",
        "CURRENT_TIME","CURRENT_TIMESTAMP","DATABASE","DEFAULT","DEFERRABLE","DEFERRED","DELETE","DESC","DETACH",
        "DISTINCT","DO","DROP","EACH","ELSE","END","ESCAPE","EXCEPT","EXCLUDE","EXCLUSIVE","EXISTS","EXPLAIN",
        "FAIL","FILTER","FIRST","FOLLOWING","FOR","FOREIGN","FROM","FULL","GENERATED","GLOB","GROUP","HAVING",
        "IF","IGNORE","IMMEDIATE","IN","INDEX","INDEXED","INITIALLY","INNER","INSERT","INSTEAD","INTERSECT",
        "INTO","IS","ISNULL","JOIN","KEY","LAST","LEFT","LIKE","LIMIT","MATCH","MATERIALIZED","NATURAL","NO",
        "NOT","NOTHING","NOTNULL","NULL","NULLS","OF","OFFSET","ON","OR","ORDER","OTHERS","OUTER","OVER","PARTITION",
        "PLAN","PRAGMA","PRECEDING","PRIMARY","QUERY","RAISE","RANGE","RECURSIVE","REFERENCES","REGEXP","REINDEX",
        "RELEASE","RENAME","REPLACE","RESTRICT","RETURNING","RIGHT","ROLLBACK","ROW","ROWS","SAVEPOINT","SELECT",
        "SET","TABLE","TEMP","TEMPORARY","THEN","TIES","TO","TRANSACTION","TRIGGER","UNBOUNDED","UNION","UNIQUE",
        "UPDATE","USING","VACUUM","VALUES","VIEW","VIRTUAL","WHEN","WHERE","WINDOW","WITH","WITHOUT","QUALIFY"
    }

    pos, out = 0, []
    for m in master.finditer(s):
        if m.start() != pos:
            raw = s[pos:m.start()]
            out.append(("ident", raw))
        pos = m.end()
        kind, val = m.lastgroup, m.group()
        if kind == "ws":
            out.append(("ws", val))
        elif kind in ("lc", "bc"):
            out.append(("comment", val))
        elif kind in ("sq","dq","bt"):
            out.append(("string", val))
        elif kind == "number":
            out.append(("number", val))
        elif kind == "param":
            out.append(("param", val))
        elif kind == "op":
            out.append(("op", val))
        elif kind == "punct":
            out.append(("punct", val))
        elif kind == "ident":
            u = val.upper()
            if u in KEYWORDS:
                out.append(("keyword", u))
            else:
                out.append(("ident", val))
    if pos < len(s):
        out.append(("ident", s[pos:]))
    return out
# ------------------------ main ------------------------ #

def main():
    app = App()
    # High-level grid weights are configured; ttk theme is default
    app.mainloop()

if __name__ == "__main__":
    main()
