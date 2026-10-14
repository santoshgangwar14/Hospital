"""python -m scripts.create_platform_admin <username> <password> [name]"""
import sys
from hms.db import platform_tx
from hms.security import hash_pw

u, p = sys.argv[1], sys.argv[2]
with platform_tx() as c:
    c.execute("insert into platform_users(username,name,password_hash) values(%s,%s,%s) on conflict (username) do update set password_hash=excluded.password_hash",
              (u.lower(), sys.argv[3] if len(sys.argv) > 3 else "Platform Owner", hash_pw(p)))
print("platform admin ready:", u)
