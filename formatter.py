"""
formatter.py
------------
General SQL formatter and lightweight syntax checker.
Pure Python 3.10+, no third-party libraries.

Goals:
- Uppercase keywords (identifiers/literals untouched).
- Canonical line breaking for major clauses (SELECT, FROM, WHERE, INSERT INTO, VALUES,
  CREATE TABLE, DROP TABLE, ALTER TABLE, GRANT, REVOKE, etc.).
- Multi-word keywords kept together (e.g., PRIMARY KEY, IF EXISTS, ORDER BY).
- Parentheses: contents are indented one level; closing paren on its own line.
- Commas:
  * Inside column/value/definition lists -> newline after each item.
  * In GRANT/REVOKE privilege lists -> stay on the same line.
- Semicolons stick to the last token with no space before, reset indentation for next statement.
- Basic checks: starts with a valid keyword, balanced parentheses, ends with semicolon (for the script).
"""

import re
from typing import List, Tuple


class SQLFormatter:
    # Compound + single keywords we'll recognize/uppercase.
    KEYWORDS = {
        # DML
        "SELECT", "FROM", "WHERE", "GROUP BY", "ORDER BY", "HAVING",
        "INSERT INTO", "VALUES", "UPDATE", "SET", "DELETE",
        # DDL
        "CREATE TABLE", "DROP TABLE", "ALTER TABLE", "TRUNCATE TABLE",
        "PRIMARY KEY", "FOREIGN KEY", "REFERENCES", "CONSTRAINT",
        "IF EXISTS", "NOT NULL", "UNIQUE", "CHECK",
        # JOINS
        "JOIN", "LEFT JOIN", "RIGHT JOIN", "INNER JOIN", "OUTER JOIN", "ON",
        # Misc
        "AS", "DISTINCT", "LIMIT", "OFFSET", "UNION", "ALL",
        # DCL / TCL
        "GRANT", "REVOKE", "TO", "WITH", "OPTION",
        "COMMIT", "ROLLBACK", "SAVEPOINT", "TRANSACTION", "BEGIN",
    }

    # Clause starters that should generally begin a new line.
    CLAUSE_KEYWORDS = {
        "SELECT", "FROM", "WHERE", "GROUP BY", "ORDER BY", "HAVING",
        "VALUES", "SET", "INSERT INTO", "UPDATE", "DELETE",
        "CREATE TABLE", "DROP TABLE", "ALTER TABLE", "TRUNCATE TABLE",
        "GRANT", "REVOKE"
    }

    # Valid starts for a statement (single or compound).
    VALID_STARTS = {
        "SELECT", "INSERT INTO", "UPDATE", "DELETE",
        "CREATE TABLE", "DROP TABLE", "ALTER TABLE", "TRUNCATE TABLE",
        "GRANT", "REVOKE", "COMMIT", "ROLLBACK", "BEGIN"
    }

    def __init__(self, sql: str) -> None:
        self.original_sql = sql.strip()
        self.formatted_sql = ""
        self.errors: List[str] = []

    # ---------------------------
    # Public API
    # ---------------------------
    def format_and_check(self) -> Tuple[str, List[str]]:
        tokens = self._tokenize(self.original_sql)
        merged = self._merge_compounds(tokens)
        self.formatted_sql = self._format_tokens(merged)
        self._check_syntax(merged)
        return self.formatted_sql, self.errors

    # ---------------------------
    # Tokenizer / compound merge
    # ---------------------------
    def _tokenize(self, sql: str) -> List[str]:
        # Pad punctuation so split() gets them as separate tokens.
        sql = re.sub(r"([(),;])", r" \1 ", sql)
        # Normalize whitespace
        return sql.split()

    def _merge_compounds(self, tokens: List[str]) -> List[str]:
        """Merge 2- or 3-word keywords into a single token; uppercase keywords only."""
        merged: List[str] = []
        i = 0
        while i < len(tokens):
            t0 = tokens[i].upper()
            # Try 3-word compound
            if i + 2 < len(tokens):
                t3 = f"{t0} {tokens[i+1].upper()} {tokens[i+2].upper()}"
                if t3 in self.KEYWORDS:
                    merged.append(t3)
                    i += 3
                    continue
            # Try 2-word compound
            if i + 1 < len(tokens):
                t2 = f"{t0} {tokens[i+1].upper()}"
                if t2 in self.KEYWORDS:
                    merged.append(t2)
                    i += 2
                    continue
            # Single token
            merged.append(t0 if t0 in self.KEYWORDS else tokens[i])
            i += 1
        return merged

    # ---------------------------
    # Formatter
    # ---------------------------
    def _format_tokens(self, tokens: List[str]) -> str:
        lines: List[str] = []
        current: List[str] = []
        indent_level = 0
        indent = "    "

        # Context flags
        inside_list = False                 # for SELECT cols, INSERT cols/VALUES, CREATE TABLE defs
        in_grant_priv_list = False          # after GRANT/REVOKE until ON/TO or ';'

        def flush_line(force: bool = False) -> None:
            nonlocal current
            if current or force:
                line = " ".join(current).strip()
                # Allow empty line only if explicitly forced elsewhere (we control via appending "")
                if line:
                    lines.append(indent * indent_level + line)
                current = []

        i = 0
        while i < len(tokens):
            tok = tokens[i]

            # Clause handling with GRANT/REVOKE special-case (privileges shouldn't break lines)
            if tok in self.CLAUSE_KEYWORDS and not (
                in_grant_priv_list and tok in {"SELECT", "INSERT", "UPDATE", "DELETE", "ALL"}
            ):
                flush_line()
                current.append(tok)
                # List-like clauses: items separated by commas should go one per line
                if tok in {"SELECT", "INSERT INTO", "VALUES", "CREATE TABLE"}:
                    inside_list = True
                # Enter privilege list context for GRANT/REVOKE
                in_grant_priv_list = tok in {"GRANT", "REVOKE"}

            elif tok == ",":
                # Attach comma to preceding token
                if current:
                    current[-1] = current[-1] + ","
                # Only break line for list contexts (SELECT cols, CREATE defs, VALUES tuples, INSERT cols)
                if inside_list:
                    flush_line()
                # In GRANT/REVOKE privilege list, we intentionally do NOT flush (keep inline)

            elif tok == "(":
                # Put '(' at end of current line, THEN increase indent so contents are indented.
                current.append("(")
                flush_line()
                indent_level += 1

            elif tok == ")":
                # Close current item, then dedent and print ')' on its own line at the dedented level.
                flush_line()
                indent_level = max(0, indent_level - 1)
                current.append(")")
                flush_line()
                # Exiting any list that was controlled by parentheses
                inside_list = False

            elif tok == ";":
                # Attach to last token (no preceding space)
                if current:
                    current[-1] = current[-1] + ";"
                    flush_line()
                else:
                    if lines:
                        lines[-1] = lines[-1].rstrip() + ";"
                # Statement boundary: blank line and reset contexts/indent
                lines.append("")             # preserve a blank line between statements
                indent_level = 0
                inside_list = False
                in_grant_priv_list = False

            else:
                # Tokens that affect GRANT context
                if in_grant_priv_list and tok in {"ON", "TO"}:
                    # Still inline; leaving privilege list once ON/TO encountered
                    current.append(tok)
                    in_grant_priv_list = False
                else:
                    current.append(tok)

            i += 1

        flush_line()
        # Join without stripping empty lines we explicitly inserted
        result = "\n".join(lines).rstrip()
        return result

    # ---------------------------
    # Syntax checker (lightweight)
    # ---------------------------
    def _check_syntax(self, tokens: List[str]) -> None:
        # Check first statement start only (lightweight multi-statement scripts work fine in practice)
        if tokens:
            if tokens[0].upper() not in self.VALID_STARTS:
                self.errors.append(f"SQL must start with a keyword, got '{tokens[0]}'.")

        # Parenthesis balance
        balance = 0
        for idx, t in enumerate(tokens):
            if t == "(":
                balance += 1
            elif t == ")":
                balance -= 1
                if balance < 0:
                    self.errors.append(f"Unmatched ')' at token {idx}.")
                    break
        if balance != 0:
            self.errors.append("Parentheses are not balanced.")

        # Script should end with semicolon (last statement)
        if tokens and tokens[-1] != ";":
            self.errors.append("SQL should end with ';'.")


# ---------------------------
# Example usage
# ---------------------------
if __name__ == "__main__":
    sql = """DROP TABLE IF EXISTS posts;
CREATE TABLE posts(
  id INTEGER PRIMARY KEY,
  title TEXT,
  body TEXT
);
INSERT INTO posts(id,title,body) VALUES(1,'Hello','World');
GRANT SELECT, INSERT ON posts TO bob;
"""
    fmt = SQLFormatter(sql)
    formatted, errors = fmt.format_and_check()
    print("Formatted SQL:")
    print(formatted)
    if errors:
        print("\nErrors:")
        for e in errors:
            print(" -", e)
    else:
        print("\nNo errors found.")
