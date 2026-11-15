"""Storage for the links service.

Two backends with the same four methods:
  MemoryStore    - a Python dict. Used by the unit tests only.
  FirestoreStore - Google Cloud Firestore (the managed datastore).

The backend is picked by the STORE_BACKEND environment variable.
"""
import os
import time


class MemoryStore:
    def __init__(self):
        self._links = {}

    def ping(self):
        """Raise an exception if the datastore cannot be reached."""
        return True

    def create(self, code, url):
        """Save a new link. Return False if the code is already taken."""
        if code in self._links:
            return False
        self._links[code] = {"code": code, "url": url, "created": time.time()}
        return True

    def get(self, code):
        """Return the link as a dict, or None if it does not exist."""
        return self._links.get(code)

    def recent(self, limit):
        """Return the newest links first."""
        items = sorted(self._links.values(), key=lambda x: x["created"], reverse=True)
        return items[:limit]


class FirestoreStore:
    # Longest a Firestore call may take, in seconds. Without this the
    # client library keeps retrying an unreachable datastore for minutes
    # and the request hangs instead of failing with a quick 503.
    TIMEOUT = 3.0

    def __init__(self):
        # Imported here so the unit tests do not need Google credentials.
        from google.cloud import firestore
        from google.api_core import exceptions, retry

        self._firestore = firestore
        self._exceptions = exceptions
        # On GKE the client finds the project and credentials by itself
        # (Workload Identity). Locally it talks to the emulator when
        # FIRESTORE_EMULATOR_HOST is set.
        self._db = firestore.Client()
        self._col = self._db.collection("links")
        # Reads are safe to repeat, so short hiccups are retried, but only
        # until TIMEOUT. Writes are sent once (retry=None): repeating a
        # write whose answer was lost could apply it twice.
        self._read = {"retry": retry.Retry(timeout=self.TIMEOUT), "timeout": self.TIMEOUT}
        self._write = {"retry": None, "timeout": self.TIMEOUT}

    def ping(self):
        list(self._col.limit(1).stream(**self._read))
        return True

    def create(self, code, url):
        data = {"url": url, "created": time.time()}
        try:
            # create() fails if the document already exists, so two requests
            # can never both claim the same code.
            self._col.document(code).create(data, **self._write)
        except self._exceptions.Conflict:
            return False
        return True

    def get(self, code):
        snap = self._col.document(code).get(**self._read)
        if not snap.exists:
            return None
        data = snap.to_dict()
        return {"code": code, "url": data["url"], "created": data["created"]}

    def recent(self, limit):
        query = self._col.order_by(
            "created", direction=self._firestore.Query.DESCENDING
        ).limit(limit)
        result = []
        for snap in query.stream(**self._read):
            data = snap.to_dict()
            result.append({"code": snap.id, "url": data["url"], "created": data["created"]})
        return result


def make_store():
    backend = os.environ.get("STORE_BACKEND", "firestore")
    if backend == "memory":
        return MemoryStore()
    return FirestoreStore()
