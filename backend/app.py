from __future__ import annotations

import json
import math
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, jsonify, redirect, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .config import settings
from .db import (
    add_enrollment_sample,
    add_refresh_sample,
    age_template,
    clear_refresh_samples,
    consume_challenge,
    create_challenge,
    create_user,
    duplicate_attempt_exists,
    get_enrollment_samples,
    get_user,
    get_user_by_username,
    get_refresh_samples,
    init_db,
    insert_attempt,
    insert_history,
    list_history,
    stamp_template,
    list_attempts,
    list_users,
    recent_attempt_count,
    save_template,
    feature_hash,
    update_auth_state,
)
from .drift import analyze_drift, decide_threshold
from .features import FEATURE_COUNT, extract_features
from .scoring import TEMPLATE_VERSION, build_template, score_vector
from .sentences import next_phrase


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
ATTEMPT_TYPES = {"genuine", "impostor", "imitacia"}

app = Flask(__name__, static_folder=None)  # vlastná /static cesta nižšie obsluhuje frontend/
app.secret_key = settings.SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=64 * 1024,
)

init_db()


def client_ip() -> str:
    # Demo server beží bez reverse proxy; X-Forwarded-For by default never dôverujeme.
    return request.remote_addr or "unknown"


def csrf_token() -> str:
    token = session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf"] = token
    return token


def require_csrf() -> None:
    supplied = request.headers.get("X-CSRF-Token", "")
    if not secrets.compare_digest(supplied, csrf_token()):
        raise ValueError("Neplatný CSRF token")


def json_body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValueError("Očakávam JSON objekt")
    return body


def validate_username(username: str) -> str:
    username = str(username).strip()
    if not USERNAME_RE.fullmatch(username):
        raise ValueError("Meno musí obsahovať 3–32 znakov: A-Z, a-z, 0-9, ., _, -")
    return username


def is_locked(user) -> bool:
    lock_until = user["lock_until"]
    if not lock_until:
        return False
    return datetime.fromisoformat(lock_until) > datetime.now(timezone.utc)


def refresh_info(user) -> dict:
    """Stav mesačnej obnovy šablóny pre daného používateľa."""
    base = user["template_updated_at"] or user["created_at"]
    updated = datetime.fromisoformat(base)
    due_at = updated + timedelta(days=settings.TEMPLATE_REFRESH_DAYS)
    now = datetime.now(timezone.utc)
    cycle = int(user["template_cycle"] or 0)
    return {
        "due": bool(user["template_json"]) and now >= due_at,
        "days_since": max(0, (now - updated).days),
        "interval_days": settings.TEMPLATE_REFRESH_DAYS,
        "due_in_days": (due_at - now).days,
        "cycle": cycle,
        "pending": len(get_refresh_samples(int(user["id"]), cycle + 1)),
        "needed": settings.REFRESH_SAMPLES,
    }


def vectors_of(rows) -> list[list[float | None]]:
    return [json.loads(row["features_json"]) for row in rows]


def make_template(vectors: list[list[float | None]]) -> dict:
    return build_template(
        vectors,
        metric=settings.METRIC,
        k=settings.K,
        floor=settings.MAD_FLOOR_MS,
        z_limit=settings.ROBUST_Z_LIMIT,
        shrinkage=settings.COV_SHRINKAGE,
    )


def current_template(user) -> dict:
    """Šablóna používateľa; ak ju vytvorila iná konfigurácia alebo staršia verzia skórovania,
    prepočíta sa z uložených enrollment vzoriek (posledných MAX_ENROLLMENT)."""
    template = json.loads(user["template_json"])
    if (
        template.get("version") == TEMPLATE_VERSION
        and template["metric"] == settings.METRIC
        and float(template["k"]) == settings.K
    ):
        return template
    user_id = int(user["id"])
    template = make_template(vectors_of(get_enrollment_samples(user_id)[-settings.MAX_ENROLLMENT:]))
    save_template(user_id, template)
    return template


def session_user():
    if "auth_user_id" not in session:
        raise PermissionError("Najprv sa prihlás.")
    user = get_user(int(session["auth_user_id"]))
    if not user or not user["template_json"]:
        raise PermissionError("Najprv sa prihlás.")
    return user


def verify_rate_limit(username: str) -> None:
    count = recent_attempt_count(client_ip(), username, settings.RATE_LIMIT_WINDOW_SECONDS)
    if count >= settings.RATE_LIMIT_MAX_ATTEMPTS:
        raise PermissionError("Príliš veľa pokusov, chvíľu počkaj.")


def make_challenge(user_id: int, purpose: str) -> tuple[str, int, str]:
    """Každý challenge nesie vlastnú vetu; server si ju pamätá, klient ju nevie zmeniť."""
    token = secrets.token_urlsafe(32)
    phrase, _source = next_phrase()
    challenge_id = create_challenge(user_id, purpose, token, settings.CHALLENGE_TTL_SECONDS, phrase)
    return token, challenge_id, phrase


def attempt_metadata(body: dict, username: str) -> tuple[str, str, str | None, bool]:
    test_requested = bool(body.get("test_mode", False)) and settings.ALLOW_TEST_MODE
    if not test_requested:
        return "genuine", username, None, False

    attempt_type = str(body.get("attempt_type", "genuine")).lower()
    if attempt_type not in ATTEMPT_TYPES:
        raise ValueError("Neplatný typ pokusu")
    actor = str(body.get("actor_label", "")).strip()[:64]
    if not actor:
        raise ValueError("V test mode musí byť zadaný tester ID")
    if attempt_type == "genuine" and actor.lower() != username.lower():
        raise ValueError("Genuine pokus musí mať tester ID rovnaké ako cieľový účet")
    if attempt_type != "genuine" and actor.lower() == username.lower():
        raise ValueError("Impostor/imitácia musí pochádzať od iného testera")
    impersonated = username if attempt_type == "imitacia" else None
    return attempt_type, actor, impersonated, True


def store_attempt(
    *, user_id: int | None, username: str, actor: str | None, attempt_type: str,
    impersonated: str | None, score: float | None, threshold: float | None,
    accepted: bool, features: list[float] | None, challenge_id: int | None,
    replay: bool = False, reason: str | None = None, phrase: str | None = None,
) -> None:
    digest = feature_hash(features) if features is not None else None
    insert_attempt(
        {
            "user_id": user_id,
            "username": username,
            "actor_label": actor,
            "attempt_type": attempt_type,
            "impersonated_username": impersonated,
            "score": score,
            "threshold": threshold,
            "accepted": 1 if accepted else 0,
            "feature_json": json.dumps(features) if features is not None else None,
            "feature_hash": digest,
            "phrase": phrase,
            "challenge_id": challenge_id,
            "replay_detected": 1 if replay else 0,
            "reason": reason,
            "ip": client_ip(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    return response


@app.get("/")
def index():
    csrf_token()
    return send_from_directory(FRONTEND, "index.html")


@app.get("/admin")
def admin_page():
    if "auth_user_id" not in session:
        return redirect(url_for("index"))
    return send_from_directory(FRONTEND, "admin.html")


@app.get("/static/<path:filename>")
def static_files(filename: str):
    return send_from_directory(FRONTEND, filename)


@app.get("/api/bootstrap")
def bootstrap():
    token = csrf_token()
    return jsonify(
        csrf=token,
        feature_count=FEATURE_COUNT,
        max_corrections=settings.MAX_CORRECTIONS,
        challenge_ttl=settings.CHALLENGE_TTL_SECONDS,
        min_enrollment=settings.MIN_ENROLLMENT,
        max_enrollment=settings.MAX_ENROLLMENT,
        metric=settings.METRIC,
        k=settings.K,
        test_mode_allowed=settings.ALLOW_TEST_MODE,
    )


@app.post("/api/enrollment/begin")
def enrollment_begin():
    try:
        require_csrf()
        body = json_body()
        username = validate_username(body.get("username", ""))
        password = str(body.get("password", ""))
        confirm = str(body.get("password_confirm", ""))
        if len(password) < 10:
            raise ValueError("Heslo musí mať aspoň 10 znakov")
        if password != confirm:
            raise ValueError("Heslá sa nezhodujú")

        user = get_user_by_username(username)
        if user:
            if user["template_json"]:
                return jsonify(error="Používateľ už má vytvorenú šablónu."), 409
            user_id = int(user["id"])
            # Heslo sa pri registrácii overuje raz; ďalšie vety tej istej relácie už scrypt
            # nepočítajú znova (inak ~0,3 s navyše pred každou vetou).
            if session.get("enroll_user_id") != user_id and not check_password_hash(user["password_hash"], password):
                return jsonify(error="Používateľ existuje, ale heslo nesedí."), 409
        else:
            user_id = create_user(username, generate_password_hash(password))
        session["enroll_user_id"] = user_id
        token, challenge_id, phrase = make_challenge(user_id, "enrollment")
        count = len(get_enrollment_samples(user_id))
        return jsonify(
            user_id=user_id,
            challenge=token,
            challenge_id=challenge_id,
            count=count,
            phrase=phrase,
            min_enrollment=settings.MIN_ENROLLMENT,
            max_enrollment=settings.MAX_ENROLLMENT,
        )
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/enrollment/sample")
def enrollment_sample():
    try:
        require_csrf()
        body = json_body()
        user_id = int(body.get("user_id"))
        token = str(body.get("challenge", ""))
        user = get_user(user_id)
        if not user:
            raise ValueError("Používateľ neexistuje")
        challenge = consume_challenge(token, user_id, "enrollment")
        if not challenge:
            raise ValueError("Challenge je neplatný alebo expirovaný")
        features = extract_features(body.get("events", []), challenge["phrase"], settings.MAX_CORRECTIONS)
        digest = feature_hash(features)
        samples = get_enrollment_samples(user_id)
        if any(row["feature_hash"] == digest for row in samples):
            return jsonify(error="Identická enrollment vzorka bola už použitá. Zopakuj ju."), 409
        existing = len(samples)
        if existing >= settings.MAX_ENROLLMENT:
            return jsonify(error="Maximum enrollment vzoriek už bolo dosiahnuté."), 409
        add_enrollment_sample(user_id, existing + 1, features, challenge["phrase"])
        new_count = existing + 1
        template_ready = new_count >= settings.MIN_ENROLLMENT
        return jsonify(count=new_count, template_ready=template_ready, max_enrollment=settings.MAX_ENROLLMENT)
    except (ValueError, TypeError) as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/enrollment/finish")
def enrollment_finish():
    try:
        require_csrf()
        body = json_body()
        user_id = int(body.get("user_id"))
        samples = get_enrollment_samples(user_id)
        if len(samples) < settings.MIN_ENROLLMENT:
            return jsonify(error=f"Potrebných je aspoň {settings.MIN_ENROLLMENT} vzoriek."), 400
        template = make_template(vectors_of(samples))
        save_template(user_id, template)
        stamp_template(user_id, 0)
        session.pop("enroll_user_id", None)
        return jsonify(ok=True, count=len(samples), threshold=template["threshold"], metric=settings.METRIC)
    except (ValueError, TypeError) as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/verification/begin")
def verification_begin():
    try:
        require_csrf()
        body = json_body()
        username = validate_username(body.get("username", ""))
        user = get_user_by_username(username)
        verify_rate_limit(username)
        if not user or not user["template_json"]:
            # Rovnaká odpoveď pre neexistujúci účet aj účet bez šablóny: žiadna enumerácia.
            # Falošný challenge sa pri overení správa ako expirovaný.
            return jsonify(challenge=secrets.token_urlsafe(32), challenge_id=0, phrase=next_phrase()[0])
        token, challenge_id, phrase = make_challenge(int(user["id"]), "verification")
        return jsonify(challenge=token, challenge_id=challenge_id, phrase=phrase)
    except PermissionError as exc:
        return jsonify(error=str(exc)), 429
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/verification")
def verification():
    try:
        require_csrf()
        body = json_body()
        username = validate_username(body.get("username", ""))
        password = str(body.get("password", ""))
        user = get_user_by_username(username)
        if not user:
            # Neodhaľujeme, či účet existuje, ale auditujeme pokus s user_id=NULL.
            store_attempt(
                user_id=None, username=username, actor=username, attempt_type="genuine",
                impersonated=None, score=None, threshold=None, accepted=False, features=None,
                challenge_id=None, reason="unknown_user",
            )
            return jsonify(error="Autentifikácia zlyhala."), 401
        user_id = int(user["id"])
        verify_rate_limit(username)

        attempt_type, actor, impersonated, test_mode = attempt_metadata(body, username)
        token = str(body.get("challenge", ""))
        challenge = consume_challenge(token, user_id, "verification")
        if not challenge:
            return jsonify(error="Challenge je neplatný, použitý alebo expirovaný."), 400

        # Heslo je prvý faktor. Biometria je druhý faktor.
        password_ok = check_password_hash(user["password_hash"], password)
        if is_locked(user):
            # Zamknutie odhalíme len tomu, kto pozná heslo; ostatní dostanú generickú chybu.
            store_attempt(
                user_id=user_id, username=username, actor=actor, attempt_type=attempt_type,
                impersonated=impersonated, score=None, threshold=None, accepted=False,
                features=None, challenge_id=int(challenge["id"]), reason="account_locked",
            )
            if password_ok:
                return jsonify(error="Účet je dočasne zamknutý."), 423
            return jsonify(error="Autentifikácia zlyhala."), 401
        if not password_ok:
            store_attempt(
                user_id=user_id, username=username, actor=actor, attempt_type=attempt_type,
                impersonated=impersonated, score=None, threshold=None, accepted=False,
                features=None, challenge_id=int(challenge["id"]), reason="password_failed",
            )
            if not test_mode or settings.ENFORCE_LOCKOUT_IN_TEST_MODE:
                update_auth_state(user_id, False, settings.LOCKOUT_THRESHOLD, settings.LOCKOUT_SECONDS)
            return jsonify(error="Autentifikácia zlyhala."), 401

        phrase = challenge["phrase"]
        features = extract_features(body.get("events", []), phrase, settings.MAX_CORRECTIONS)
        digest = feature_hash(features)
        if duplicate_attempt_exists(user_id, digest):
            store_attempt(
                user_id=user_id, username=username, actor=actor, attempt_type=attempt_type,
                impersonated=impersonated, score=None, threshold=None, accepted=False,
                features=features, challenge_id=int(challenge["id"]), replay=True,
                reason="duplicate_feature_vector", phrase=phrase,
            )
            if not test_mode or settings.ENFORCE_LOCKOUT_IN_TEST_MODE:
                update_auth_state(user_id, False, settings.LOCKOUT_THRESHOLD, settings.LOCKOUT_SECONDS)
            return jsonify(result="ZAMIETNUTÝ", error="Detegovaný replay / identický vektor."), 401

        if not user["template_json"]:
            return jsonify(error="Autentifikácia zlyhala."), 401
        template = current_template(user)
        metric = settings.METRIC
        threshold = float(template["threshold"])
        score = score_vector(features, template, metric=metric)
        if not math.isfinite(score):
            store_attempt(
                user_id=user_id, username=username, actor=actor, attempt_type=attempt_type,
                impersonated=impersonated, score=None, threshold=None, accepted=False,
                features=features, challenge_id=int(challenge["id"]), reason="insufficient_features",
                phrase=phrase,
            )
            return jsonify(error="Veta obsahovala príliš málo použiteľných kláves. Skús znova s novou vetou."), 400

        accepted = score <= threshold
        store_attempt(
            user_id=user_id, username=username, actor=actor, attempt_type=attempt_type,
            impersonated=impersonated, score=score, threshold=threshold, accepted=accepted,
            features=features, challenge_id=int(challenge["id"]), reason=None, phrase=phrase,
        )

        if not test_mode or settings.ENFORCE_LOCKOUT_IN_TEST_MODE:
            update_auth_state(user_id, accepted, settings.LOCKOUT_THRESHOLD, settings.LOCKOUT_SECONDS)

        # Adaptácia len pri genuine úspechu; test/impostor výsledok šablónu nemení.
        if accepted and settings.ADAPTIVE_TEMPLATE and attempt_type == "genuine" and actor == username:
            alpha = settings.ADAPTIVE_ALPHA
            center = template["center"]
            # príznak, ktorý vo vete nebol (alebo ho šablóna nepozná), sa nemení
            template["center"] = [
                c if c is None or x is None else (1 - alpha) * float(c) + alpha * float(x)
                for c, x in zip(center, features)
            ] + center[len(features):]
            save_template(user_id, template)

        if accepted:
            session["auth_user_id"] = user_id
            session["auth_username"] = username
        return jsonify(
            result="AUTENTIFIKOVANÝ" if accepted else "ZAMIETNUTÝ",
            accepted=accepted,
            score=round(score, 6),
            threshold=round(threshold, 6),
            metric=metric,
            test_mode=test_mode,
            refresh=refresh_info(get_user(user_id)) if accepted else None,
        ), (200 if accepted else 401)
    except PermissionError as exc:
        return jsonify(error=str(exc)), 429
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return jsonify(error=str(exc)), 400


@app.get("/api/admin/overview")
def admin_overview():
    if "auth_user_id" not in session:
        return jsonify(error="Neautorizované"), 401
    current = get_user(int(session["auth_user_id"]))
    if not current:
        session.clear()
        return jsonify(error="Relácia skončila"), 401
    users = []
    for row in list_users():
        item = dict(row)
        item["refresh"] = refresh_info(get_user(int(row["id"])))
        users.append(item)
    attempts = [dict(row) for row in list_attempts(100)]
    return jsonify(
        current_user=current["username"],
        metric=settings.METRIC,
        k=settings.K,
        lockout_threshold=settings.LOCKOUT_THRESHOLD,
        lockout_seconds=settings.LOCKOUT_SECONDS,
        rate_limit=f"{settings.RATE_LIMIT_MAX_ATTEMPTS}/{settings.RATE_LIMIT_WINDOW_SECONDS}s",
        users=users,
        attempts=attempts,
        history=[dict(row) for row in list_history()],
        refresh_days=settings.TEMPLATE_REFRESH_DAYS,
        test_mode_allowed=settings.ALLOW_TEST_MODE,
    )


# ---------- mesačná obnova šablóny ----------

@app.get("/api/refresh/status")
def refresh_status():
    try:
        return jsonify(refresh_info(session_user()))
    except PermissionError as exc:
        return jsonify(error=str(exc)), 401


@app.post("/api/refresh/begin")
def refresh_begin():
    try:
        require_csrf()
        user = session_user()
        info = refresh_info(user)
        token, challenge_id, phrase = make_challenge(int(user["id"]), "refresh")
        return jsonify(challenge=token, challenge_id=challenge_id, phrase=phrase,
                       count=info["pending"], needed=info["needed"], due=info["due"])
    except PermissionError as exc:
        return jsonify(error=str(exc)), 401
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/refresh/sample")
def refresh_sample():
    try:
        require_csrf()
        user = session_user()
        user_id = int(user["id"])
        body = json_body()
        challenge = consume_challenge(str(body.get("challenge", "")), user_id, "refresh")
        if not challenge:
            raise ValueError("Challenge je neplatný alebo expirovaný")
        features = extract_features(body.get("events", []), challenge["phrase"], settings.MAX_CORRECTIONS)
        digest = feature_hash(features)
        cycle = int(user["template_cycle"] or 0) + 1
        known = [r["feature_hash"] for r in get_enrollment_samples(user_id)] + \
                [r["feature_hash"] for r in get_refresh_samples(user_id, cycle)]
        if digest in known:
            return jsonify(error="Identická vzorka bola už použitá. Zopakuj ju."), 409

        template = current_template(user)
        score = score_vector(features, template)
        threshold = float(template["threshold"])
        # ochrana pred otrávením šablóny: vzorka musí byť rozumne blízko aktuálnej šablóne
        if not math.isfinite(score) or score > threshold * settings.REFRESH_MAX_RATIO:
            return jsonify(
                error="Táto vzorka sa príliš líši od tvojej šablóny, preto sa nepoužila. Skús ju napísať znova.",
                rejected=True,
            ), 422
        add_refresh_sample(user_id, cycle, features, challenge["phrase"], score)
        count = len(get_refresh_samples(user_id, cycle))
        return jsonify(count=count, needed=settings.REFRESH_SAMPLES, ready=count >= settings.REFRESH_SAMPLES)
    except PermissionError as exc:
        return jsonify(error=str(exc)), 401
    except (ValueError, TypeError) as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/refresh/finish")
def refresh_finish():
    try:
        require_csrf()
        user = session_user()
        user_id = int(user["id"])
        cycle = int(user["template_cycle"] or 0) + 1
        pending = get_refresh_samples(user_id, cycle)
        if len(pending) < settings.REFRESH_SAMPLES:
            raise ValueError(f"Potrebných je aspoň {settings.REFRESH_SAMPLES} vzoriek.")

        old_template = current_template(user)
        existing = get_enrollment_samples(user_id)
        old_window = vectors_of(existing[-settings.MAX_ENROLLMENT:])
        new_vectors = vectors_of(pending)

        report = analyze_drift(old_window, new_vectors, old_template)

        # nové vzorky sa pridajú k tým pôvodným; šablóna sa počíta z posledných MAX_ENROLLMENT
        next_index = (int(existing[-1]["sample_index"]) if existing else 0) + 1
        for offset, (row, vector) in enumerate(zip(pending, new_vectors)):
            add_enrollment_sample(user_id, next_index + offset, vector, row["phrase"], cycle)
        new_template = make_template((old_window + new_vectors)[-settings.MAX_ENROLLMENT:])
        old_threshold = float(old_template["threshold"])
        threshold, capped = decide_threshold(
            old_threshold, float(new_template["threshold"]),
            settings.THRESHOLD_MAX_GROWTH, settings.THRESHOLD_MAX_SHRINK,
        )
        new_template["threshold"] = threshold
        new_template["threshold_capped"] = capped
        save_template(user_id, new_template)
        stamp_template(user_id, cycle)
        clear_refresh_samples(user_id)

        report.update(
            cycle=cycle, old_threshold=old_threshold, new_threshold=threshold, threshold_capped=capped,
            new_samples=len(pending), needs_reenrollment=bool(capped and report["verdict"] == "degraded"),
        )
        insert_history({**report, "user_id": user_id, "threshold_capped": 1 if capped else 0,
                        "created_at": datetime.now(timezone.utc).isoformat()})
        return jsonify(ok=True, **report)
    except PermissionError as exc:
        return jsonify(error=str(exc)), 401
    except (ValueError, TypeError) as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/admin/age-template")
def admin_age_template():
    """Len v test mode: simuluje ubehnutie času, aby sa dala predviesť mesačná obnova."""
    if not settings.ALLOW_TEST_MODE:
        return jsonify(error="Dostupné len v test mode."), 403
    if "auth_user_id" not in session:
        return jsonify(error="Neautorizované"), 401
    try:
        require_csrf()
        body = json_body()
        age_template(int(body["user_id"]), int(body.get("days", settings.TEMPLATE_REFRESH_DAYS)))
        return jsonify(ok=True)
    except (ValueError, TypeError, KeyError) as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/logout")
def logout():
    try:
        require_csrf()
    except ValueError:
        return jsonify(error="Neplatný CSRF token"), 400
    session.clear()
    return jsonify(ok=True)


@app.get("/api/health")
def health():
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
