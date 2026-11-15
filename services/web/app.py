"""web service: the only public service. Serves the page and is the gateway
to the two internal services.

  browser -> web -> links  (create and look up short links)
                 -> stats  (count clicks)

Endpoints
  GET  /              the page
  GET  /api/links     list of links with click counts
  POST /api/links     create a short link
  GET  /r/{code}      redirect to the original address and count the click
  GET  /api/status    whether links and stats can do their job right now
  GET  /api/whoami    which pod answered (used to show load balancing)
  GET  /healthz       liveness
  GET  /readyz        readiness
"""
import asyncio
import logging
import os
import re
import socket
import time
import uuid
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s web %(levelname)s %(message)s")
log = logging.getLogger("web")

# Inside the cluster these names are resolved by Kubernetes DNS to the
# Service in front of each set of pods.
LINKS_URL = os.environ.get("LINKS_URL", "http://links:8080")
STATS_URL = os.environ.get("STATS_URL", "http://stats:8080")

# links is needed to answer, so it gets a longer wait. stats is optional,
# so it gets a short one and the page never waits long for it.
LINKS_TIMEOUT = 4.0
STATS_TIMEOUT = 1.0

CODE_PATTERN = re.compile(r"^[a-z0-9]{1,32}$")
POD = socket.gethostname()
INDEX = Path(__file__).parent / "static" / "index.html"

app = FastAPI(title="web")

# One shared client, so connections to links and stats are reused. Idle
# connections are dropped after 5 seconds: a Kubernetes Service picks a pod
# when a connection is opened, so long-lived connections would keep sending
# everything to the same pods and ignore new ones added by the autoscaler.
http = httpx.AsyncClient(limits=httpx.Limits(keepalive_expiry=5.0))


@app.middleware("http")
async def tag_and_log(request: Request, call_next):
    """Give every request an ID, pass it to links and stats, and log it.

    Searching the three services' logs for one ID shows the whole path of
    one request.
    """
    request.state.request_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Served-By"] = POD
    response.headers["X-Request-ID"] = request.state.request_id
    if request.url.path not in ("/healthz", "/readyz", "/api/whoami"):
        log.info(
            "%s %s -> %s in %.0f ms request_id=%s",
            request.method, request.url.path, response.status_code,
            (time.perf_counter() - started) * 1000, request.state.request_id,
        )
    return response


async def call(method, url, timeout, request_id, retries=0, **kwargs):
    """Call another service.

    A retry happens only when the connection failed (for example the pod
    was just killed), not when the service was slow: retrying a slow
    service only adds load to it. Retries are only requested for GETs,
    because repeating a GET cannot create anything twice.
    """
    headers = {"X-Request-ID": request_id}
    attempt = 0
    while True:
        try:
            return await http.request(method, url, timeout=timeout, headers=headers, **kwargs)
        except httpx.TimeoutException:
            raise
        except httpx.TransportError as error:
            if attempt >= retries:
                raise
            attempt += 1
            log.warning("retrying %s %s after %s", method, url, type(error).__name__)


def links_down(error):
    log.error("links service unavailable: %s", error)
    return HTTPException(
        status_code=503,
        detail="links service unavailable",
        headers={"Retry-After": "5"},
    )


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "service": "web", "pod": POD}


@app.get("/readyz")
async def readyz():
    # web is ready as soon as it is running. It does not check links or
    # stats here on purpose: if it did, one broken backend would mark every
    # web pod unready and the whole site would vanish instead of showing a
    # clear error message.
    return {"status": "ready"}


@app.get("/api/whoami")
async def whoami():
    return {"pod": POD}


@app.get("/")
async def index():
    return FileResponse(INDEX)


async def backend_state(base_url, request_id):
    """'ok' if the service's readiness check passes, otherwise 'down'."""
    try:
        response = await call("GET", f"{base_url}/readyz", LINKS_TIMEOUT, request_id)
        return "ok" if response.status_code == 200 else "down"
    except httpx.HTTPError:
        return "down"


@app.get("/api/status")
async def status(request: Request):
    request_id = request.state.request_id
    links, stats = await asyncio.gather(
        backend_state(LINKS_URL, request_id), backend_state(STATS_URL, request_id)
    )
    return {"web": "ok", "links": links, "stats": stats, "pod": POD}


@app.get("/api/links")
async def list_links(request: Request):
    request_id = request.state.request_id
    try:
        response = await call("GET", f"{LINKS_URL}/links", LINKS_TIMEOUT, request_id, retries=1)
    except httpx.HTTPError as error:
        raise links_down(error)
    if response.status_code != 200:
        raise links_down(f"status {response.status_code}")
    links = response.json()["links"]

    # Click counts are a nice-to-have. If stats is down, still show the links.
    stats_available = True
    counts = {}
    if links:
        codes = ",".join(link["code"] for link in links)
        try:
            response = await call(
                "GET", f"{STATS_URL}/clicks", STATS_TIMEOUT, request_id, params={"codes": codes}
            )
            if response.status_code == 200:
                counts = response.json()["clicks"]
            else:
                stats_available = False
        except httpx.HTTPError as error:
            log.warning("stats unavailable: %s", type(error).__name__)
            stats_available = False

    base = str(request.base_url)
    for link in links:
        link["short_url"] = f"{base}r/{link['code']}"
        link["clicks"] = counts.get(link["code"], 0) if stats_available else None
    return {"links": links, "stats_available": stats_available, "pod": POD}


@app.post("/api/links", status_code=201)
async def create_link(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="body must be JSON")
    try:
        # No retry here: sending a create twice could make two links.
        response = await call(
            "POST", f"{LINKS_URL}/links", LINKS_TIMEOUT, request.state.request_id, json=body
        )
    except httpx.HTTPError as error:
        raise links_down(error)
    if response.status_code == 422:
        raise HTTPException(status_code=422, detail="please enter a valid http or https address")
    if response.status_code != 201:
        raise links_down(f"status {response.status_code}")
    link = response.json()
    link["short_url"] = f"{request.base_url}r/{link['code']}"
    return link


async def record_click(code, request_id):
    """Tell stats about a click. Runs after the redirect has been sent."""
    try:
        response = await call("POST", f"{STATS_URL}/clicks/{code}", STATS_TIMEOUT, request_id)
        if response.status_code != 202:
            log.warning("click for %s not recorded: status %s", code, response.status_code)
    except httpx.HTTPError as error:
        # The click is lost, but the visitor already got their redirect.
        log.warning("click for %s not recorded: %s", code, type(error).__name__)


@app.get("/r/{code}")
async def follow(code: str, request: Request, background: BackgroundTasks):
    request_id = request.state.request_id
    if not CODE_PATTERN.match(code):
        raise HTTPException(status_code=404, detail="link not found")
    try:
        response = await call(
            "GET", f"{LINKS_URL}/links/{code}", LINKS_TIMEOUT, request_id, retries=1
        )
    except httpx.HTTPError as error:
        raise links_down(error)
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="link not found")
    if response.status_code != 200:
        raise links_down(f"status {response.status_code}")
    background.add_task(record_click, code, request_id)
    # 307 is a temporary redirect, so browsers ask again next time and
    # every click is counted.
    return RedirectResponse(response.json()["url"], status_code=307)
