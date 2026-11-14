from types import SimpleNamespace

from agent import matcher

KW = ["LTE", "4G", "eNodeB", "VoLTE"]
PAT = matcher.build_pattern(KW)


def test_keyword_hits():
    assert matcher.keyword_match("Our new LTE rollout", PAT)
    assert matcher.keyword_match("upgrading to 4G.", PAT)
    assert matcher.keyword_match("eNodeB tuning tips", PAT)


def test_keyword_misses():
    assert not matcher.keyword_match("Gold standard in cooking", PAT)
    assert not matcher.keyword_match("web3G is not a thing", PAT)


def test_parse_verdict():
    assert matcher.parse_verdict("YES: about VoLTE") == (True, "about VoLTE")
    assert matcher.parse_verdict("NO: passing mention")[0] is False
    assert matcher.parse_verdict("")[0] is False


class FakeClient:
    def __init__(self, text, stop="end_turn"):
        self.messages = SimpleNamespace(create=lambda **kw: SimpleNamespace(
            stop_reason=stop, content=[SimpleNamespace(type="text", text=text)]))


def test_llm_match_yes():
    ok, why = matcher.llm_match("LTE post", "claude-test", FakeClient("YES: LTE topic"))
    assert ok and why == "LTE topic"


def test_llm_match_refusal():
    ok, _ = matcher.llm_match("x", "claude-test", FakeClient("", stop="refusal"))
    assert not ok


def test_main_survives_emoji_on_cp1252_console(monkeypatch, capsys):
    import io, sys
    from agent import __main__ as cli
    buf = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", buf)
    monkeypatch.setattr(sys, "argv", ["agent", "--help"])
    try:
        cli.main()
    except SystemExit:
        pass
    print("rocket \U0001f680")  # would raise UnicodeEncodeError before the fix


class FakeNvidia:
    def __init__(self, content):
        msg = SimpleNamespace(content=content)
        self.chat = SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kw: SimpleNamespace(choices=[SimpleNamespace(message=msg)])))


def test_nvidia_route_yes():
    ok, why = matcher.llm_match("LTE post", "nvidia/nemotron-3.5-bolt-e4b", FakeNvidia("YES: about LTE"))
    assert ok and why == "about LTE"


def test_nvidia_route_no_and_empty():
    assert matcher.llm_match("x", "nvidia/m", FakeNvidia("NO: food"))[0] is False
    assert matcher.llm_match("x", "nvidia/m", FakeNvidia(None)) == (False, "")


def test_parse_ignores_think_block_and_markdown():
    assert matcher.parse_verdict("<think>maybe NO... or YES</think>\nYES: ok") == (True, "ok")
    assert matcher.parse_verdict("**YES**: bold") == (True, "bold")
    assert matcher.parse_verdict("NOTE this is odd") == (False, "")  # 'NOTE' is not 'NO'


def test_claude_models_do_not_use_nvidia(monkeypatch):
    called = {}
    class Anth:
        messages = SimpleNamespace(create=lambda **kw: called.setdefault("m", kw["model"]) and SimpleNamespace(
            stop_reason="end_turn", content=[SimpleNamespace(type="text", text="YES: y")]))
    assert matcher.llm_match("t", "claude-haiku-5-5", Anth())[0] is True
    assert called["m"] == "claude-haiku-5-5"


def _real_pattern():
    import yaml
    from pathlib import Path
    cfg = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text(encoding="utf-8"))
    return matcher.build_pattern(cfg["keywords"])


def test_config_keywords_hit_node_names():
    pat = _real_pattern()
    for text in [
        "Troubleshooting the S1-MME link between the eNodeB and MME",
        "PGW and SGW selection in the Evolved Packet Core",
        "Our PCRF policy rules for VoLTE calls",
        "X2 interface handover failures, RSRP below -110",
        "Carrier Aggregation on PDSCH and PUCCH",
        "Hands-on LTE-Advanced BBU/RRU commissioning",
    ]:
        assert matcher.keyword_match(text, pat), text


def test_config_keywords_ignore_lookalikes():
    pat = _real_pattern()
    for text in [
        "EPS grew 12% this quarter",                       # earnings per share
        "SAE standards for automotive steel",              # not SAE-GW
        "Enjoying the summer, mmmm tasty",                 # 'mme' inside a word
        "Our subscriber base is growing",                  # 'sub' words, no node names
        "Gold medal winners announced",
    ]:
        assert not matcher.keyword_match(text, pat), text
