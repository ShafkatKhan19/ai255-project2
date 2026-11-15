from fastapi.testclient import TestClient

from conftest import load_service


def make_client():
    return TestClient(load_service("links").app)


def test_create_then_get():
    client = make_client()
    created = client.post("/links", json={"url": "https://example.com/page"})
    assert created.status_code == 201
    code = created.json()["code"]
    assert len(code) == 7
    fetched = client.get(f"/links/{code}")
    assert fetched.status_code == 200
    assert fetched.json()["url"] == "https://example.com/page"


def test_unknown_code_is_404():
    assert make_client().get("/links/nothere").status_code == 404


def test_rejects_non_http_addresses():
    client = make_client()
    for bad in ["javascript:alert(1)", "ftp://example.com/file", "not a url", ""]:
        assert client.post("/links", json={"url": bad}).status_code == 422


def test_rejects_very_long_address():
    long_url = "https://example.com/" + "a" * 3000
    assert make_client().post("/links", json={"url": long_url}).status_code == 422


def test_list_is_newest_first():
    client = make_client()
    first = client.post("/links", json={"url": "https://example.com/1"}).json()["code"]
    second = client.post("/links", json={"url": "https://example.com/2"}).json()["code"]
    codes = [link["code"] for link in client.get("/links").json()["links"]]
    assert codes == [second, first]


def test_code_collision_is_retried():
    module = load_service("links")
    client = TestClient(module.app)
    codes = iter(["samecode", "samecode", "othercode"])
    module.new_code = lambda: next(codes)
    assert client.post("/links", json={"url": "https://example.com/1"}).json()["code"] == "samecode"
    assert client.post("/links", json={"url": "https://example.com/2"}).json()["code"] == "othercode"


def test_health_and_ready():
    client = make_client()
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


def test_datastore_failure_gives_503_and_not_ready():
    module = load_service("links")

    class BrokenStore:
        def __getattr__(self, name):
            def fail(*args, **kwargs):
                raise RuntimeError("datastore is down")
            return fail

    module.store = BrokenStore()
    client = TestClient(module.app)
    assert client.get("/readyz").status_code == 503
    assert client.get("/links").status_code == 503
    assert client.get("/links/abc").status_code == 503
    assert client.post("/links", json={"url": "https://example.com"}).status_code == 503
    # Liveness must stay green: restarting the pod would not fix the datastore.
    assert client.get("/healthz").status_code == 200
