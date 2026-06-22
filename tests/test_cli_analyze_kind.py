import json
import pytest
from dotmd_parser.cli import run


def test_apply_from_ref_kind_via_cli(tmp_path, capsys):
    (tmp_path / "SKILL.md").write_text("# Root\n", encoding="utf-8")
    (tmp_path / "guide.md").write_text("# Guide\n", encoding="utf-8")
    analysis = {"edges": [{"from": "SKILL.md", "to": "guide.md", "kind": "ref"}],
                "shared_proposals": []}
    js = tmp_path / "analysis.json"
    js.write_text(json.dumps(analysis), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        run(["analyze", str(tmp_path), "--apply-from", str(js)])
    assert e.value.code == 0
    assert (tmp_path / "SKILL.md").read_text(encoding="utf-8").startswith("@ref guide.md")


def test_apply_from_max_include_bytes_demotes(tmp_path):
    (tmp_path / "SKILL.md").write_text("# Root\n", encoding="utf-8")
    (tmp_path / "big.md").write_text("x" * 500, encoding="utf-8")
    analysis = {"edges": [{"from": "SKILL.md", "to": "big.md", "kind": "include"}],
                "shared_proposals": []}
    js = tmp_path / "analysis.json"
    js.write_text(json.dumps(analysis), encoding="utf-8")
    with pytest.raises(SystemExit):
        run(["analyze", str(tmp_path), "--apply-from", str(js), "--max-include-bytes", "100"])
    assert (tmp_path / "SKILL.md").read_text(encoding="utf-8").startswith("@ref big.md")
