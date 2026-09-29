from pathlib import Path
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from app.models.schemas import DocumentUnit


def load_docx(path: Path) -> list[DocumentUnit]:
    document = Document(path)
    blocks = []
    for block in document.iter_inner_content():
        if isinstance(block, Paragraph):
            blocks.append(block.text)
        elif isinstance(block, Table):
            blocks.extend(' | '.join(cell.text for cell in row.cells) for row in block.rows)
    return [DocumentUnit(content='\n'.join(blocks), metadata={
        'filename': path.name, 'file_type': 'docx', 'source_type': 'text',
    })]
