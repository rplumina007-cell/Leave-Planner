"""Password hashing, session tokens and login throttling. Standard library only."""
import base64
import hashlib
import hmac
import re
import secrets
import time

# scrypt cost. OWASP lists N=2^16, r=8, p=2 as equal in strength to its N=2^17
# baseline at half the memory (64 MiB), which suits small serverless instances:
# a fraction of a second per check - negligible for a sign-in, very expensive
# for anyone guessing against a stolen database. Each stored hash records its
# own settings, so older, weaker hashes still verify and are upgraded at the
# next successful sign-in (see needs_rehash).
N, R, P, DKLEN = 2 ** 16, 8, 2, 64
MAXMEM = 256 * 1024 * 1024          # hashlib's 32 MiB default is too small for N=2^16

SESSION_HOURS = 12
MIN_PASSWORD = 10

# What most guessed passwords reduce to once digits and symbols are stripped
# ("Password2026!", "Welcome@123"). Deliberately short: it stops the obvious,
# and the length rule does the rest.
COMMON_WORDS = {
    "password", "passwort", "pass", "passw", "welcome", "letmein", "qwerty", "qwertyuiop", "asdf",
    "asdfgh", "asdfghjkl", "zxcvbnm", "admin", "administrator", "login", "changeme", "default",
    "secret", "master", "monkey", "dragon", "sunshine", "iloveyou", "football", "baseball",
    "princess", "superman", "trustno", "abc", "abcd", "abcdef", "test", "guest", "user", "hello",
    "summer", "winter", "spring", "autumn", "leave", "leaveplanner", "planner", "wfh", "office",
}
DIGIT_RUNS = "01234567890123456789" + "98765432109876543210"


def hash_password(password, n=N, r=R, p=P):
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=DKLEN, maxmem=MAXMEM)
    return "scrypt$%d$%d$%d$%s$%s" % (n, r, p, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password, stored):
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        want = base64.b64decode(dk)
        got = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt),
                             n=int(n), r=int(r), p=int(p), dklen=len(want), maxmem=MAXMEM)
        return hmac.compare_digest(got, want)
    except (ValueError, TypeError):
        return False


def needs_rehash(stored):
    """True for a hash made with different (older, weaker) settings than today's."""
    try:
        algo, n, r, p = stored.split("$")[:4]
        return algo != "scrypt" or (int(n), int(r), int(p)) != (N, R, P)
    except (ValueError, AttributeError):
        return True


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
    core = re.sub(r"[^a-z]", "", low)
    if core in COMMON_WORDS or (not core and low.isdigit() and low in DIGIT_RUNS):
        return "That password is too common - choose something harder to guess"
    return None


def new_token():
    return secrets.token_urlsafe(32)


def token_hash(token):
    """Only the hash is stored, so a copied database holds no usable sessions."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class LoginLimiter:
    """Failed attempts are counted in the database, so every server instance
    shares one count (Vercel runs several at once), over a 15-minute window.
    Sign-in failures are counted three ways, each with its own limit:

      pair  one employee ID from one address     5   guessing at one account
      ip    one address, whatever the ID        20   spraying many accounts
      id    one employee ID, from any address   50   a distributed attack on one account

    The tight limit is per ID *and* address, so someone who knows a colleague's
    ID can't lock them out with a few wrong passwords from their own device."""
    WINDOW = 15 * 60
    LIMITS = {"pair": 5, "ip": 20, "id": 50}
    DEFAULT = 5                      # any other key, e.g. "pw:<id>" for password changes

    def __init__(self, store):
        self.store = store

    @staticmethod
    def login_keys(emp_id, ip):
        emp = emp_id.lower()
        return ("pair:%s|%s" % (emp, ip), "ip:" + ip, "id:" + emp)

    def limit(self, key):
        return self.LIMITS.get(key.split(":", 1)[0], self.DEFAULT)

    def blocked(self, *keys):
        since = time.time() - self.WINDOW
        return any(self.store.failure_count(k, since) >= self.limit(k) for k in keys)

    def fail(self, *keys):
        now = time.time()
        self.store.add_failures(keys, now, prune_before=now - self.WINDOW)

    def clear(self, *keys):
        self.store.clear_failures(keys)
