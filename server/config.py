"""Where things live. Shared by the server, the admin CLI and the tools."""
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED_PATH = os.path.join(PROJECT_ROOT, "data", "seed.json")
ENV_FILES = (".env.local", ".env")       # git-ignored; `vercel env pull` writes .env.local


def app_folder():
    """Outside the project folder on purpose: the project sits in OneDrive, and
    a live SQLite file being synced mid-write can corrupt, plus the accounts
    database and the connection string shouldn't travel wherever the source
    code is shared, or into a cloud sync's history."""
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    folder = os.path.join(base, "WFSTracker")
    os.makedirs(folder, exist_ok=True)
    return folder


def default_db_path():
    return os.path.join(app_folder(), "wfs.sqlite3")


def env_file_paths():
    """Where the connection string may be kept, most private first: beside the
    local database, outside any synced folder - then the project folder, which
    is where `vercel env pull` writes it."""
    return ([os.path.join(app_folder(), name) for name in ENV_FILES]
            + [os.path.join(PROJECT_ROOT, name) for name in ENV_FILES])


def hosted_database_url():
    """The hosted (Neon) database URL, for tools run on your own computer:
    the DATABASE_URL / POSTGRES_URL environment variable, or the same key in a
    git-ignored .env.local / .env file - see env_file_paths() for where those
    are looked for. Returns None if not configured.

    The local server deliberately never calls this - running the app on your
    computer should not quietly start writing to the live database."""
    for key in ("DATABASE_URL", "POSTGRES_URL"):
        if os.environ.get(key):
            return os.environ[key]
    for path in env_file_paths():
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                key, sep, value = line.strip().partition("=")
                if sep and key.strip() in ("DATABASE_URL", "POSTGRES_URL") and value.strip():
                    return value.strip().strip('"').strip("'")
    return None
