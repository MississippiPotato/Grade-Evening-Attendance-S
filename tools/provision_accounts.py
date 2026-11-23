"""Issue per-user static attendance accounts from the two source workbooks."""

import argparse
import base64
from collections import Counter
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
import unicodedata

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from openpyxl import load_workbook


ITERATIONS = 300_000


def encode(data):
    return base64.b64encode(data).decode("ascii")


def json_bytes(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def normalize_username(username):
    return unicodedata.normalize("NFKC", username).strip().lower()


def read_people(path):
    workbook = load_workbook(path, read_only=True, data_only=True)
    people = []
    try:
        for sheet in workbook:
            rows = iter(sheet.rows)
            headers = [str(c.value or "").strip() for c in next(rows, ())]
            if not all(field in headers for field in ("学号", "姓名", "班级")):
                continue
            indices = [headers.index(field) for field in ("学号", "姓名", "班级")]
            for number, row in enumerate(rows, start=2):
                id_cell, name_cell, group_cell = (row[i] for i in indices)
                if all(cell.value is None for cell in (id_cell, name_cell, group_cell)):
                    continue
                number_value = id_cell.value
                if type(number_value) is int or (type(number_value) is float and number_value.is_integer()):
                    student_id = str(int(number_value))
                    if re.fullmatch(r"0+", id_cell.number_format or ""):
                        student_id = student_id.zfill(len(id_cell.number_format))
                else:
                    student_id = str(number_value or "").strip()
                name = str(name_cell.value or "").strip()
                group = str(group_cell.value or "").strip()
                if not re.fullmatch(r"[0-9]{1,24}", student_id) or not name or not group:
                    raise ValueError(f"{path.name} 的 {sheet.title} 第 {number} 行缺少有效姓名、班级或学号。")
                people.append({"id": student_id, "name": name, "group": group})
    finally:
        workbook.close()
    if not people or len({person["id"] for person in people}) != len(people):
        raise ValueError(f"{path.name} 无有效记录或存在重复学号。")
    return people


def read_key(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    else:
        key = ec.generate_private_key(ec.SECP256R1())
        data = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                 serialization.NoEncryption())
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as handle:
            handle.write(data)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise ValueError("签发密钥须为 P-256 私钥。")
    return key


def public_module(key):
    public = key.public_key().public_numbers()

    def coordinate(value):
        return base64.urlsafe_b64encode(value.to_bytes(32, "big")).rstrip(b"=").decode("ascii")

    jwk = {"kty": "EC", "crv": "P-256", "x": coordinate(public.x), "y": coordinate(public.y), "ext": True}
    return "export const accountIssuer = " + json_bytes(jwk).decode("utf-8") + ";\n"


def issue(key, details, password, identity):
    key_material = key.private_numbers().private_value.to_bytes(32, "big")
    account_id = hmac.new(key_material, ("upc-attendance:" + identity).encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    lookup = hashlib.sha256(normalize_username(details["username"]).encode("utf-8")).hexdigest()
    salt = os.urandom(16)
    nonce = os.urandom(12)
    encryption_key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    contents = json_bytes(dict(details, accountId=account_id))
    sealed = AESGCM(encryption_key).encrypt(nonce, contents, account_id.encode("ascii"))
    payload = json_bytes({"id": account_id, "lookup": lookup, "salt": encode(salt),
                          "iv": encode(nonce), "ciphertext": encode(sealed)})
    signature = key.sign(payload, ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(signature)
    return {"format": "upc-attendance-account", "version": 1, "payload": encode(payload),
            "signature": encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))}


def save_account(path, account):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json_bytes(account) + b"\n")


def safe_part(value):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", value).strip(". ")
    return cleaned or "未命名"


def provision(output, key, students, leaders, admin_password):
    master = {person["id"]: person for person in students}
    for person in leaders:
        if master.get(person["id"]) != person:
            raise ValueError("负责人名单中有记录与学籍表的姓名、班级或学号不一致。")

    all_names = Counter(normalize_username(person["name"]) for person in students)
    numbered = Counter()
    for person in students:
        group = person["group"]
        numbered[group] += 1
        name = person["name"]
        details = {"username": name, "role": "student", "person": person}
        filename = f"{numbered[group]:02d}-{safe_part(name)}.json"
        save_account(output / "学生" / safe_part(group) / filename,
                     issue(key, details, person["id"], "student:" + person["id"]))
    numbered.clear()
    for person in leaders:
        group = person["group"]
        numbered[group] += 1
        name = "A" + person["name"]
        details = {"username": name, "role": "leader", "person": person}
        filename = f"{numbered[group]:02d}-{safe_part(name)}.json"
        save_account(output / "班级负责人" / safe_part(group) / filename,
                     issue(key, details, person["id"], "leader:" + person["id"] + ":" + group))
    save_account(output / "管理员账户.json", issue(key, {"username": "admin", "role": "admin",
        "roster": students, "staff": leaders}, admin_password, "admin:primary"))

    uncovered = len(set(p["group"] for p in students) - set(p["group"] for p in leaders))
    print(json.dumps({"students": len(students), "leaders": len(leaders), "admin": 1,
                      "groups": len({p["group"] for p in students}), "groups_without_leader": uncovered,
                      "duplicate_student_names": sum(value > 1 for value in all_names.values())}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--students", required=True, type=Path)
    parser.add_argument("--leaders", required=True, type=Path)
    parser.add_argument("--signing-key", required=True, type=Path)
    parser.add_argument("--public-module", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--password-stdin", action="store_true")
    args = parser.parse_args()
    private_paths = [args.signing_key.resolve(), args.output.resolve()]
    checkout = Path(__file__).resolve().parents[1]
    if any(item.is_relative_to(checkout) for item in private_paths):
        parser.error("签发私钥和真实账户文件必须写在站点项目目录之外。")
    if args.output.exists():
        parser.error("输出目录已存在，请指定新目录。")
    students = read_people(args.students)
    leaders = read_people(args.leaders)
    password = (sys.stdin.readline().rstrip("\r\n") if args.password_stdin else getpass.getpass("管理员密码: "))
    if not password:
        parser.error("管理员密码不能为空。")
    key = read_key(args.signing_key)
    if args.public_module:
        args.public_module.write_text(public_module(key), encoding="utf-8")
    provision(args.output, key, students, leaders, password)


if __name__ == "__main__":
    main()
