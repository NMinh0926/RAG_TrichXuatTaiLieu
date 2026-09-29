from fastapi.testclient import TestClient
from app.main import app


def test_local_frontend_preflight_and_disallowed_origin():
    with TestClient(app) as client:
        for origin in ('http://localhost:5173', 'http://127.0.0.1:5173'):
            response = client.options('/api/questions/ask', headers={
                'Origin': origin, 'Access-Control-Request-Method': 'POST',
                'Access-Control-Request-Headers': 'content-type'})
            assert response.status_code == 200
            assert response.headers['access-control-allow-origin'] == origin
            assert 'access-control-allow-credentials' not in response.headers
        response = client.options('/api/questions/ask', headers={
            'Origin': 'https://untrusted.example', 'Access-Control-Request-Method': 'POST'})
        assert response.status_code == 400
        assert 'access-control-allow-origin' not in response.headers


def test_controlled_errors_are_readable_from_browser():
    with TestClient(app) as client:
        response = client.post('/api/questions/ask', json={'question': ''},
                               headers={'Origin': 'http://localhost:5173'})
        assert response.status_code == 422
        assert response.headers['access-control-allow-origin'] == 'http://localhost:5173'
