from pathlib import Path
import pandas as pd
from app.models.schemas import DocumentUnit


def load_excel(path: Path) -> list[DocumentUnit]:
    units = []
    engine = 'xlrd' if path.suffix.lower() == '.xls' else 'openpyxl'
    with pd.ExcelFile(path, engine=engine) as workbook:
        for sheet in workbook.sheet_names:
            frame = workbook.parse(sheet, header=None, dtype=str, keep_default_na=False)
            frame = frame.loc[~frame.eq('').all(axis=1)]
            if frame.empty:
                continue
            headers = [str(value) or f'Column {i + 1}' for i, value in enumerate(frame.iloc[0])]
            rows = [' | '.join(headers)]
            for row in frame.iloc[1:].itertuples(index=False, name=None):
                rows.append(' | '.join(f'{header}: {value}' for header, value in zip(headers, row)))
            units.append(DocumentUnit(content='\n'.join(rows), metadata={
                'filename': path.name, 'file_type': 'excel', 'sheet': sheet, 'source_type': 'text',
            }))
    return units
