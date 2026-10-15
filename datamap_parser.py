"""
Parses hierarchical "Datamap" spreadsheets exported from survey platforms
like Decipher/Forsta into the flat "CODE = plain-English label" text format
the rest of the app (AI prompts) expects.

These datamaps have a distinctive structure that a naive two-column
CSV/Excel parser (grab column A and column B) completely mangles:

  [S2]: Are you...?                     <- variable header: "[code]: question text"
  Values: 1-4                           <- signals a coded value range follows
        1  Female                       <- value label rows: code, then label
        2  Male
        3  Other / Non-binary
        4  Prefer not to answer

  V1: How interested are you in the following?   <- battery header (no brackets - just a label)
  Values: 1-5
        1  Not interested at all
        ...
        5  Very interested
  [V1r1]   Attending (in person) esports events  <- one row-item per brand/attribute,
  [V1r2]   Attending college sporting events         each with its OWN label, sharing
  ...                                                 the battery's 1-5 scale above

  V1_T2B: Top-2 box for each row (4-5) at V1
  Values: 0-1
        0  Unchecked
        1  Checked
  [V1_T2Br1]   Attending (in person) esports events  <- T2B flag per item, same label
  [V1_T2Br2]   Attending college sporting events         as its parent V1r_ item

This parser walks the raw grid row by row, tracks the "current variable"
context, and produces one output line per leaf code:

  S2 = Are you...? (1=Female, 2=Male, 3=Other / Non-binary, 4=Prefer not to answer)
  V1r1 = How interested are you in the following? - Attending (in person) esports events (1=Not interested at all...5=Very interested)
  V1_T2Br1 = Top-2 box for each row (4-5) at V1 - Attending (in person) esports events (0=Unchecked, 1=Checked)
"""
import re
import pandas as pd


_HEADER_WITH_DESC = re.compile(r"^\[(\w+)\]:\s*(.*)$")   # [S2]: Are you...?
_HEADER_NO_BRACKET = re.compile(r"^(\w+):\s*(.*)$")       # V1: How interested...
_ITEM_ONLY = re.compile(r"^\[(\w+)\]$")                   # [V1r1]  (label in next cell)
_VALUES_LINE = re.compile(r"^Values:\s*", re.IGNORECASE)
_PURE_INT = re.compile(r"^-?\d+$")


def _row_cells(row) -> list[str]:
    out = []
    for x in row:
        if pd.isna(x):
            out.append("")
        else:
            out.append(str(x).strip())
    return out


def _format_value_labels(value_labels: dict) -> str:
    if not value_labels:
        return ""
    items = sorted(value_labels.items(), key=lambda kv: (len(kv[0]), kv[0]))
    return ", ".join(f"{k}={v}" for k, v in items)


def parse_decipher_datamap(filepath: str) -> str:
    """
    Reads a hierarchical datamap spreadsheet (xlsx/xls/csv) and returns a
    flat text data map, one "CODE = description (value labels)" line per
    leaf variable. Returns "" if the file doesn't look like this format at
    all (caller should fall back to simpler parsing in that case).
    """
    if filepath.lower().endswith(".csv"):
        raw = pd.read_csv(filepath, header=None, dtype=str)
    else:
        xl = pd.ExcelFile(filepath)
        sheet_name = xl.sheet_names[0]
        for name in xl.sheet_names:
            if name.strip().lower() == "datamap":
                sheet_name = name
                break
        raw = pd.read_excel(filepath, header=None, dtype=str, sheet_name=sheet_name)

    rows = [_row_cells(r) for r in raw.itertuples(index=False, name=None)]

    lines: list[str] = []
    current_code = None
    current_desc = ""
    current_value_labels: dict[str, str] = {}
    bracket_header_count = 0

    def flush_current():
        # Emit a line for the current top-level code IF it's a leaf
        # (has no row-items under it - those get emitted individually
        # via _ITEM_ONLY instead, using the parent's description/scale).
        if current_code and not current_code.endswith("__has_items__"):
            desc = current_desc.strip()
            vl = _format_value_labels(current_value_labels)
            if desc or vl:
                line = f"{current_code} = {desc}"
                if vl:
                    line += f" ({vl})"
                lines.append(line)

    for cells in rows:
        if not cells or not any(cells):
            continue
        a = cells[0]

        if a:
            m = _HEADER_WITH_DESC.match(a)
            if m:
                flush_current()
                current_code = m.group(1)
                current_desc = m.group(2)
                current_value_labels = {}
                bracket_header_count += 1
                continue

            m_item = _ITEM_ONLY.match(a)
            if m_item:
                # Row-item under a battery, e.g. [V1r1]  Attending esports events
                item_code = m_item.group(1)
                item_label = ""
                for c in cells[1:]:
                    if c:
                        item_label = c
                        break
                base_desc = current_desc.strip()
                full_desc = f"{base_desc} - {item_label}" if base_desc else item_label
                vl = _format_value_labels(current_value_labels)
                line = f"{item_code} = {full_desc}"
                if vl:
                    line += f" ({vl})"
                lines.append(line)
                continue

            if _VALUES_LINE.match(a):
                continue

            # Unbracketed battery header, e.g. "V1: How interested are you..."
            m_plain = _HEADER_NO_BRACKET.match(a)
            if m_plain and len(m_plain.group(1)) <= 20:
                flush_current()
                current_code = m_plain.group(1)
                current_desc = m_plain.group(2)
                current_value_labels = {}
                continue

        # value-label row: number can be in ANY column (often B/C, since
        # these rows are indented under their header) - find the first
        # purely-numeric cell, take the next non-empty cell as its label
        numeric_idx = None
        for i, c in enumerate(cells):
            if _PURE_INT.match(c):
                numeric_idx = i
                break
        if numeric_idx is not None:
            label_text = ""
            for c in cells[numeric_idx + 1:]:
                if c:
                    label_text = c
                    break
            if label_text and current_code:
                current_value_labels[cells[numeric_idx]] = label_text
            continue

    flush_current()

    # Heuristic: if we found very few bracketed headers, this probably
    # isn't a Decipher-style datamap - let the caller fall back.
    if bracket_header_count < 2 or len(lines) < 3:
        return ""

    return "\n".join(lines)
