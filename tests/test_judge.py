from aist_swarm.judge import F2P_OK, P2P_OK, lookup, parse_report
from aist_swarm.tasks import _id_list


def test_parse_report_strips_color_and_messages():
    out = "\x1b[32mPASSED\x1b[0m tests/a.py::t1\nFAILED tests/a.py::t2 - AssertionError: x\nERROR tests/b.py::t3"
    assert parse_report(out) == {"tests/a.py::t1": "PASSED", "tests/a.py::t2": "FAILED", "tests/b.py::t3": "ERROR"}


def test_lookup_exact_and_truncated_ids():
    status = {"t.py::f[not joined with space]": "PASSED", "t.py::g[a b]": "PASSED", "t.py::g[a c]": "FAILED"}
    assert lookup(status, "t.py::f[not", F2P_OK)
    assert not lookup(status, "t.py::g[a", F2P_OK)
    assert not lookup(status, "t.py::missing", P2P_OK)


def test_id_list_formats():
    assert _id_list('["a", "b"]') == ["a", "b"]
    assert _id_list("['a', 'b']") == ["a", "b"]
    assert _id_list(None) == []
