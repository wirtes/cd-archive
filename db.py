import os
from pathlib import Path


DEFAULT_DATABASE_URL = "postgresql://radio1190:radio1190@127.0.0.1:5432/radio1190_archive"


def preload_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def database_url() -> str:
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    return DEFAULT_DATABASE_URL


class Row(dict):
    def __init__(self, columns, values):
        super().__init__(zip(columns, values))
        self._values = tuple(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


def translate_sql(sql: str) -> str:
    # The project only uses qmark placeholders for values, not literal question marks.
    return sql.replace("?", "%s")


class Cursor:
    def __init__(self, cursor):
        self.cursor = cursor
        self.rowcount = cursor.rowcount

    @property
    def lastrowid(self):
        row = self.cursor.fetchone()
        return row[0] if row else None

    def _columns(self):
        return [column.name for column in self.cursor.description or []]

    def _row(self, values):
        if values is None:
            return None
        return Row(self._columns(), values)

    def fetchone(self):
        return self._row(self.cursor.fetchone())

    def fetchall(self):
        columns = self._columns()
        return [Row(columns, values) for values in self.cursor.fetchall()]

    def __iter__(self):
        columns = self._columns()
        for values in self.cursor:
            yield Row(columns, values)


class Connection:
    def __init__(self, raw):
        self.raw = raw
        self.row_factory = None

    def execute(self, sql: str, params=()):
        cursor = self.raw.cursor()
        cursor.execute(translate_sql(sql), params)
        return Cursor(cursor)

    def executemany(self, sql: str, seq_of_params):
        cursor = self.raw.cursor()
        params = list(seq_of_params)
        if not params:
            return Cursor(cursor)
        cursor.executemany(translate_sql(sql), params)
        return Cursor(cursor)

    def executescript(self, sql: str):
        cursor = self.raw.cursor()
        statements = [statement.strip() for statement in sql.split(";") if statement.strip()]
        for statement in statements:
            cursor.execute(translate_sql(statement))
        return Cursor(cursor)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()


def connect(url: str | None = None) -> Connection:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "Install psycopg in a virtual environment to use Postgres: "
            "python3 -m venv .venv && . .venv/bin/activate && python -m pip install -r requirements.txt"
        ) from exc
    return Connection(psycopg.connect(url or database_url()))


def parse_database_url_env_file(path: Path) -> str:
    preload_dotenv(path)
    return database_url()
