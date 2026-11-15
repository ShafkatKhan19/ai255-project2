"""links service: creates short links and looks them up.

Endpoints
  POST /links          {"url": "https://..."} -> {"code", "url"}
  GET  /links          newest 25 links
  GET  /links/{code}   one link, or 404
  GET  /healthz        liveness: the process is running
  GET  /readyz         readiness: the datastore can be reached
"""
import logging
import secrets
import socket
import string
import time

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, HttpUrl

from store import make_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s links %(levelname)s %(message)s")
log = logging.getLogger("links")

ALPHABET = string.ascii_lowercase + string.digits
CODE_LENGTH = 7
MAX_URL_LENGTH = 2048
LIST_SIZE = 25

app = FastAPI(title="links")
store = make_store()


@app.middleware("http")
async def log_request(request: Request, call_next):
    """One log line per request, with the ID that web gave the request, so
    a single click can be followed through all three services' logs."""
    started = time.perf_counter()
    response = await call_next(request)
    if request.url.path not in ("/healthz", "/readyz"):
        log.info(
            "%s %s -> %s in %.0f ms request_id=%s",
            request.method, request.url.path, response.status_code,
            (time.perf_counter() - started) * 1000,
            request.headers.get("x-request-id", "-"),
        )
    return response


class NewLink(BaseModel):
    # HttpUrl only accepts http:// and https:// addresses, which blocks
    # things like javascript: links.
    url: HttpUrl


def new_code():
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def unavailable(error):
    """Turn a datastore error into a 503 so callers know to try again later."""
    log.error("datastore error: %s", error)
    return HTTPException(status_code=503, detail="datastore unavailable")


# "async def" keeps this off the worker threads that datastore calls use.
# Even if every worker is stuck waiting for Firestore, liveness still
# answers, so Kubernetes does not restart a pod that is only waiting.
@app.get("/healthz")
async def healthz():
    return {"status": "ok", "service": "links", "pod": socket.gethostname()}


@app.get("/readyz")
def readyz():
    try:
        store.ping()
    except Exception as error:
        raise unavailable(error)
    return {"status": "ready"}


@app.post("/links", status_code=201)
def create_link(body: NewLink):
    url = str(body.url)
    if len(url) > MAX_URL_LENGTH:
        raise HTTPException(status_code=422, detail="url too long")
    # A random 7-character code almost never collides, but try a few times
    # in case it does.
    for _ in range(5):
        code = new_code()
        try:
            created = store.create(code, url)
        except Exception as error:
            raise unavailable(error)
        if created:
            log.info("created %s", code)
            return {"code": code, "url": url}
    raise HTTPException(status_code=500, detail="could not allocate a code")


def store_get(code):
    try:
        link = store.get(code)
    except Exception as error:
        raise unavailable(error)
    if link is None:
        raise HTTPException(status_code=404, detail="link not found")
    return link


@app.get("/links/{code}")
def get_link(code: str):
    return store_get(code)


@app.get("/links")
def list_links():
    try:
        return {"links": store.recent(LIST_SIZE)}
    except Exception as error:
        raise unavailable(error)
