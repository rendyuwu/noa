"""Large READ results, parked off the transcript (T56 — V64, V85).

One module today, `tables`. A READ whose answer is a listing rather than a fact — WHM's
accounts (T20), PMG's mynetworks (T30) — writes the body here and answers the model with a
short summary plus a URL; the operator opens that URL and reads the whole table on NOA's own
origin. Zero tokens for the table body, and nothing dropped from it that is not counted
(V85).

Beside `core.approvals` rather than inside it: nothing here authorises anything. The rows are
the output of a READ that already ran, and the one guard on reading them back is the same
requester-match the approval surfaces use (V27), spelled here against its own table.
"""

from __future__ import annotations

from core.results.errors import ResultTableNotFoundError
from core.results.tables import (
    ParkedTable,
    ResultTableService,
    ResultTableView,
    SQLToolResultTableReader,
    SQLToolResultTableWriter,
    TableColumn,
    ToolResultTableReader,
    ToolResultTableWriter,
    cap_rows,
    mint_table_token,
    park_result_table,
)

__all__ = [
    "ParkedTable",
    "ResultTableNotFoundError",
    "ResultTableService",
    "ResultTableView",
    "SQLToolResultTableReader",
    "SQLToolResultTableWriter",
    "TableColumn",
    "ToolResultTableReader",
    "ToolResultTableWriter",
    "cap_rows",
    "mint_table_token",
    "park_result_table",
]
