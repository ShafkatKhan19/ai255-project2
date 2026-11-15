"""Send steady traffic at the web service to make the autoscaler add pods.

Usage:  python scripts/load_test.py http://ADDRESS [seconds] [threads]

Defaults: 180 seconds, 40 threads. It requests /api/whoami, which uses CPU
on the web pods but does not read or write Firestore, so the test costs
nothing in datastore operations. Prints requests per second and which pods
answered every 10 seconds. Uses only the standard library.

Each thread keeps its connection open for 50 requests and then opens a new
one. Opening a new connection for every request runs the PC out of free
ports within a minute; never reconnecting would pin each thread to one pod
and the new pods added by the autoscaler would get no traffic.
"""
import http.client
import json
import sys
import threading
import time
from urllib.parse import urlsplit

REQUESTS_PER_CONNECTION = 50


def worker(host, port, stop_at, counts, pods, lock):
    connection = None
    used = 0
    while time.time() < stop_at:
        try:
            if connection is None:
                connection = http.client.HTTPConnection(host, port, timeout=10)
                used = 0
            connection.request("GET", "/api/whoami")
            response = connection.getresponse()
            pod = json.loads(response.read())["pod"]
            used += 1
            with lock:
                counts["ok"] += 1
                pods[pod] = pods.get(pod, 0) + 1
            if used >= REQUESTS_PER_CONNECTION:
                connection.close()
                connection = None
        except Exception:
            with lock:
                counts["failed"] += 1
            if connection is not None:
                connection.close()
                connection = None
            time.sleep(0.05)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    target = urlsplit(sys.argv[1])
    host, port = target.hostname, target.port or 80
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 180
    threads = int(sys.argv[3]) if len(sys.argv) > 3 else 40

    counts = {"ok": 0, "failed": 0}
    pods = {}
    lock = threading.Lock()
    started = time.time()
    stop_at = started + seconds
    for _ in range(threads):
        threading.Thread(target=worker, args=(host, port, stop_at, counts, pods, lock), daemon=True).start()

    print(f"{threads} threads -> http://{host}:{port}/api/whoami for {seconds}s")
    last_ok = 0
    while time.time() < stop_at:
        time.sleep(10)
        with lock:
            ok, failed, seen = counts["ok"], counts["failed"], len(pods)
        elapsed = int(time.time() - started)
        print(f"t={elapsed:4d}s  {(ok - last_ok) / 10:7.1f} req/s  failed={failed}  web pods seen={seen}")
        last_ok = ok

    print(f"\ntotal ok={counts['ok']} failed={counts['failed']}")
    print("requests answered per pod:")
    for pod, n in sorted(pods.items()):
        print(f"  {pod}  {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
