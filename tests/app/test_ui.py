from fastapi.testclient import TestClient

from app.main import create_app


def client() -> TestClient:
    app = create_app()
    app.state.llm_client = None
    return TestClient(app)


def test_index_serves_with_accessibility_landmarks():
    html = client().get("/").text
    assert 'aria-live="polite"' in html
    assert 'role="dialog"' in html and "aria-modal" in html
    assert "aria-label" in html


def test_static_assets_served():
    c = client()
    assert c.get("/static/style.css").status_code == 200
    js = c.get("/static/app.js")
    assert js.status_code == 200
    # The stop flow must be reachable by keyboard alone.
    assert "Escape" in js.text and "Enter" in js.text
    # Results table semantics required by the brief.
    assert 'th.scope = "col"' in js.text
