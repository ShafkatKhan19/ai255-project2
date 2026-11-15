"""Measure availability while something is being broken on purpose.

Usage:  python scripts/availability.py http://ADDRESS [seconds] [path]

Defaults: 60 seconds, path /api/links. Sends about 5 requests a second
and prints one line per second: how many succeeded, how many failed and
the slowest response. Start it, then kill a pod or scale a service to
zero in another window. The summary at the end is the evidence for the
report. Uses only the standard library.
"""
import sys
import time
import urllib.error
import urllib.request

INTERVAL = 0.2


def one_request(url):
    started = time.time()
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            response.read()
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code
    except Exception:
        status = 0  # no answer at all
    return status, time.time() - started


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    path = sys.argv[3] if len(sys.argv) > 3 else "/api/links"
    url = sys.argv[1].rstrip("/") + path

    print(f"GET {url} for {seconds}s")
    print("second  ok  failed  slowest_ms  statuses")
    totals = {}
    slowest_overall = 0.0
    first_failure = last_failure = None
    start = time.time()
    second = 0
    while time.time() - start < seconds:
        bucket_end = start + second + 1
        statuses = []
        slowest = 0.0
        while time.time() < bucket_end:
            status, elapsed = one_request(url)
            statuses.append(status)
            slowest = max(slowest, elapsed)
            totals[status] = totals.get(status, 0) + 1
            if status != 200:
                now = time.time() - start
                first_failure = now if first_failure is None else first_failure
                last_failure = now
            time.sleep(max(0.0, INTERVAL - elapsed))
        ok = statuses.count(200)
        failed = len(statuses) - ok
        seen = ",".join(str(s) for s in sorted(set(statuses)))
        print(f"{second:6d}  {ok:2d}  {failed:6d}  {slowest * 1000:10.0f}  {seen}")
        slowest_overall = max(slowest_overall, slowest)
        second += 1

    total = sum(totals.values())
    ok = totals.get(200, 0)
    print(f"\nrequests={total} ok={ok} failed={total - ok} "
          f"success={100.0 * ok / total:.2f}% slowest={slowest_overall * 1000:.0f} ms")
    print("by status (0 = no answer): " + ", ".join(f"{s}: {n}" for s, n in sorted(totals.items())))
    if first_failure is not None:
        print(f"failures between t={first_failure:.1f}s and t={last_failure:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
