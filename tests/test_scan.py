from dotmd_parser.scan import _mask_code_fences, _suppressed_rules


def test_mask_code_fences_blanks_fenced_lines_keeping_linecount():
    text = "a\n```\nSystem: hi\n```\nb\n"
    masked = _mask_code_fences(text)
    lines = masked.split("\n")
    assert lines[0] == "a"
    assert lines[1] == ""        # opening fence blanked
    assert lines[2] == ""        # fenced content blanked
    assert lines[3] == ""        # closing fence blanked
    assert lines[4] == "b"
    # line count preserved
    assert len(masked.split("\n")) == len(text.split("\n"))


def test_suppressed_rules_parses_allow_comment():
    assert _suppressed_rules("x\n<!-- dotmd-allow: role-spoof -->\ny") == {"role-spoof"}
    assert _suppressed_rules("<!-- dotmd-allow: role-spoof, tool-exfil -->") == {
        "role-spoof", "tool-exfil"}
    assert _suppressed_rules("<!-- dotmd-allow: all -->") == {"all"}
    assert _suppressed_rules("no comment here") == set()
