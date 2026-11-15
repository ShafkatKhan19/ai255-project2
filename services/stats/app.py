"""stats service: counts clicks on short links.

Endpoints
  POST /clicks/{code}        add one click
  GET  /clicks?codes=a,b,c   {"clicks": {code: count, ...}} for those codes
  GET  /healthz              liveness: the process is running
  GET  /readyz               readiness: the datastore can be reached
"""
import logging
import re
import socket
import time

from fastapi import FastAPI, HTTPException, Request

from store import make_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s stats %(levelname)s %(message)s")
log = logging.getLogger("stats")

# Codes are short and only contain lowercase letters and digits. Checking
# this stops odd values being used as Firestore document names.
CODE_PATTERN = re.compile(r"^[a-z0-9]{1,32}$")
MAX_CODES = 50

app = FastAPI(title="stats")
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


def check_code(code):
    if not CODE_PATTERN.match(code):
        raise HTTPException(status_code=422, detail="invalid code")


def unavailable(error):
    log.error("datastore error: %s", error)
    return HTTPException(status_code=503, detail="datastore unavailable")


# "async def" keeps this off the worker threads that datastore calls use.
# Even if every worker is stuck waiting for Firestore, liveness still
# answers, so Kubernetes does not restart a pod that is only waiting.
@app.get("/healthz")
async def healthz():
    return {"status": "ok", "service": "stats", "pod": socket.gethostname()}


@app.get("/readyz")
def readyz():
    try:
        store.ping()
    except Exception as error:
        raise unavailable(error)
    return {"status": "ready"}


@app.post("/clicks/{code}", status_code=202)
def add_click(code: str):
    check_code(code)
    try:
        store.increment(code)
    except Exception as error:
        raise unavailable(error)
    return {"code": code, "recorded": True}


@app.get("/clicks")
def get_clicks(codes: str = ""):
    wanted = [code for code in codes.split(",") if code]
    if len(wanted) > MAX_CODES:
        raise HTTPException(status_code=422, detail=f"at most {MAX_CODES} codes")
    for code in wanted:
        check_code(code)
    if not wanted:
        return {"clicks": {}}
    try:
        return {"clicks": store.get_many(wanted)}
    except Exception as error:
        raise unavailable(error)
