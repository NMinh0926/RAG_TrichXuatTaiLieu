from scripts.create_evaluation_data import create_dataset
from scripts.evaluate import contains_keywords, source_matches


def test_evaluation_dataset_distribution(tmp_path):
    cases = create_dataset(tmp_path)
    assert len(cases) == 25
    assert sum(case['expected_source'] is None for case in cases) == 4
    assert len({case['id'] for case in cases}) == 25
    for case in cases:
        if case['expected_source']:
            assert (tmp_path / 'documents' / case['expected_source']['filename']).is_file()
            assert case['expected_keywords']


def test_evaluation_matching():
    assert contains_keywords('Nghỉ 12 ngày', ['12'])
    assert not contains_keywords('120 ngày', ['12'])
    assert contains_keywords('3.000.000 đồng', ['3000000'])
    assert not source_matches({'filename': 'x.xlsx', 'sheet': 'Wrong'}, {'filename': 'x.xlsx', 'sheet': 'Sales'})
