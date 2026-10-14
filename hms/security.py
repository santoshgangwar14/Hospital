import base64, hashlib, hmac, json, os, time

SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me").encode()


def hash_pw(p):
    salt = os.urandom(16)
    return "s1$%s$%s" % (salt.hex(), hashlib.scrypt(p.encode(), salt=salt, n=2**14, r=8, p=1).hex())


def check_pw(p, stored):
    try:
        _, s, h = stored.split("$")
        return hmac.compare_digest(hashlib.scrypt(p.encode(), salt=bytes.fromhex(s), n=2**14, r=8, p=1).hex(), h)
    except Exception:
        return False


def _b(x):
    return base64.urlsafe_b64encode(x).rstrip(b"=").decode()


def _ub(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(data, ttl=12 * 3600):
    body = _b(json.dumps({**data, "exp": int(time.time()) + ttl}).encode())
    return body + "." + _b(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())


def verify(tok):
    try:
        body, sig = tok.split(".")
        if not hmac.compare_digest(sig, _b(hmac.new(SECRET, body.encode(), hashlib.sha256).digest())):
            return None
        d = json.loads(_ub(body))
        return d if d["exp"] > time.time() else None
    except Exception:
        return None


PERMS = {
    "admin": {"*"},
    "receptionist": {"patients:r", "patients:w", "appointments:r", "appointments:w", "billing:r", "billing:w", "tariff:r",
                     "doctors:r", "dashboard:r", "reports:r", "ipd:r", "ipd:w", "ipd_bill:r"},
    "doctor": {"patients:r", "appointments:r", "appointments:w", "prescriptions:r", "prescriptions:w", "doctors:r", "dashboard:r",
               "emr:r", "emr:w", "ipd:r", "ipd:w", "orders:w", "discharge:w"},
    "nurse": {"patients:r", "doctors:r", "dashboard:r", "emr:r", "ipd:r", "vitals:w", "nursing:w", "mar:w", "clearance:nursing"},
    "billing": {"patients:r", "appointments:r", "billing:r", "billing:w", "tariff:r", "doctors:r", "dashboard:r", "reports:r",
                "ipd:r", "ipd_bill:r", "ipd_bill:w"},
}


def has(role, perm):
    p = PERMS.get(role, set())
    return "*" in p or perm in p
