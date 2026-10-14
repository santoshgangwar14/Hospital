import contextlib, os
import psycopg2, psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

psycopg2.extras.register_uuid()
DSN = {"app": os.getenv("APP_DSN", "postgresql://hms_app:hms_app@localhost/hms"),
       "platform": os.getenv("PLATFORM_DSN", "postgresql://hms_platform:hms_platform@localhost/hms")}
_pools = {}


def _pool(kind):
    if kind not in _pools:
        _pools[kind] = ThreadedConnectionPool(1, 10, DSN[kind])
    return _pools[kind]


@contextlib.contextmanager
def _tx(kind, tenant_id=None):
    p = _pool(kind)
    conn = p.getconn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if kind == "app":   # transaction-local tenant context; empty means NO rows are visible
            cur.execute("select set_config('app.tenant_id', %s, true)", (str(tenant_id) if tenant_id else "",))
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        p.putconn(conn)


def tenant_tx(tenant_id):
    return _tx("app", tenant_id)


def platform_tx():
    return _tx("platform")
