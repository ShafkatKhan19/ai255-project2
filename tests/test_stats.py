from fastapi.testclient import TestClient

from conftest import load_service


def make_client():
    return TestClient(load_service("stats").app)


def test_counts_clicks():
    client = make_client()
    assert client.get("/clicks?codes=abc1234").json()["clicks"] == {"abc1234": 0}
    for _ in range(3):
        assert client.post("/clicks/abc1234").status_code == 202
    assert client.get("/clicks?codes=abc1234,other").json()["clicks"] == {"abc1234": 3, "other": 0}
    assert client.get("/clicks").json()["clicks"] == {}


def test_rejects_bad_codes():
    client = make_client()
    assert client.post("/clicks/NOT-VALID!").status_code == 422
    assert client.get("/clicks?codes=ok,NOT-VALID!").status_code == 422
    too_many = ",".join(f"c{i}" for i in range(51))
    assert client.get(f"/clicks?codes={too_many}").status_code == 422


def test_health_and_ready():
    client = make_client()
    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


def test_datastore_failure_gives_503_and_not_ready():
    module = load_service("stats")

    class BrokenStore:
        def __getattr__(self, name):
            def fail(*args, **kwargs):
                raise RuntimeError("datastore is down")
            return fail

    module.store = BrokenStore()
    client = TestClient(module.app)
    assert client.get("/readyz").status_code == 503
    assert client.post("/clicks/abc").status_code == 503
    assert client.get("/clicks?codes=abc").status_code == 503
    assert client.get("/healthz").status_code == 200
