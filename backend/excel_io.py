"""CSV/.xlsx parsing for the historical-data imports.

Trimmed from Boord (../Boord/backend/excel_io.py) to just the one function
routers/historical.py needs. Keeping it generic (a header row + data rows in,
list-of-dicts out) means the import endpoints supply no per-file boilerplate.
"""
import csv
import io

from fastapi import UploadFile
from openpyxl import load_workbook


async def parse_uploaded_table(file: UploadFile) -> list[dict]:
    """Returns a list of dict rows keyed by the header row. Accepts .csv or .xlsx."""
    content = await file.read()
    name = (file.filename or "").lower()
    if name.endswith(".xlsx"):
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        headers = [str(h).strip() if h is not None else "" for h in next(rows_iter)]
        return [dict(zip(headers, row)) for row in rows_iter if any(v is not None for v in row)]
    else:
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        return [row for row in reader]
