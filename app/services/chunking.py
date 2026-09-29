from app.models.schemas import Chunk, DocumentUnit
from app.services.layout import chunk_boxes


def chunk_units(units: list[DocumentUnit], tokenizer, chunk_size: int,
                chunk_overlap: int, model_max_length: int) -> list[Chunk]:
    """Token windows, with original text slices (including punctuation/newlines).

    Configured size is an upper bound. Cap it to the model's input budget to
    prevent SentenceTransformer silently truncating the tail of each chunk.
    Preserve the configured overlap ratio when capping. Each chunk records its
    character range [char_start, char_end) inside the unit text.
    """
    if not 0 <= chunk_overlap < chunk_size:
        raise ValueError('Invalid chunk overlap')
    size = min(chunk_size, model_max_length - tokenizer.num_special_tokens_to_add(pair=False))
    if size < 1:
        raise ValueError('Embedding model has no usable token budget')
    overlap = min(size - 1, round(size * chunk_overlap / chunk_size))
    chunks = []
    for unit in units:
        encoded = tokenizer(unit.content, add_special_tokens=False, return_offsets_mapping=True,
                            truncation=False, verbose=False)
        offsets = encoded['offset_mapping']
        for start in range(0, len(offsets), size - overlap):
            end = min(start + size, len(offsets))
            raw_start, raw_end = offsets[start][0], offsets[end - 1][1]
            raw = unit.content[raw_start:raw_end]
            content = raw.strip()
            if content:
                char_start = raw_start + (len(raw) - len(raw.lstrip()))
                char_end = char_start + len(content)
                page = unit.metadata.get('page')
                bboxes = chunk_boxes(unit.layout, char_start, char_end, page) if unit.layout and page else None
                index = len(chunks)
                chunks.append(Chunk(content=content, chunk_index=index, char_start=char_start, char_end=char_end,
                                    bboxes=bboxes or None, metadata={**unit.metadata, 'chunk_index': index}))
            if end == len(offsets):
                break
    return chunks
