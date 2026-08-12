"""Which commit is actually running, and how long it has been up.

Exists to answer one recurring question after a `git push`: *did the
deploy land?* Without this the only way to tell is to hunt for a change
in the UI, which is slow and mistakes a browser cache for a failed
deploy.

**Reads git's files directly rather than shelling out.** Nothing under
`app/` may import `subprocess` — that is what keeps the deployment to
"pip install and run", and there is a test enforcing it. Git's refs are
plain text, so a couple of file reads is enough.

Degrades quietly: a deployment without a `.git` directory shows
"unknown" rather than failing. A version line is never worth an error
page.
"""

import os
import time
from datetime import timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GIT = os.path.join(_ROOT, ".git")

# Module import happens once per process, so this is the moment the app
# started — which is what actually changes on a redeploy.
_STARTED_AT = time.time()

UNKNOWN = "unknown"


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return None


def commit_sha() -> str:
    """The full SHA of the checked-out commit, or "unknown".

    An explicit APP_VERSION wins, so a host that deploys without a `.git`
    directory can still report something useful.
    """
    override = os.environ.get("APP_VERSION")
    if override:
        return override.strip()

    head = _read(os.path.join(_GIT, "HEAD"))
    if head is None:
        return UNKNOWN

    if not head.startswith("ref:"):
        return head  # detached HEAD holds the SHA directly

    ref = head.split(" ", 1)[1].strip()
    direct = _read(os.path.join(_GIT, *ref.split("/")))
    if direct:
        return direct

    # A ref that has been packed lives in packed-refs instead of its own
    # file — normal on a fresh clone, which is exactly what a host does.
    packed = _read(os.path.join(_GIT, "packed-refs"))
    for line in (packed or "").splitlines():
        if line.startswith("#") or " " not in line:
            continue
        sha, name = line.split(" ", 1)
        if name.strip() == ref:
            return sha
    return UNKNOWN


def short_sha() -> str:
    sha = commit_sha()
    return sha if sha == UNKNOWN else sha[:7]


def uptime() -> str:
    """How long this process has been running, coarsely.

    Deliberately relative rather than a timestamp: the server runs in UTC
    and the school does not, and "up 4m" answers "did it just restart?"
    without anyone doing the arithmetic.
    """
    seconds = int(time.time() - _STARTED_AT)
    if seconds < 60:
        return "up %ds" % seconds
    delta = timedelta(seconds=seconds)
    days, rest = delta.days, delta.seconds
    hours, minutes = rest // 3600, (rest % 3600) // 60
    if days:
        return "up %dd %dh" % (days, hours)
    if hours:
        return "up %dh %dm" % (hours, minutes)
    return "up %dm" % minutes


def version_line() -> str:
    return "%s · %s" % (short_sha(), uptime())
