"""Tests for the FastAPI wiring in app.main — scenario load ordering, the
concurrency lock, and basic endpoint error handling. Previously undisclosed
gap in coverage per docs/BUGS.md; every invariant-bearing module had unit
tests, but the HTTP layer that ties them together didn't.
"""
import threading

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_unknown_scenario_returns_404_not_a_crash():
    resp = client.post("/api/scenario/does_not_exist")
    assert resp.status_code == 404


def test_normal_scenario_loads_and_digest_responds():
    resp = client.post("/api/scenario/normal")
    assert resp.status_code == 200
    resp = client.get("/api/digest")
    assert resp.status_code == 200
    assert resp.json()["scenario"] == "normal"


def test_concurrent_scenario_reload_and_reads_stay_internally_consistent():
    """The lock in app.main guards exactly this: without it, a reader can
    observe _states already swapped to the new scenario while _scenario (or
    _watermarks) is still the old one, because the five-name reassignment at
    the end of _load_scenario is not atomic across threads on its own. This
    hammers a scenario reload against concurrent reads and asserts every
    observed response is self-consistent — RELIANCE's raw price always
    matches the scenario tag reported in that same response.

    This is a stress test, not a proof: it demonstrates the absence of the
    specific torn read many times under real thread interleaving, backed by
    a structural fix (the lock), not by the test run alone. See docs/LLD.md.
    """
    errors = []

    def reload_loop():
        for i in range(25):
            name = "split_day" if i % 2 == 0 else "normal"
            client.post(f"/api/scenario/{name}")

    def read_loop():
        for _ in range(25):
            wl = client.get("/api/watchlist").json()
            reliance = next(r for r in wl["watchlist"] if r["isin"] == "INE002A01018")
            scenario = wl["scenario"]
            if scenario == "split_day" and not (200 <= reliance["ltp"] <= 260):
                errors.append(("split_day", reliance["ltp"]))
            elif scenario == "normal" and not (2400 <= reliance["ltp"] <= 2500):
                errors.append(("normal", reliance["ltp"]))

    threads = [threading.Thread(target=reload_loop) for _ in range(2)]
    threads += [threading.Thread(target=read_loop) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"torn reads observed: {errors}"

    client.post("/api/scenario/normal")  # leave state clean for other tests
