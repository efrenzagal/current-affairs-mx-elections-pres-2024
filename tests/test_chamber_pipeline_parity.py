"""Guard the duplication the per-chamber split deliberately bought.

`camara_de_diputados/` and `camara_de_senadores/` each own a full copy of the
seat-resolution and roster pipelines. That is the point -- each chamber reads
top to bottom without a `chamber` flag threaded through it -- but it means a fix
to the shared *algorithm* has to be applied twice, and nothing in Python
notices when it is applied only once.

These tests pin the functions that are supposed to be identical between the two
copies. A failure here is not necessarily a bug: it means the two chambers
disagree about a function that used to be shared, so either the fix is missing
from one side, or the divergence is intentional and the function belongs in the
EXPECTED_DIFFERENT list below with a reason.
"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PAIRS = {
    "escanos/seat_members.py": (
        "camara_de_diputados/escanos/seat_members.py",
        "camara_de_senadores/escanos/seat_members.py",
    ),
    "escanos/seat_margins.py": (
        "camara_de_diputados/escanos/seat_margins.py",
        "camara_de_senadores/escanos/seat_margins.py",
    ),
    "escanos/audited_overrides.py": (
        "camara_de_diputados/escanos/audited_overrides.py",
        "camara_de_senadores/escanos/audited_overrides.py",
    ),
    "composicion/ingest.py": (
        "camara_de_diputados/composicion/ingest.py",
        "camara_de_senadores/composicion/ingest.py",
    ),
}

# Functions each chamber is expected to write differently, and why. Everything
# else that exists in both files must match character for character.
EXPECTED_DIFFERENT = {
    "escanos/seat_members.py": {
        "load_current_roster": "names its own chamber in the error message",
        "load_substitutes": "reads dim_diputados vs dim_senadores",
        "load_chamber_vote_rows": "only the Senado translates its vote vocabulary",
        "resolve_display_names": "different roll-call tables and name formatting",
    },
    "escanos/seat_margins.py": {
        "load_results": "district number vs state-list position; only the Camara has a cabecera",
        "materialize": "different seat query and district_seat column",
    },
    "escanos/audited_overrides.py": {},
    "composicion/ingest.py": {
        "resolve_seats": "district/RP matching vs senador_id matching",
        "_vote_membership_observations": "reads each chamber's own vote tables",
        "_insert_snapshot": "different source_url and constitutional seat count",
        "materialize": "different reconciliation invariants per chamber",
        "main": "different resolution summary printed",
    },
}


def top_level_functions(path: Path) -> dict[str, str]:
    source = path.read_text()
    lines = source.splitlines()
    return {
        node.name: "\n".join(lines[node.lineno - 1 : node.end_lineno])
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef)
    }


class ChamberPipelineParityTests(unittest.TestCase):
    def test_shared_functions_are_identical_in_both_chambers(self):
        for label, (dip_path, sen_path) in PAIRS.items():
            dip = top_level_functions(ROOT / dip_path)
            sen = top_level_functions(ROOT / sen_path)
            expected = EXPECTED_DIFFERENT[label]
            for name in sorted(set(dip) & set(sen)):
                if name in expected:
                    continue
                with self.subTest(file=label, function=name):
                    self.assertEqual(
                        dip[name],
                        sen[name],
                        f"{name}() has drifted between the two chambers in {label}. "
                        "Apply the fix to both copies, or record the divergence in "
                        "EXPECTED_DIFFERENT with a reason.",
                    )

    def test_expected_differences_really_do_differ(self):
        # Keeps the allowlist honest: once a divergence is resolved, its entry
        # has to go, or it silently hides a future drift in the same function.
        for label, (dip_path, sen_path) in PAIRS.items():
            dip = top_level_functions(ROOT / dip_path)
            sen = top_level_functions(ROOT / sen_path)
            for name in EXPECTED_DIFFERENT[label]:
                with self.subTest(file=label, function=name):
                    self.assertIn(name, dip, f"{name}() is gone from {dip_path}")
                    self.assertIn(name, sen, f"{name}() is gone from {sen_path}")
                    self.assertNotEqual(
                        dip[name],
                        sen[name],
                        f"{name}() is now identical in both chambers; "
                        "drop it from EXPECTED_DIFFERENT.",
                    )


if __name__ == "__main__":
    unittest.main()
