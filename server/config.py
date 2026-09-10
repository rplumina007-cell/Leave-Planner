"""Where things live. Shared by the server and the admin CLI."""
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED_PATH = os.path.join(PROJECT_ROOT, "data", "seed.json")


def default_db_path():
    """Outside the project folder on purpose: the project sits in OneDrive, and
    a live SQLite file being synced mid-write can corrupt, plus the accounts
    database shouldn't travel wherever the source code is shared."""
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    folder = os.path.join(base, "WFSTracker")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "wfs.sqlite3")
