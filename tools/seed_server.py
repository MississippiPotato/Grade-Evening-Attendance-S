"""Create a private service database from the two original workbooks."""

import argparse
from contextlib import closing
import getpass
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import sys

from provision_accounts import read_key, read_people

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from service import connect, initialize, normalized, verifier


def seed(database, pepper, students, leaders, password, account_key, admin_username="admin"):
    master = {row["id"]: row for row in students}
    if any(master.get(row["id"]) != row for row in leaders):
        raise ValueError("负责人记录与学籍表不一致。")
    records = []
    staff = [("student", row) for row in students] + [("leader", row) for row in leaders]
    if any(normalized(admin_username) == normalized(("A" if role == "leader" else "") + row["name"])
           for role, row in staff):
        raise ValueError("管理员用户名与现有账户重复。")
    staff.append(("admin", {"name": admin_username, "id": None, "group": None}))
    for role, row in staff:
        login = normalized(("A" if role == "leader" else "") + row["name"])
        identifier = role + ":" + (row["id"] or "primary")
        if role == "leader":
            identifier += ":" + row["group"]
        account_id = hmac.new(account_key, ("upc-attendance:" + identifier).encode(), hashlib.sha256).hexdigest()[:32]
        salt = secrets.token_bytes(16)
        check = verifier(pepper, password if role == "admin" else row["id"], salt)
        records.append((account_id, login, row["name"], role, row["id"], row["group"], salt, check))
    initialize(database)
    with closing(connect(database)) as db:
        with db:
            db.executemany("INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?, ?, ?)", records)
            db.execute("INSERT INTO configuration VALUES ('directory_version', ?)", (secrets.token_hex(16),))
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    print("Seeded {} students, {} leaders and one admin.".format(len(students), len(leaders)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--students", type=Path, required=True)
    parser.add_argument("--leaders", type=Path, required=True)
    parser.add_argument("--signing-key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--admin-username", default="admin")
    parser.add_argument("--generate-admin-password", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error("账户数据库必须位于源码仓库之外。")
    students, leaders = read_people(args.students), read_people(args.leaders)
    if not args.signing_key.is_file():
        parser.error("请使用当前已签发账户对应的签发密钥，以保留原浏览器数据。")
    account_key = read_key(args.signing_key).private_numbers().private_value.to_bytes(32, "big")
    password = secrets.token_urlsafe(22) if args.generate_admin_password else getpass.getpass("管理员密码: ")
    admin_username = args.admin_username.strip()
    if not 0 < len(admin_username) <= 80 or not password:
        parser.error("管理员用户名或密码无效。")
    output.mkdir(parents=True, exist_ok=False)
    pepper = secrets.token_bytes(32)
    for filename, payload in (("pepper.bin", pepper),
                              ("管理员新密码.txt", ("用户名：" + admin_username + "\n密码：" + password + "\n").encode("utf-8"))):
        if filename.endswith(".txt") and not args.generate_admin_password:
            continue
        with os.fdopen(os.open(output / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
            stream.write(payload)
    seed(output / "attendance.sqlite3", pepper, students, leaders, password, account_key, admin_username)


if __name__ == "__main__":
    main()
