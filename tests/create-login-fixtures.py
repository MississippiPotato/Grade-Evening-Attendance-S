from pathlib import Path
import sys

from cryptography.hazmat.primitives.asymmetric import ec

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from provision_accounts import provision, public_module


output = Path(sys.argv[1])
key = ec.generate_private_key(ec.SECP256R1())
students = [
    {"name": "测试甲", "id": "001", "group": "测试2401"},
    {"name": "测试乙", "id": "002", "group": "测试2401"},
    {"name": "测试丙", "id": "003", "group": "测试2402"},
]
provision(output, key, students, [students[0]], "fixture-pass")
(output / "public.mjs").write_text(public_module(key), encoding="utf-8")
