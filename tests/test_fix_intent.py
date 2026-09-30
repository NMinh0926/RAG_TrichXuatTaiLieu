"""Intent rules: accent-insensitive matching, compound words that are not summary requests, NFC input."""
import unicodedata

import httpx
import pytest

from app.core.config import Settings
from app.llm.client import LLMClient
from app.services.intent import classify
from tests.test_generation import BASE_URL, MODEL, FakeLLM


@pytest.mark.parametrize('question', [
    # Unaccented (finding A; the live question "tom tat noi dung cua file debug" was answered by RAG).
    'tom tat noi dung cua file debug', 'Tom tat tai lieu nay', 'tong quan ve quy che', 'khai quat van ban',
    'tom luoc bao cao', 'tong hop noi dung cac file', 'Liet ke y chinh', 'noi dung chinh cua quy che la gi?',
    'cac diem chinh cua tai lieu', 'y chinh', 'NOI DUNG CHINH',
    # Mixed and accented.
    'tom tắt tài liệu', 'Tóm tat file debug', 'Liệt kê ý chính', 'Nội dung chính của quy chế là gì?',
    'Các điểm chính của báo cáo', 'Nêu các ý chính yếu', 'Tổng  quan   về dự án', 'key points please',
    # Everyday words whose accent-free form equals a compound syllable ("trong" vs "trọng", "co" vs "cố", "ta" vs "tả").
    'Liệt kê những gì trong ý chính', 'lỗi chính tả trong ý chính', 'tai lieu co y chinh gi?',
    'Tài liệu có ý chính gì?', 'cac y chinh ta can nam',
])
def test_summary_requests_with_or_without_accents(question: str) -> None:
    result = classify(question)
    assert (result.intent, result.source) == ('summary', 'rule')


@pytest.mark.parametrize('question', [
    # Finding #1: "chính" starting a compound, or "ý"/"điểm" ending one.
    'Nội dung chính sách hỗ trợ nhân viên là gì?', 'Thời điểm chính thức áp dụng quy chế là khi nào?',
    'Địa điểm chính của công ty ở đâu?', 'Cần lưu ý chính sách bảo hiểm nào?', 'Nội dung chính xác của điều 5?',
    'Ý chính trị của văn bản?', 'Ưu điểm chính của phương án A là gì?', 'Tôi đồng ý chính thức khi nào?',
    'noi dung chinh sach ho tro nhan vien', 'thoi diem chinh thuc ap dung', 'dia diem chinh cua cong ty',
    'luu y chinh sach bao hiem', 'noi dung chinh xac cua dieu 5', 'noi dung chinh sua hop dong',
    'Nội dung chính tả của văn bản', 'Nêu ý chính kiến của tác giả', 'Điểm chính diện của tòa nhà',
    'Ý chính nghĩa của cuộc chiến', 'Nội dung chính sự trong nước', 'noi dung chinh kien cua tac gia',
    'Cố ý chính là lỗi gì?',
    # A differently accented syllable is another word, not an unaccented "chính".
    'Nội dung chỉnh sửa hợp đồng là gì?', 'Điểm chỉnh lý hồ sơ',
    'Phụ cấp ăn trưa bao nhiêu?', 'Tôm tắt?',
])
def test_specific_questions_stay_qa(question: str) -> None:
    assert classify(question).intent == 'qa'


def test_main_points_found_after_an_excluded_compound() -> None:
    assert classify('Thời điểm chính thức và các ý chính của quy chế').intent == 'summary'


@pytest.mark.parametrize('question', ['Tóm tắt tài liệu này', 'Liệt kê ý chính', 'Nội dung chính của quy chế'])
def test_decomposed_input_is_normalized(question: str) -> None:
    decomposed = unicodedata.normalize('NFD', question)
    assert decomposed != question and classify(decomposed).intent == 'summary'


@pytest.mark.parametrize('question', ['tai lieu nay noi ve gi?', 'File đề cập đến những điều gì?',
                                      'gioi thieu file nay', unicodedata.normalize('NFD', 'Tài liệu này nói về gì?')])
def test_ambiguous_phrasings_ask_llm_with_normalized_question(question: str) -> None:
    settings = Settings(_env_file=None, llm_base_url=BASE_URL, llm_model=MODEL)
    fake = FakeLLM('summary')
    result = classify(question, LLMClient(settings, transport=httpx.MockTransport(fake)))
    assert (result.intent, result.source) == ('summary', 'llm')
    sent = fake.chats[0]['messages'][-1]['content']
    assert unicodedata.normalize('NFC', question) in sent and unicodedata.is_normalized('NFC', sent)


def test_unambiguous_question_skips_llm() -> None:
    settings = Settings(_env_file=None, llm_base_url=BASE_URL, llm_model=MODEL)
    fake = FakeLLM('summary')
    result = classify('noi dung chinh sach ho tro la gi?', LLMClient(settings, transport=httpx.MockTransport(fake)))
    assert (result.intent, result.source) == ('qa', 'rule') and fake.chats == []
