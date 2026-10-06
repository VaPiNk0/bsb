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
def client(monkeypatch, request):
    for name, value in getattr(request, "param", {}).items():   # nastavenia nasadenia pre daný test
        monkeypatch.setenv(name, value)
    tmp = tempfile.mkdtemp()
    monkeypatch.setenv("DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("ALLOW_TEST_MODE", "0")
    monkeypatch.setenv("SECRET_KEY", "test")   # od kľúča závisí jazyk falošnej vety, a tým aj poradie viet
    # TEST_DATABASE_URL=postgresql://... pustí tie isté testy proti Postgresu (tabuľky sa zmažú!)
    monkeypatch.setenv("DATABASE_URL", os.getenv("TEST_DATABASE_URL", ""))
    import backend.config as config
    import backend.db as db
    import backend.app as appmod
    importlib.reload(config)
    importlib.reload(db)
    if db.IS_POSTGRES:
        with db._connect() as conn:
            conn.execute("DROP TABLE IF EXISTS " + ", ".join(db.TABLES) + " CASCADE")
    import backend.sentences as sentences
    importlib.reload(sentences)   # zoznam nedávno použitých viet sa nesmie prenášať medzi testami
    importlib.reload(appmod)
    monkeypatch.setattr(sentences, "_rng", random.Random(1234))   # deterministické vety => stabilné testy
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()
    if config.settings.PROXY_SECRET:
        c.environ_base["HTTP_X_PROXY_SECRET"] = config.settings.PROXY_SECRET
    csrf = c.get("/api/bootstrap").get_json()["csrf"]
    c.csrf = {"X-CSRF-Token": csrf}
    yield c
    if db._pool is not None:
        db._pool.close()


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


def test_unfinished_signup_fails_like_unknown_user(client):
    """Účet bez šablóny nesmie končiť na "neplatný challenge": klient by si donekonečna pýtal novú vetu."""
    body = {"username": "P02", "password": "dlhe-heslo-123", "password_confirm": "dlhe-heslo-123"}
    assert client.post("/api/enrollment/begin", json=body, headers=c_h(client)).status_code == 200
    unfinished = verify(client, "P02", "dlhe-heslo-123", random.Random(3))
    unknown = verify(client, "nikto", "dlhe-heslo-123", random.Random(3))
    assert unfinished.status_code == unknown.status_code == 401
    assert unfinished.get_json() == unknown.get_json()


def test_unfinished_signup_login_is_audited(client):
    import backend.db as db
    body = {"username": "P02", "password": "dlhe-heslo-123", "password_confirm": "dlhe-heslo-123"}
    uid = client.post("/api/enrollment/begin", json=body, headers=c_h(client)).get_json()["user_id"]
    verify(client, "P02", "dlhe-heslo-123", random.Random(3))
    verify(client, "nikto", "dlhe-heslo-123", random.Random(3))
    reasons = {row["username"]: row["reason"] for row in db.list_attempts(2)}
    assert reasons == {"P02": "not_enrolled", "nikto": "unknown_user"}
    # neúspešné pokusy na nedokončený účet ho nezamknú: lockout patrí až hotovému účtu
    assert db.get_user(uid)["failed_count"] == 0


def test_signup_can_continue_after_a_failed_login(client):
    """Presne cesta testera: rozbehnutá registrácia, pokus o prihlásenie, potom dokončenie a prihlásenie."""
    body = {"username": "P01", "password": "dlhe-heslo-123", "password_confirm": "dlhe-heslo-123"}
    assert client.post("/api/enrollment/begin", json=body, headers=c_h(client)).status_code == 200
    assert verify(client, "P01", "dlhe-heslo-123", random.Random(3)).status_code == 401
    enroll(client)
    r = verify(client, "P01", "dlhe-heslo-123", random.Random(50), jitter=5.0)
    assert r.status_code == 200 and r.get_json()["accepted"] is True


def test_concurrent_signup_of_one_name_is_not_a_server_error(client, monkeypatch):
    """Dvojité odoslanie formulára: druhá požiadavka účet ešte nevidí, ale meno je už zabraté."""
    import backend.app as appmod
    import backend.db as db
    body = {"username": "P02", "password": "dlhe-heslo-123", "password_confirm": "dlhe-heslo-123"}
    assert client.post("/api/enrollment/begin", json=body, headers=c_h(client)).status_code == 200
    with monkeypatch.context() as patch:
        patch.setattr(appmod, "get_user_by_username", lambda username: None)
        r = client.post("/api/enrollment/begin", json=body, headers=c_h(client))
    assert r.status_code == 409 and "práve prebieha" in r.get_json()["error"]
    assert len(db.list_users()) == 1
    # po neúspešnej súbežnej požiadavke registrácia normálne pokračuje
    assert client.post("/api/enrollment/begin", json=body, headers=c_h(client)).status_code == 200


def test_unexpected_error_is_readable_json(client, monkeypatch):
    import backend.app as appmod

    def broken(*args, **kwargs):
        raise RuntimeError("databáza neodpovedá")

    monkeypatch.setitem(appmod.app.config, "TESTING", False)   # inak test client výnimku prepustí von
    monkeypatch.setattr(appmod, "make_challenge", broken)
    body = {"username": "P02", "password": "dlhe-heslo-123", "password_confirm": "dlhe-heslo-123"}
    r = client.post("/api/enrollment/begin", json=body, headers=c_h(client))
    assert r.status_code == 500 and r.is_json
    assert r.get_json() == {"error": "Na serveri nastala chyba. Skús to o chvíľu znova."}   # bez detailov výnimky
    r = client.post("/api/enrollment/begin", json=body, headers={**c_h(client), "X-Lang": "en"})
    assert r.get_json() == {"error": "The server ran into an error. Try again in a moment."}


def test_postgres_pool_checks_connections_before_use(client, monkeypatch):
    """Pooler nečinné spojenie zavrie; pool ho musí pred použitím overiť, inak prvá požiadavka spadne."""
    psycopg_pool = pytest.importorskip("psycopg_pool")
    import backend.db as db
    if db.IS_POSTGRES:
        pytest.skip("pool je skutočný, nastavenie sa overuje len so zástupným")

    class FakePool:
        made = []

        def __init__(self, conninfo, **options):
            self.options = options
            FakePool.made.append(self)

        @staticmethod
        def check_connection(conn):
            pass

        def close(self):
            pass

    monkeypatch.setattr(psycopg_pool, "ConnectionPool", FakePool)
    monkeypatch.setattr(db, "_pool", None)
    assert db._postgres_pool() is db._postgres_pool()          # jeden pool na proces
    assert len(FakePool.made) == 1
    assert FakePool.made[0].options["check"] is FakePool.check_connection
    assert FakePool.made[0].options["kwargs"]["prepare_threshold"] is None


def test_page_warns_touch_devices_only_on_demand(client):
    """Upozornenie na fyzickú klávesnicu je v stránke skryté; zobrazí ho až skript na dotykovom zariadení."""
    html = client.get("/").get_data(as_text=True)
    assert '<section class="notice" id="touchNotice" hidden>' in html
    # zamietacia obrazovka pre telefón je tiež skrytá a vedie preč z aplikácie
    block = html.split('id="phoneBlock" hidden>')[1].split("</section>")[0]
    assert 'href="https://www.google.com/"' in block


@pytest.mark.parametrize("agent, allowed", [
    ("Mozilla/5.0 (Linux; Android 16; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Mobile Safari/537.36", False),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/19.0 Mobile/15E148 Safari/604.1", False),
    ("Mozilla/5.0 (Android 16; Mobile; rv:144.0) Gecko/144.0 Firefox/144.0", False),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36", True),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/19.0 Safari/605.1.15", True),
    ("Mozilla/5.0 (Linux; Android 16; SM-X910) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36", True),   # tablet
])
def test_phones_are_turned_away(client, agent, allowed):
    import backend.db as db
    headers = {**c_h(client), "User-Agent": agent}
    body = {"username": "P02", "password": "dlhe-heslo-123", "password_confirm": "dlhe-heslo-123"}
    assert client.get("/api/bootstrap", headers=headers).status_code == (200 if allowed else 403)
    r = client.post("/api/enrollment/begin", json=body, headers=headers)
    assert r.status_code == (200 if allowed else 403)
    assert (db.get_user_by_username("P02") is not None) == allowed     # telefón účet ani nezaloží
    if not allowed:
        assert "fyzickú klávesnicu" in r.get_json()["error"]
        assert client.post("/api/verification/begin", json={"username": "P02"}, headers=headers).status_code == 403
        assert client.get("/api/health", headers=headers).status_code == 200   # health check hostingu


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
    ev = make_events(b["phrase"], random.Random(11), jitter=5.0)
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
    replies, fin = do_refresh(client, random.Random(60), scale=0.9, jitter=10.0)
    assert sum(r.status_code == 200 for r in replies) == 6, [r.get_json() for r in replies]
    report = fin.get_json()
    assert fin.status_code == 200, report
    assert report["verdict"] == "improved", report
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


def test_template_from_older_scoring_is_rebuilt_on_login(client):
    """Šablóna zo staršej verzie skórovania (kratšie vektory, iný prah) sa pri prihlásení prepočíta."""
    import json
    import backend.db as db
    from backend.scoring import TEMPLATE_VERSION
    enroll(client)
    user = db.get_user_by_username("P01")
    stale = json.loads(user["template_json"])
    stale["version"] = TEMPLATE_VERSION - 1
    stale["threshold"] = 99.0
    db.save_template(user["id"], stale)
    assert login(client)["threshold"] < 10
    assert json.loads(db.get_user_by_username("P01")["template_json"])["version"] == TEMPLATE_VERSION


def test_enrollment_checks_password_of_existing_account(client):
    """Rozpracovanú registráciu nemôže z inej relácie prevziať niekto bez hesla."""
    import backend.app as appmod
    body = {"username": "P02", "password": "dlhe-heslo-123", "password_confirm": "dlhe-heslo-123"}
    assert client.post("/api/enrollment/begin", json=body, headers=c_h(client)).status_code == 200
    other = appmod.app.test_client()
    headers = {"X-CSRF-Token": other.get("/api/bootstrap").get_json()["csrf"]}
    wrong = dict(body, password="ine-heslo-456789", password_confirm="ine-heslo-456789")
    assert other.post("/api/enrollment/begin", json=wrong, headers=headers).status_code == 409
    assert other.post("/api/enrollment/begin", json=body, headers=headers).status_code == 200


# ---------- nasadenie: ochrana proti botom a proxy ----------

@pytest.mark.parametrize("client", [{"TURNSTILE_SITE_KEY": "site", "TURNSTILE_SECRET": "secret"}], indirect=True)
def test_bot_check_is_required_once_per_session(client, monkeypatch):
    import backend.app as appmod
    seen = []
    monkeypatch.setattr(appmod, "turnstile_ok", lambda token: seen.append(token) or token == "good")
    assert client.get("/api/bootstrap").get_json()["turnstile_site_key"] == "site"
    body = {"username": "P03", "password": "dlhe-heslo-123", "password_confirm": "dlhe-heslo-123"}

    r = client.post("/api/enrollment/begin", json=body, headers=c_h(client))
    assert r.status_code == 403 and r.get_json()["captcha"] is True          # bez tokenu
    r = client.post("/api/verification/begin", json={"username": "P03", "turnstile": "bad"}, headers=c_h(client))
    assert r.status_code == 403
    import backend.db as db
    assert db.get_user_by_username("P03") is None                             # bot účet nevytvoril

    assert client.post("/api/enrollment/begin", json=dict(body, turnstile="good"), headers=c_h(client)).status_code == 200
    # ďalšie vety tej istej relácie už token nepotrebujú a Cloudflare sa znova nevolá
    assert client.post("/api/enrollment/begin", json=body, headers=c_h(client)).status_code == 200
    assert seen == ["bad", "good"]


@pytest.mark.parametrize("client", [{"PROXY_SECRET": "tajne"}], indirect=True)
def test_api_answers_only_through_the_proxy(client):
    import backend.app as appmod
    direct = appmod.app.test_client()
    assert direct.get("/api/bootstrap").status_code == 403
    assert direct.get("/api/bootstrap", headers={"X-Proxy-Secret": "zle"}).status_code == 403
    assert direct.get("/api/health").status_code == 200                       # health check hostingu
    assert client.get("/api/bootstrap").status_code == 200

    # IP klienta sa berie z hlavičky proxy, nie z adresy proxy servera
    client.post("/api/verification", json={"username": "nikto", "password": "x", "challenge": "x", "events": []},
                headers={**c_h(client), "X-Client-IP": "203.0.113.7"})
    import backend.db as db
    assert db.list_attempts(1)[0]["ip"] == "203.0.113.7"


@pytest.mark.parametrize("client", [{"ADMIN_USERS": "P01"}], indirect=True)
def test_admin_panel_is_limited_to_listed_accounts(client):
    enroll(client)
    enroll(client, username="P02")
    admin = login(client)
    assert admin["is_admin"] is True and admin["redirect"] is None
    assert client.get("/api/admin/overview").status_code == 200
    assert client.get("/admin").status_code == 200

    r = verify(client, "P02", "dlhe-heslo-123", random.Random(50), jitter=5.0)
    assert r.status_code == 200 and r.get_json()["is_admin"] is False     # prihlásený, ale nie admin
    assert r.get_json()["redirect"] == "https://www.google.sk/"           # bežný používateľ ide von z aplikácie
    assert client.get("/api/admin/overview").status_code == 403
    assert client.get("/admin").status_code == 302
    assert client.get("/api/refresh/status").status_code == 200           # vlastné veci mu fungujú ďalej


def test_admin_panel_requires_login(client):
    assert client.get("/api/admin/overview").status_code == 401
    assert client.get("/api/admin/report/scenarios.csv").status_code == 401


def test_admin_report_matches_recorded_decisions(client):
    enroll(client)
    login(client)
    assert verify(client, "P01", "dlhe-heslo-123", random.Random(5), sig=3, scale=2.0).status_code == 401
    verify(client, "P01", "zle-heslo-xxx", random.Random(3))   # bez skóre, do FRR nepatrí

    report = client.get("/api/admin/overview").get_json()["report"]
    genuine = report["scenarios"][0]
    assert (genuine["scenario"], genuine["n"], genuine["errors"], genuine["rate"]) == ("genuine", 2, 1, 0.5)
    assert report["excluded"] == [{"reason": "password_failed", "n": 1}]
    assert report["users"][0]["enrollment_n"] == 12 and report["users"][0]["median_dd_ms"] > 0

    for name, first_column in (("scenarios", "scenario"), ("users", "username"), ("attempts", "id")):
        r = client.get(f"/api/admin/report/{name}.csv")
        assert r.status_code == 200 and r.mimetype == "text/csv"
        lines = r.get_data(as_text=True).lstrip("﻿").splitlines()
        assert lines[0].split(",")[0] == first_column and len(lines) > 1
    assert client.get("/api/admin/report/nieco.csv").status_code == 404


# ---------- jazyk viet a hlášok ----------

def test_sentence_language_is_bound_to_the_account(client):
    from backend.corpus import CORPUS
    english = {**c_h(client), "X-Lang": "en"}
    body = {"username": "P05", "password": "dlhe-heslo-123", "password_confirm": "dlhe-heslo-123"}
    assert client.post("/api/enrollment/begin", json=body, headers=english).get_json()["phrase"] in CORPUS["en"]
    # účet vznikol s anglickými vetami; prepnutie rozhrania na slovenčinu ich nezmení
    assert client.post("/api/enrollment/begin", json=body, headers=c_h(client)).get_json()["phrase"] in CORPUS["en"]

    enroll(client)                                           # P01 bez hlavičky => slovenské vety
    r = client.post("/api/verification/begin", json={"username": "P01"}, headers=english)
    assert r.get_json()["phrase"] in CORPUS["sk"]

    # neexistujúci účet sa musí správať rovnako: jazyk vety nezávisí od hlavičky, inak by prezradil, že účet nie je
    def decoy_language(headers):
        phrase = client.post("/api/verification/begin", json={"username": "nikto"}, headers=headers).get_json()["phrase"]
        return "en" if phrase in CORPUS["en"] else "sk"
    assert decoy_language(english) == decoy_language(c_h(client)) == decoy_language(english)


def test_errors_are_translated_on_request(client):
    body = {"username": "P06", "password": "kratke", "password_confirm": "kratke"}
    slovak = client.post("/api/enrollment/begin", json=body, headers=c_h(client)).get_json()["error"]
    english = client.post("/api/enrollment/begin", json=body, headers={**c_h(client), "X-Lang": "en"}).get_json()["error"]
    assert slovak == "Heslo musí mať aspoň 10 znakov"
    assert english == "The password must be at least 10 characters"
