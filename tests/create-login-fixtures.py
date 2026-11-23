from pathlib import Path
import sys

import secrets

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from seed_server import seed


output = Path(sys.argv[1])
output.mkdir()
pepper = secrets.token_bytes(32)
(output / "pepper.bin").write_bytes(pepper)
students = [
    {"name": "测试甲", "id": "001", "group": "测试2401"},
    {"name": "测试乙", "id": "002", "group": "测试2401"},
    {"name": "测试丙", "id": "003", "group": "测试2402"},
]
seed(output / "attendance.sqlite3", pepper, students, [students[0]], "fixture-pass", secrets.token_bytes(32))
