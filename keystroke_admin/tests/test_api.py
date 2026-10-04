"""End-to-end test Flask API cez test client (bez reálneho prehliadača)."""
import importlib
import os
import random
import tempfile

import pytest

def make_events(phrase, rng, sig=0, scale=1.0, jitter=15.0):
    """Rytmus závisí od znaku/bigramu (podpis používateľa `sig`), takže funguje pre každú vetu."""
    events, t = [], 100.0
    for i, ch in enumerate(phrase):
        dwell = ((70 + (ord(ch) * 7 + sig * 13) % 40) * scale) + rng.gauss(0, jitter)
        events.append({"key": ch, "code": "x", "down": round(t, 3), "up": round(t + dwell, 3)})
        nxt = phrase[i + 1] if i + 1 < len(phrase) else " "
        gap = ((110 + ((ord(ch) + ord(nxt)) * 5 + sig * 17) % 70) * scale) + rng.gauss(0, jitter)
        t += dwell + max(gap, 20.0)
    return events


@pytest.fixture()
def client(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("ALLOW_TEST_MODE", "0")
    import backend.config as config
    import backend.db as db
    import backend.app as appmod
    importlib.reload(config)
    importlib.reload(db)
    importlib.reload(appmod)
    import backend.sentences as sentences
    monkeypatch.setattr(sentences, "_rng", random.Random(1234))   # deterministické vety => stabilné testy
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    csrf = c.get("/api/bootstrap").get_json()["csrf"]
    c.csrf = {"X-CSRF-Token": csrf}
    return c


def enroll(c, username="P01", password="dlhe-heslo-123", n=12, jitter=15.0):
    rng = random.Random(1)
    uid = None
    for _ in range(n):
        b = c.post("/api/enrollment/begin", json={"username": username, "password": password,
                                                  "password_confirm": password}, headers=c.csrf).get_json()
        uid = b["user_id"]
        r = c.post("/api/enrollment/sample", json={"user_id": uid, "challenge": b["challenge"],
                                                   "events": make_events(b["phrase"], rng, jitter=jitter)}, headers=c.csrf)
        assert r.status_code == 200, r.get_json()
    r = c.post("/api/enrollment/finish", json={"user_id": uid}, headers=c.csrf)
    assert r.status_code == 200, r.get_json()


def verify(c, username, password, rng, **kw):
    b = c.post("/api/verification/begin", json={"username": username}, headers=c.csrf).get_json()
    return c.post("/api/verification", json={"username": username, "password": password,
                                             "challenge": b["challenge"],
                                             "events": make_events(b["phrase"], rng, **kw)}, headers=c.csrf)


def test_genuine_login_accepted(client):
    enroll(client)
    r = verify(client, "P01", "dlhe-heslo-123", random.Random(99))
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["accepted"] is True


def test_very_different_rhythm_rejected(client):
    enroll(client)
    r = verify(client, "P01", "dlhe-heslo-123", random.Random(5), sig=3, scale=2.0)
    assert r.status_code == 401
    assert r.get_json()["accepted"] is False


def test_wrong_password_is_generic(client):
    enroll(client)
    r = verify(client, "P01", "zle-heslo-xxx", random.Random(3))
    assert r.status_code == 401
    unknown = verify(client, "nikto", "zle-heslo-xxx", random.Random(3))
    assert r.get_json()["error"] == unknown.get_json()["error"]


def test_begin_does_not_reveal_unknown_user(client):
    enroll(client)
    real = client.post("/api/verification/begin", json={"username": "P01"}, headers=c_h(client))
    fake = client.post("/api/verification/begin", json={"username": "neexistuje"}, headers=c_h(client))
    assert real.status_code == fake.status_code == 200
    assert set(real.get_json()) == set(fake.get_json())


def test_client_cannot_enable_test_mode(client):
    enroll(client)
    b = client.post("/api/verification/begin", json={"username": "P01"}, headers=c_h(client)).get_json()
    r = client.post("/api/verification", json={
        "username": "P01", "password": "dlhe-heslo-123", "challenge": b["challenge"],
        "events": make_events(b["phrase"], random.Random(7)), "test_mode": True,
        "actor_label": "P02", "attempt_type": "impostor"}, headers=c_h(client))
    assert r.get_json().get("test_mode") is False


def test_each_challenge_gets_a_new_sentence(client):
    enroll(client)
    phrases = {client.post("/api/verification/begin", json={"username": "P01"}, headers=c_h(client)).get_json()["phrase"]
               for _ in range(8)}
    assert len(phrases) > 4


def test_login_with_typo_and_backspace(client):
    enroll(client)
    b = client.post("/api/verification/begin", json={"username": "P01"}, headers=c_h(client)).get_json()
    ev = make_events(b["phrase"], random.Random(21), jitter=5.0)
    # preklep na 10. znaku: zlý znak + Backspace, potom správny
    t = ev[9]["up"] + 30
    typo = [{"key": "q", "code": "x", "down": t, "up": t + 80}, {"key": "Backspace", "code": "x", "down": t + 150, "up": t + 220}]
    for e in ev[10:]:   # zvyšok vety sa posunie o čas, ktorý zabrala oprava
        e["down"] += 400
        e["up"] += 400
    ev = ev[:10] + typo + ev[10:]
    r = client.post("/api/verification", json={"username": "P01", "password": "dlhe-heslo-123",
                                               "challenge": b["challenge"], "events": ev}, headers=c_h(client))
    assert r.status_code == 200, r.get_json()


def test_text_must_match_the_servers_sentence(client):
    enroll(client)
    b = client.post("/api/verification/begin", json={"username": "P01"}, headers=c_h(client)).get_json()
    r = client.post("/api/verification", json={"username": "P01", "password": "dlhe-heslo-123",
                                               "challenge": b["challenge"],
                                               "events": make_events("ina veta uplne ina veta uplne ina", random.Random(2))},
                    headers=c_h(client))
    assert r.status_code == 400


def test_replay_of_identical_vector_rejected(client):
    enroll(client)
    b = client.post("/api/verification/begin", json={"username": "P01"}, headers=c_h(client)).get_json()
    ev = make_events(b["phrase"], random.Random(11))
    body = {"username": "P01", "password": "dlhe-heslo-123", "challenge": b["challenge"], "events": ev}
    assert client.post("/api/verification", json=body, headers=c_h(client)).status_code == 200
    b2 = client.post("/api/verification/begin", json={"username": "P01"}, headers=c_h(client)).get_json()
    body["challenge"] = b2["challenge"]   # nový challenge, rovnaké časovanie
    r = client.post("/api/verification", json=body, headers=c_h(client))
    assert r.status_code in (400, 401)


def c_h(c):
    return c.csrf


# ---------- mesačná obnova šablóny ----------

def login(client, rng=None, **kw):
    kw.setdefault("jitter", 5.0)   # probe bližšie k stredu šablóny: test nezávisí od šťastnej náhody
    r = verify(client, "P01", "dlhe-heslo-123", rng or random.Random(50), **kw)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def do_refresh(client, rng, **kw):
    """Prepíše vety, kým nie je REFRESH_SAMPLES prijatých (odmietnutú vzorku appka necháva zopakovať)."""
    replies = []
    for _ in range(14):
        b = client.post("/api/refresh/begin", json={}, headers=c_h(client)).get_json()
        r = client.post("/api/refresh/sample", json={
            "challenge": b["challenge"], "events": make_events(b["phrase"], rng, **kw)}, headers=c_h(client))
        replies.append(r)
        if r.status_code == 200 and r.get_json()["ready"]:
            break
    return replies, client.post("/api/refresh/finish", json={}, headers=c_h(client))


def age(client, days=31):
    import backend.db as db
    db.age_template(db.get_user_by_username("P01")["id"], days)


def test_refresh_not_due_right_after_enrollment(client):
    enroll(client)
    info = login(client)["refresh"]
    assert info["due"] is False and info["interval_days"] == 30


def test_refresh_due_after_a_month(client):
    enroll(client)
    age(client, 31)
    info = login(client)["refresh"]
    assert info["due"] is True and info["needed"] == 6


def test_refresh_requires_login(client):
    r = client.post("/api/refresh/begin", json={}, headers=c_h(client))
    assert r.status_code == 401


def test_refresh_faster_typing_is_improved_and_tightens_threshold(client):
    enroll(client)
    age(client, 31)
    login(client)
    replies, fin = do_refresh(client, random.Random(60), scale=0.9)
    assert sum(r.status_code == 200 for r in replies) == 6, [r.get_json() for r in replies]
    report = fin.get_json()
    assert fin.status_code == 200, report
    assert report["verdict"] == "improved"
    assert report["speed_change_pct"] < -5
    assert report["new_samples"] == 6 and report["cycle"] == 1
    # po obnove už nie je ďalšia obnova hneď splatná
    assert login(client, random.Random(61), scale=0.9)["refresh"]["due"] is False


def test_refresh_slower_typing_is_degraded_and_threshold_is_capped(client):
    enroll(client)
    old = login(client)["threshold"]
    age(client, 31)
    login(client, random.Random(70))
    replies, fin = do_refresh(client, random.Random(62), scale=1.12)
    report = fin.get_json()
    accepted = [r for r in replies if r.status_code == 200]
    if len(accepted) < 6:       # príliš odlišné vzorky sa odmietajú (ochrana pred otrávením)
        assert any(r.status_code == 422 for r in replies)
        assert fin.status_code == 400
    else:
        assert report["verdict"] == "degraded"
        assert report["new_threshold"] <= old * 1.25 + 1e-9


def test_refresh_rejects_samples_from_a_different_typist(client):
    enroll(client)
    age(client, 31)
    login(client)
    b = client.post("/api/refresh/begin", json={}, headers=c_h(client)).get_json()
    r = client.post("/api/refresh/sample", json={
        "challenge": b["challenge"], "events": make_events(b["phrase"], random.Random(8), sig=9, scale=2.0)},
        headers=c_h(client))
    assert r.status_code == 422 and r.get_json()["rejected"] is True


def test_refresh_finish_needs_enough_samples(client):
    enroll(client)
    age(client, 31)
    login(client)
    r = client.post("/api/refresh/finish", json={}, headers=c_h(client))
    assert r.status_code == 400


def test_age_endpoint_disabled_outside_test_mode(client):
    enroll(client)
    login(client)
    r = client.post("/api/admin/age-template", json={"user_id": 1}, headers=c_h(client))
    assert r.status_code == 403
