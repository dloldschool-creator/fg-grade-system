import os

from dotenv import load_dotenv
from sqlalchemy import URL, create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

# Connection pool sizing. The app is expected to serve ~40 teachers, and
# Streamlit runs each user's session in its own thread, so concurrent
# requests all compete for connections from this one pool.
#
# The ceiling that matters is Postgres', not ours: the Supabase instance
# reports max_connections = 60, and other clients (the SQL editor, any
# background job) use some of that. 20 + 10 keeps a comfortable margin
# while being far more than the previous 5 + 10, which would have queued
# requests as soon as a dozen teachers were active at once.
POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "20"))
MAX_OVERFLOW = int(os.environ.get("DB_MAX_OVERFLOW", "10"))

# Supabase closes idle connections server-side. Without pre_ping, the
# first query on a connection that was closed underneath us raises
# instead of transparently reconnecting — which surfaces as random
# errors in a long-running app rather than at a predictable moment.
# recycle discards connections before they get old enough for that to be
# likely in the first place.
POOL_RECYCLE_SECONDS = int(os.environ.get("DB_POOL_RECYCLE", "1800"))

# How long a request waits for a free connection before giving up. The
# default is 30s, which just converts a capacity problem into a very slow
# page; failing sooner makes the cause obvious.
POOL_TIMEOUT_SECONDS = int(os.environ.get("DB_POOL_TIMEOUT", "10"))


def build_database_url() -> URL:
    # Built from parts (not a hand-assembled string) so special characters
    # in the password — @, :, /, etc. — never need manual percent-encoding
    # and can't be misparsed as URL delimiters.
    return URL.create(
        drivername="postgresql+psycopg",
        username=os.environ.get("DB_USER", "postgres"),
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", "5432")),
        database=os.environ.get("DB_NAME", "postgres"),
    )


engine = create_engine(
    build_database_url(),
    pool_size=POOL_SIZE,
    max_overflow=MAX_OVERFLOW,
    pool_pre_ping=True,
    pool_recycle=POOL_RECYCLE_SECONDS,
    pool_timeout=POOL_TIMEOUT_SECONDS,
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
