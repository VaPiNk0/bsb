from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import math
import re
import secrets
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .config import settings
from .db import (
    DEVICES,
    IntegrityError,
    add_enrollment_sample,
    add_refresh_sample,
    age_template,
    clear_refresh_samples,
    consume_challenge,
    create_challenge,
    create_user,
    duplicate_attempt_exists,
    get_enrollment_samples,
    get_template,
    get_templates,
    get_user,
    get_user_by_username,
    get_refresh_samples,
    init_db,
    insert_attempt,
    insert_history,
    list_history,
    stamp_template,
    list_attempts,
    list_enrollment_features,
    list_report_attempts,
    list_templates,
    list_users,
    next_sample_index,
    recent_attempt_count,
    save_template,
    feature_hash,
    update_auth_state,
)
from .drift import analyze_drift, decide_threshold
from .features import FEATURE_COUNT, extract_features
from .report import ATTEMPT_COLUMNS, SCENARIO_COLUMNS, USER_COLUMNS, build_report
from .scoring import TEMPLATE_VERSION, build_template, score_vector
from .i18n import to_english
from .sentences import DEFAULT_LANGUAGE, LANGUAGES, next_phrase


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
ATTEMPT_TYPES = {"genuine", "impostor", "imitacia"}
PAIR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # bez 0/O a 1/I, aby sa kód dal bez chyby opísať
PAIR_CODE_LENGTH = 8
PAIR_TTL_SECONDS = 600

app = Flask(__name__, static_folder=None)  # vlastná /static cesta nižšie obsluhuje frontend/
app.secret_key = settings.SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=settings.COOKIE_SECURE,
    MAX_CONTENT_LENGTH=64 * 1024,
)

init_db()


TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


class HumanCheckFailed(Exception):
    pass


def via_trusted_proxy() -> bool:
    supplied = request.headers.get("X-Proxy-Secret", "")
    return bool(settings.PROXY_SECRET) and secrets.compare_digest(supplied, settings.PROXY_SECRET)


def client_ip() -> str:
    # Hlavičkám o pôvodnej IP veríme len od vlastnej proxy (preukáže sa tajomstvom); inak by si
    # ich klient nastavil sám a obišiel limit pokusov.
    if via_trusted_proxy():
        return request.headers.get("X-Client-IP") or request.remote_addr or "unknown"
    return request.remote_addr or "unknown"


def turnstile_ok(token: str) -> bool:
    data = urllib.parse.urlencode(
        {"secret": settings.TURNSTILE_SECRET, "response": token, "remoteip": client_ip()}
    ).encode()
    try:
        with urllib.request.urlopen(TURNSTILE_VERIFY_URL, data=data, timeout=8) as response:
            return bool(json.load(response).get("success"))
    except (OSError, ValueError):
        return False   # keď sa overenie nedá urobiť, nepúšťame


def require_human(body: dict) -> None:
    """Kontrola proti botom: token z Turnstile sa overí raz a relácia si to chvíľu pamätá,
    takže ďalšie vety tej istej registrácie či prihlásenia už widget nepotrebujú."""
    if not settings.TURNSTILE_SECRET or session.get("human_until", 0) > time.time():
        return
    token = str(body.get("turnstile") or "")
    if not token or len(token) > 4096 or not turnstile_ok(token):
        raise HumanCheckFailed("Overenie, že nie si robot, zlyhalo. Skús to znova.")
    session["human_until"] = time.time() + settings.HUMAN_CHECK_TTL_SECONDS


def request_lang() -> str:
    """Jazyk rozhrania, ktorý si klient vypýtal hlavičkou X-Lang."""
    lang = request.headers.get("X-Lang", "")
    return lang if lang in LANGUAGES else DEFAULT_LANGUAGE


def phrase_lang(user) -> str:
    """Jazyk viet je viazaný na účet: šablóna vznikla z viet v jednom jazyku a v inom jazyku
    píše človek inak, takže prihlásenie aj obnova dostanú vety v jazyku registrácie."""
    return user["phrase_lang"] or DEFAULT_LANGUAGE


def decoy_lang(username: str) -> str:
    """Jazyk falošnej vety pre neexistujúci účet. Nesmie závisieť od hlavičky X-Lang: skutočný
    účet má jazyk pevný, takže podľa toho, či veta "poslúcha" hlavičku, by sa dalo zistiť, či
    účet existuje. Odvodí sa preto z mena (stále rovnako pre to isté meno); väčšina vychádza
    v predvolenom jazyku, tak ako skutočné účty."""
    digest = hmac.new(app.secret_key.encode(), username.lower().encode(), hashlib.sha256).digest()
    return DEFAULT_LANGUAGE if digest[0] % 4 else next(lang for lang in LANGUAGES if lang != DEFAULT_LANGUAGE)


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


def request_device(body: dict) -> str:
    """Typ zariadenia, z ktorého klient píše ("pc" alebo "mobile"). Každý typ má vlastnú šablónu.
    Údaj pochádza od klienta; podvrhnutím si útočník vyberie len to, s ktorou šablónou účtu ho porovnáme."""
    device = str(body.get("device") or "pc")
    if device not in DEVICES:
        raise ValueError("Neznámy typ zariadenia")
    return device


def refresh_info(tpl) -> dict:
    """Stav mesačnej obnovy jednej šablóny (riadok z tabuľky templates)."""
    updated = datetime.fromisoformat(tpl["updated_at"])
    due_at = updated + timedelta(days=settings.TEMPLATE_REFRESH_DAYS)
    now = datetime.now(timezone.utc)
    cycle = int(tpl["cycle"] or 0)
    return {
        "device": tpl["device"],
        "due": now >= due_at,
        "days_since": max(0, (now - updated).days),
        "interval_days": settings.TEMPLATE_REFRESH_DAYS,
        "due_in_days": (due_at - now).days,
        "cycle": cycle,
        "pending": len(get_refresh_samples(int(tpl["user_id"]), cycle + 1, tpl["device"])),
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


def current_template(tpl) -> dict:
    """Šablóna účtu pre jedno zariadenie; ak ju vytvorila iná konfigurácia alebo staršia verzia
    skórovania, prepočíta sa z uložených vzoriek toho zariadenia (posledných MAX_ENROLLMENT)."""
    template = json.loads(tpl["template_json"])
    if (
        template.get("version") == TEMPLATE_VERSION
        and template["metric"] == settings.METRIC
        and float(template["k"]) == settings.K
    ):
        return template
    user_id, device = int(tpl["user_id"]), tpl["device"]
    template = make_template(vectors_of(get_enrollment_samples(user_id, device)[-settings.MAX_ENROLLMENT:]))
    save_template(user_id, device, template)
    return template


def session_user():
    """Prihlásený používateľ a šablóna zariadenia, z ktorého sa prihlásil."""
    if "auth_user_id" not in session:
        raise PermissionError("Najprv sa prihlás.")
    user = get_user(int(session["auth_user_id"]))
    tpl = get_template(int(user["id"]), session.get("auth_device", "pc")) if user else None
    if not tpl:
        raise PermissionError("Najprv sa prihlás.")
    return user, tpl


def pair_code_digits(code: str) -> str:
    """Kód tak, ako ho používateľ opísal: bez medzier a pomlčiek, veľkými písmenami."""
    return re.sub(r"[\s-]", "", str(code or "")).upper()[:32]


def is_admin(username: str) -> bool:
    return not settings.ADMIN_USERS or username in settings.ADMIN_USERS


def session_admin():
    """Prihlásený používateľ s prístupom do admin panela, inak None."""
    if "auth_user_id" not in session:
        return None
    user = get_user(int(session["auth_user_id"]))
    return user if user and is_admin(user["username"]) else None


def verify_rate_limit(username: str) -> None:
    count = recent_attempt_count(client_ip(), username, settings.RATE_LIMIT_WINDOW_SECONDS)
    if count >= settings.RATE_LIMIT_MAX_ATTEMPTS:
        raise PermissionError("Príliš veľa pokusov, chvíľu počkaj.")


def make_challenge(user_id: int, purpose: str, lang: str, device: str) -> tuple[str, int, str]:
    """Každý challenge nesie vlastnú vetu a zariadenie; server si ich pamätá, klient ich nevie zmeniť."""
    token = secrets.token_urlsafe(32)
    phrase, _source = next_phrase(lang)
    challenge_id = create_challenge(user_id, purpose, token, settings.CHALLENGE_TTL_SECONDS, phrase, device)
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
    test_mode: bool = False, device: str | None = None,
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
            "test_mode": 1 if test_mode else 0,
            "device": device,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )


@app.before_request
def only_through_proxy():
    if settings.PROXY_SECRET and request.path != "/api/health" and not via_trusted_proxy():
        return jsonify(error="Neautorizované"), 403


@app.errorhandler(HumanCheckFailed)
def human_check_failed(exc):
    return jsonify(error=str(exc), captcha=True), 403


@app.errorhandler(500)
def server_error(exc):
    # Výnimku Flask už zalogoval; klient dostane čitateľnú hlášku namiesto HTML stránky.
    return jsonify(error="Na serveri nastala chyba. Skús to o chvíľu znova."), 500


@app.after_request
def translate_error(response):
    if request_lang() == "en" and response.is_json:
        data = response.get_json(silent=True)
        if isinstance(data, dict) and isinstance(data.get("error"), str):
            data["error"] = to_english(data["error"])
            response.set_data(json.dumps(data))
    return response


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"   # CSRF token ani výsledky sa nesmú brať z cache
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' https://challenges.cloudflare.com; style-src 'self'; "
        "connect-src 'self'; frame-src https://challenges.cloudflare.com; "
        "img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    return response


@app.get("/")
def index():
    csrf_token()
    return send_from_directory(FRONTEND, "index.html")


@app.get("/admin")
def admin_page():
    if not session_admin():
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
        turnstile_site_key=settings.TURNSTILE_SITE_KEY if settings.TURNSTILE_SECRET else "",
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
        device = request_device(body)
        require_human(body)

        user = get_user_by_username(username)
        if user:
            user_id = int(user["id"])
            templates = get_templates(user_id)
            if any(tpl["device"] == device for tpl in templates):
                return jsonify(error="Tento účet už má šablónu pre tento typ zariadenia."), 409
            # Heslo (a párovací kód) sa pri registrácii overuje raz; ďalšie vety tej istej relácie
            # už scrypt nepočítajú znova (inak ~0,3 s navyše pred každou vetou).
            if session.get("enroll_user_id") != user_id or session.get("enroll_device") != device:
                if not check_password_hash(user["password_hash"], password):
                    return jsonify(error="Používateľ existuje, ale heslo nesedí."), 409
                if templates:
                    # Účet už je chránený šablónou z iného zariadenia. Samotné heslo by stačilo na
                    # pridanie vlastnej šablóny a obišlo by druhý faktor; preto treba kód, ktorý
                    # vydá len zariadenie prihlásené aj biometriou.
                    code = pair_code_digits(body.get("pair_code"))
                    pair = consume_challenge(code, user_id, "pair") if code else None
                    if not pair or pair["device"] != device:
                        return jsonify(error="Na pridanie ďalšieho zariadenia treba platný párovací kód.", pair_required=True), 403
            lang = phrase_lang(user)
        else:
            lang = request_lang()
            try:
                user_id = create_user(username, generate_password_hash(password), lang)
            except IntegrityError:
                # dve súbežné požiadavky s tým istým menom (dvojité odoslanie formulára)
                return jsonify(error="Registrácia tohto mena práve prebieha. Skús to znova."), 409
        session["enroll_user_id"] = user_id
        session["enroll_device"] = device
        token, challenge_id, phrase = make_challenge(user_id, "enrollment", lang, device)
        count = len(get_enrollment_samples(user_id, device))
        return jsonify(
            user_id=user_id,
            device=device,
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
        device = challenge["device"] or "pc"
        features = extract_features(body.get("events", []), challenge["phrase"], settings.MAX_CORRECTIONS)
        digest = feature_hash(features)
        if any(row["feature_hash"] == digest for row in get_enrollment_samples(user_id)):
            return jsonify(error="Identická enrollment vzorka bola už použitá. Zopakuj ju."), 409
        existing = len(get_enrollment_samples(user_id, device))
        if existing >= settings.MAX_ENROLLMENT:
            return jsonify(error="Maximum enrollment vzoriek už bolo dosiahnuté."), 409
        add_enrollment_sample(user_id, next_sample_index(user_id), features, challenge["phrase"], device=device)
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
        device = request_device(body)
        # šablónu dokončí len relácia, ktorá registráciu začala (prešla heslom, prípadne párovacím kódom)
        if session.get("enroll_user_id") != user_id or session.get("enroll_device") != device:
            return jsonify(error="Registrácia nie je rozbehnutá. Začni ju znova."), 400
        if get_template(user_id, device):
            return jsonify(error="Tento účet už má šablónu pre tento typ zariadenia."), 409
        samples = get_enrollment_samples(user_id, device)
        if len(samples) < settings.MIN_ENROLLMENT:
            return jsonify(error=f"Potrebných je aspoň {settings.MIN_ENROLLMENT} vzoriek."), 400
        template = make_template(vectors_of(samples))
        save_template(user_id, device, template)
        session.pop("enroll_user_id", None)
        session.pop("enroll_device", None)
        return jsonify(ok=True, count=len(samples), device=device, threshold=template["threshold"], metric=settings.METRIC)
    except (ValueError, TypeError) as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/verification/begin")
def verification_begin():
    try:
        require_csrf()
        body = json_body()
        username = validate_username(body.get("username", ""))
        device = request_device(body)
        require_human(body)
        user = get_user_by_username(username)
        verify_rate_limit(username)
        if not user or not get_template(int(user["id"]), device):
            # Rovnaká odpoveď pre neexistujúci účet aj účet bez šablóny pre toto zariadenie: žiadna
            # enumerácia. Falošný challenge sa pri overení nepoužije (pokus skončí ako neúspešný).
            return jsonify(challenge=secrets.token_urlsafe(32), challenge_id=0, phrase=next_phrase(decoy_lang(username))[0])
        token, challenge_id, phrase = make_challenge(int(user["id"]), "verification", phrase_lang(user), device)
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
        device = request_device(body)
        user = get_user_by_username(username)
        tpl = get_template(int(user["id"]), device) if user else None
        if not tpl:
            # Neodhaľujeme, či účet existuje (ani či má šablónu pre toto zariadenie), ale pokus auditujeme.
            # Taký účet dostal vo verification/begin falošný challenge, takže by tu inak
            # donekonečna končil na "neplatný challenge" a klient by si pýtal ďalšiu vetu.
            store_attempt(
                user_id=int(user["id"]) if user else None, username=username, actor=username,
                attempt_type="genuine", impersonated=None, score=None, threshold=None, accepted=False,
                features=None, challenge_id=None, reason="not_enrolled" if user else "unknown_user", device=device,
            )
            return jsonify(error="Autentifikácia zlyhala."), 401
        user_id = int(user["id"])
        verify_rate_limit(username)

        attempt_type, actor, impersonated, test_mode = attempt_metadata(body, username)
        token = str(body.get("challenge", ""))
        challenge = consume_challenge(token, user_id, "verification")
        if not challenge or (challenge["device"] or "pc") != device:
            return jsonify(error="Challenge je neplatný, použitý alebo expirovaný."), 400

        # Heslo je prvý faktor. Biometria je druhý faktor.
        password_ok = check_password_hash(user["password_hash"], password)
        if is_locked(user):
            # Zamknutie odhalíme len tomu, kto pozná heslo; ostatní dostanú generickú chybu.
            store_attempt(
                user_id=user_id, username=username, actor=actor, attempt_type=attempt_type,
                impersonated=impersonated, test_mode=test_mode, score=None, threshold=None, accepted=False,
                features=None, challenge_id=int(challenge["id"]), reason="account_locked", device=device,
            )
            if password_ok:
                return jsonify(error="Účet je dočasne zamknutý."), 423
            return jsonify(error="Autentifikácia zlyhala."), 401
        if not password_ok:
            store_attempt(
                user_id=user_id, username=username, actor=actor, attempt_type=attempt_type,
                impersonated=impersonated, test_mode=test_mode, score=None, threshold=None, accepted=False,
                features=None, challenge_id=int(challenge["id"]), reason="password_failed", device=device,
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
                impersonated=impersonated, test_mode=test_mode, score=None, threshold=None, accepted=False,
                features=features, challenge_id=int(challenge["id"]), replay=True,
                reason="duplicate_feature_vector", phrase=phrase, device=device,
            )
            if not test_mode or settings.ENFORCE_LOCKOUT_IN_TEST_MODE:
                update_auth_state(user_id, False, settings.LOCKOUT_THRESHOLD, settings.LOCKOUT_SECONDS)
            return jsonify(result="ZAMIETNUTÝ", error="Detegovaný replay / identický vektor."), 401

        template = current_template(tpl)
        metric = settings.METRIC
        threshold = float(template["threshold"])
        score = score_vector(features, template, metric=metric)
        if not math.isfinite(score):
            store_attempt(
                user_id=user_id, username=username, actor=actor, attempt_type=attempt_type,
                impersonated=impersonated, test_mode=test_mode, score=None, threshold=None, accepted=False,
                features=features, challenge_id=int(challenge["id"]), reason="insufficient_features",
                phrase=phrase, device=device,
            )
            return jsonify(error="Veta obsahovala príliš málo použiteľných kláves. Skús znova s novou vetou."), 400

        accepted = score <= threshold
        store_attempt(
            user_id=user_id, username=username, actor=actor, attempt_type=attempt_type,
            impersonated=impersonated, test_mode=test_mode, score=score, threshold=threshold, accepted=accepted,
            features=features, challenge_id=int(challenge["id"]), reason=None, phrase=phrase, device=device,
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
            save_template(user_id, device, template)

        if accepted:
            session["auth_user_id"] = user_id
            session["auth_username"] = username
            session["auth_device"] = device
        devices = [row["device"] for row in get_templates(user_id)]
        return jsonify(
            result="AUTENTIFIKOVANÝ" if accepted else "ZAMIETNUTÝ",
            accepted=accepted,
            score=round(score, 6),
            threshold=round(threshold, 6),
            metric=metric,
            test_mode=test_mode,
            is_admin=accepted and is_admin(username),
            # overený používateľ je "pustený na internet"; admin a testovacie pokusy ostávajú v aplikácii
            redirect=settings.LOGIN_REDIRECT_URL if accepted and not test_mode and not is_admin(username) else None,
            refresh=refresh_info(get_template(user_id, device)) if accepted else None,
            device=device,
            # druhé zariadenie sa pridáva párovacím kódom, ktorý vydá len takto prihlásená relácia
            missing_devices=[d for d in DEVICES if d not in devices] if accepted else None,
        ), (200 if accepted else 401)
    except PermissionError as exc:
        return jsonify(error=str(exc)), 429
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return jsonify(error=str(exc)), 400


def verification_report(attempts, users) -> dict:
    thresholds = {
        (int(row["user_id"]), row["device"]): float(json.loads(row["template_json"])["threshold"])
        for row in list_templates()
    }
    return build_report(attempts, users, list_enrollment_features(), thresholds)


@app.get("/api/admin/overview")
def admin_overview():
    current = session_admin()
    if not current:
        return jsonify(error="Neautorizované"), 401 if "auth_user_id" not in session else 403
    users = []
    for row in list_users():
        item = dict(row)
        item["templates"] = [refresh_info(tpl) for tpl in get_templates(int(row["id"]))]
        item["enrolled"] = bool(item["templates"])
        users.append(item)
    attempts = [dict(row) for row in list_attempts(100)]
    return jsonify(
        report=verification_report(list_report_attempts(), users),
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


@app.get("/api/admin/report/<name>.csv")
def admin_report_csv(name: str):
    """Tabuľky výsledkov ako CSV do referátu (desatinná bodka, podiely v rozsahu 0–1)."""
    if not session_admin():
        return jsonify(error="Neautorizované"), 401 if "auth_user_id" not in session else 403
    attempts = list_report_attempts()
    if name == "attempts":
        columns, rows = ATTEMPT_COLUMNS, [dict(row) for row in attempts]
    elif name in ("scenarios", "users"):
        report = verification_report(attempts, list_users())
        columns, rows = (SCENARIO_COLUMNS, report["scenarios"]) if name == "scenarios" else (USER_COLUMNS, report["users"])
    else:
        return jsonify(error="Neznáma tabuľka"), 404
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    # BOM, aby Excel správne prečítal diakritiku v menách
    return Response(
        "﻿" + buffer.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="keystroke_{name}.csv"'},
    )


# ---------- mesačná obnova šablóny ----------

@app.get("/api/refresh/status")
def refresh_status():
    try:
        return jsonify(refresh_info(session_user()[1]))
    except PermissionError as exc:
        return jsonify(error=str(exc)), 401


@app.post("/api/refresh/begin")
def refresh_begin():
    try:
        require_csrf()
        user, tpl = session_user()
        info = refresh_info(tpl)
        token, challenge_id, phrase = make_challenge(int(user["id"]), "refresh", phrase_lang(user), tpl["device"])
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
        user, tpl = session_user()
        user_id, device = int(user["id"]), tpl["device"]
        body = json_body()
        challenge = consume_challenge(str(body.get("challenge", "")), user_id, "refresh")
        if not challenge or (challenge["device"] or "pc") != device:
            raise ValueError("Challenge je neplatný alebo expirovaný")
        features = extract_features(body.get("events", []), challenge["phrase"], settings.MAX_CORRECTIONS)
        digest = feature_hash(features)
        cycle = int(tpl["cycle"] or 0) + 1
        known = [r["feature_hash"] for r in get_enrollment_samples(user_id)] + \
                [r["feature_hash"] for r in get_refresh_samples(user_id, cycle, device)]
        if digest in known:
            return jsonify(error="Identická vzorka bola už použitá. Zopakuj ju."), 409

        template = current_template(tpl)
        score = score_vector(features, template)
        threshold = float(template["threshold"])
        # ochrana pred otrávením šablóny: vzorka musí byť rozumne blízko aktuálnej šablóne
        if not math.isfinite(score) or score > threshold * settings.REFRESH_MAX_RATIO:
            return jsonify(
                error="Táto vzorka sa príliš líši od tvojej šablóny, preto sa nepoužila. Skús ju napísať znova.",
                rejected=True,
            ), 422
        add_refresh_sample(user_id, cycle, features, challenge["phrase"], score, device)
        count = len(get_refresh_samples(user_id, cycle, device))
        return jsonify(count=count, needed=settings.REFRESH_SAMPLES, ready=count >= settings.REFRESH_SAMPLES)
    except PermissionError as exc:
        return jsonify(error=str(exc)), 401
    except (ValueError, TypeError) as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/refresh/finish")
def refresh_finish():
    try:
        require_csrf()
        user, tpl = session_user()
        user_id, device = int(user["id"]), tpl["device"]
        cycle = int(tpl["cycle"] or 0) + 1
        pending = get_refresh_samples(user_id, cycle, device)
        if len(pending) < settings.REFRESH_SAMPLES:
            raise ValueError(f"Potrebných je aspoň {settings.REFRESH_SAMPLES} vzoriek.")

        old_template = current_template(tpl)
        old_window = vectors_of(get_enrollment_samples(user_id, device)[-settings.MAX_ENROLLMENT:])
        new_vectors = vectors_of(pending)

        report = analyze_drift(old_window, new_vectors, old_template)

        # nové vzorky sa pridajú k tým pôvodným; šablóna sa počíta z posledných MAX_ENROLLMENT
        next_index = next_sample_index(user_id)
        for offset, (row, vector) in enumerate(zip(pending, new_vectors)):
            add_enrollment_sample(user_id, next_index + offset, vector, row["phrase"], cycle, device)
        new_template = make_template((old_window + new_vectors)[-settings.MAX_ENROLLMENT:])
        old_threshold = float(old_template["threshold"])
        threshold, capped = decide_threshold(
            old_threshold, float(new_template["threshold"]),
            settings.THRESHOLD_MAX_GROWTH, settings.THRESHOLD_MAX_SHRINK,
        )
        new_template["threshold"] = threshold
        new_template["threshold_capped"] = capped
        save_template(user_id, device, new_template)
        stamp_template(user_id, device, cycle)
        clear_refresh_samples(user_id, device)

        report.update(
            cycle=cycle, old_threshold=old_threshold, new_threshold=threshold, threshold_capped=capped,
            new_samples=len(pending), needs_reenrollment=bool(capped and report["verdict"] == "degraded"),
        )
        insert_history({**report, "user_id": user_id, "device": device, "threshold_capped": 1 if capped else 0,
                        "created_at": datetime.now(timezone.utc).isoformat()})
        return jsonify(ok=True, **report)
    except PermissionError as exc:
        return jsonify(error=str(exc)), 401
    except (ValueError, TypeError) as exc:
        return jsonify(error=str(exc)), 400


# ---------- druhé zariadenie ----------

@app.post("/api/devices/pair")
def devices_pair():
    """Jednorazový kód na pridanie šablóny pre druhý typ zariadenia. Vydá ho len relácia
    prihlásená heslom aj biometriou; na druhom zariadení sa zadá pri registrácii."""
    try:
        require_csrf()
        user, _tpl = session_user()
        user_id = int(user["id"])
        have = {row["device"] for row in get_templates(user_id)}
        body = json_body()
        missing = [d for d in DEVICES if d not in have]
        device = str(body.get("device") or (missing[0] if missing else ""))
        if device not in missing:
            return jsonify(error="Šablóna pre toto zariadenie už existuje."), 409
        code = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(PAIR_CODE_LENGTH))
        create_challenge(user_id, "pair", code, PAIR_TTL_SECONDS, None, device)
        half = PAIR_CODE_LENGTH // 2
        return jsonify(code=f"{code[:half]}-{code[half:]}", device=device, expires_in=PAIR_TTL_SECONDS)
    except PermissionError as exc:
        return jsonify(error=str(exc)), 401
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@app.post("/api/admin/age-template")
def admin_age_template():
    """Len v test mode: simuluje ubehnutie času, aby sa dala predviesť mesačná obnova."""
    if not settings.ALLOW_TEST_MODE:
        return jsonify(error="Dostupné len v test mode."), 403
    if not session_admin():
        return jsonify(error="Neautorizované"), 401 if "auth_user_id" not in session else 403
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
