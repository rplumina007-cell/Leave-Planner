"""Password hashing, session tokens and login throttling. Standard library only."""
import base64
import hashlib
import hmac
import secrets
import time

# scrypt cost: ~50ms per check on a laptop - negligible for a login, expensive
# for anyone trying guesses against a stolen database.
N, R, P, DKLEN = 2 ** 14, 8, 1, 64

SESSION_HOURS = 12
MIN_PASSWORD = 8


def hash_password(password):
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=N, r=R, p=P, dklen=DKLEN)
    return "scrypt$%d$%d$%d$%s$%s" % (N, R, P, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password, stored):
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        want = base64.b64decode(dk)
        got = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt),
                             n=int(n), r=int(r), p=int(p), dklen=len(want))
        return hmac.compare_digest(got, want)
    except (ValueError, TypeError):
        return False


# A real hash to check against when the employee ID doesn't exist, so a wrong
# ID and a wrong password take the same time and can't be told apart.
_DUMMY = hash_password(secrets.token_hex(16))


def verify_or_dummy(password, stored):
    return verify_password(password, stored or _DUMMY) and bool(stored)


def password_problem(password, emp_id="", name=""):
    """Return a user-facing reason the password is unacceptable, or None."""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        return "Password must be at least %d characters" % MIN_PASSWORD
    if len(password) > 200:
        return "Password is too long"
    low = password.lower()
    if emp_id and emp_id.lower() in low:
        return "Password must not contain the employee ID"
    if name and any(part and len(part) > 2 and part.lower() in low for part in name.split()):
        return "Password must not contain your name"
    if len(set(password)) < 4:
        return "Password is too repetitive"
    return None


def new_token():
    return secrets.token_urlsafe(32)


def token_hash(token):
    """Only the hash is stored, so a copied database holds no usable sessions."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class LoginLimiter:
    """After 5 failures within 15 minutes, lock that employee ID and that address
    for 15 minutes. Failures are stored in the database so every server instance
    shares one count - Vercel runs several at once."""
    MAX, WINDOW = 5, 15 * 60

    def __init__(self, store):
        self.store = store

    def blocked(self, *keys):
        since = time.time() - self.WINDOW
        return any(self.store.failure_count(k, since) >= self.MAX for k in keys)

    def fail(self, *keys):
        now = time.time()
        self.store.add_failures(keys, now, prune_before=now - self.WINDOW)

    def clear(self, *keys):
        self.store.clear_failures(keys)
