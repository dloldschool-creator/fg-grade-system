import os

from dotenv import load_dotenv
from sqlalchemy import URL, create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()


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


engine = create_engine(build_database_url())
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
