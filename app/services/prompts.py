"""Prompt templates for answer generation. Edit wording here, not in the client code."""
from app.models.schemas import SearchHit

# Returned without calling the LLM when retrieval finds no candidate chunks.
NO_CONTEXT_ANSWER = 'Tôi không tìm thấy thông tin này trong tài liệu đã tải lên.'
# The exact sentence the LLM must use when the context is insufficient.
INSUFFICIENT_ANSWER = 'Tài liệu không có đủ thông tin để trả lời câu hỏi này.'

SYSTEM_PROMPT = f'''Bạn là trợ lý hỏi đáp tài liệu. Chỉ trả lời dựa trên phần NGỮ CẢNH được cung cấp.
Quy tắc:
- Trả lời bằng tiếng Việt, rõ ràng, ngắn gọn, đúng trọng tâm câu hỏi.
- Chỉ dùng thông tin có trong NGỮ CẢNH. Không bịa, không dùng kiến thức bên ngoài.
- Nếu NGỮ CẢNH không đủ để trả lời, nói đúng câu: "{INSUFFICIENT_ANSWER}"
- Sau mỗi ý quan trọng, ghi nguồn dạng [Nguồn: <tên file>, trang <số trang>] dựa trên metadata của đoạn ngữ cảnh.
- Không lặp lại nguyên văn các đoạn ngữ cảnh dài.'''


def location(hit: SearchHit) -> str:
    """'<file>, trang N' for PDFs; sheet for Excel; file only for DOCX/images (no page metadata)."""
    meta = hit.metadata
    parts = [str(meta.get('filename', 'không rõ tên file'))]
    if meta.get('page') is not None:
        parts.append(f"trang {meta['page']}")
    elif meta.get('sheet') is not None:
        parts.append(f"sheet {meta['sheet']}")
    return ', '.join(parts)


def build_user_message(question: str, contexts: list[SearchHit]) -> str:
    blocks = '\n'.join(f'[{number}] ({location(hit)}) {hit.content}' for number, hit in enumerate(contexts, 1))
    return f'NGỮ CẢNH:\n{blocks}\nCÂU HỎI: {question}'
