import asyncio

import pytest

from harness_agent_runtime import (
    Claim,
    ContextManager,
    Delegator,
    FairScheduler,
    Quota,
    RuntimeDenied,
    SpecialistRegistry,
    SpecialistSpec,
    TaskBoard,
    TaskGraph,
    TaskNode,
    decompose,
    run_swarm,
    synthesize,
)

TOOLS = {"read_file", "edit_file", "run_tests", "web_fetch"}


def _reg():
    r = SpecialistRegistry({"deepseek-chat", "deepseek-reasoner"}, TOOLS)
    r.register(
        "t1",
        SpecialistSpec(
            "coder", "write code", "deepseek-chat", frozenset({"read_file", "edit_file"})
        ),
    )
    r.register(
        "t1",
        SpecialistSpec(
            "tester", "run tests", "deepseek-reasoner", frozenset({"read_file", "run_tests"})
        ),
    )
    return r


# ---- F030 graph
def test_graph_layers_and_validation():
    g = TaskGraph.build(
        [TaskNode("a", "A"), TaskNode("b", "B"), TaskNode("c", "C", deps=("a", "b"))]
    )
    assert g.layers() == [["a", "b"], ["c"]] and g.descendants("a") == {"c"}
    for bad, code in [
        ([TaskNode("a", "A", deps=("b",)), TaskNode("b", "B", deps=("a",))], "graph.cycle"),
        ([TaskNode("a", "A", deps=("zz",))], "graph.bad_dependency"),
        ([TaskNode("a", "A", deps=("a",))], "graph.bad_dependency"),
        ([TaskNode("a", "A"), TaskNode("a", "B")], "graph.bad_id"),
        ([], "graph.size"),
        ([TaskNode("../x", "A")], "graph.bad_id"),
    ]:
        with pytest.raises(RuntimeDenied) as e:
            TaskGraph.build(bad)
        assert e.value.code == code


def test_decompose_routes_roles_and_orders():
    kws = {"tester": ("test",), "coder": ("implement", "fix")}
    g = decompose("1. implement parser 2. run test suite", kws)
    assert [n.role for n in g.nodes.values()] == ["coder", "tester"]
    assert g.nodes["t2"].deps == ("t1",)
    par = decompose("fix bug; write docs", kws)
    assert par.layers() == [["t1", "t2"]] and par.nodes["t2"].role == "generalist"


# ---- F071/F076/F077 specialists
def test_specialist_model_and_tool_policy():
    r = _reg()
    with pytest.raises(RuntimeDenied) as e:
        r.register("t1", SpecialistSpec("x", "i", "gpt-evil", frozenset()))
    assert e.value.code == "specialist.model_not_allowed"
    with pytest.raises(RuntimeDenied) as e:
        r.register("t1", SpecialistSpec("x", "i", "deepseek-chat", frozenset({"rm_rf"})))
    assert e.value.code == "specialist.unknown_tool"
    r.authorize_tool("t1", "coder", "edit_file")
    with pytest.raises(RuntimeDenied) as e:
        r.authorize_tool("t1", "tester", "edit_file")
    assert e.value.code == "tool.denied"
    assert r.get("t1", "tester").model == "deepseek-reasoner"  # per-agent model kept
    with pytest.raises(RuntimeDenied) as e:  # other team cannot see t1's specialist
        r.get("t2", "coder")
    assert e.value.code == "specialist.not_found"


# ---- F072 delegation
def test_delegation_contracts():
    r = _reg()
    r.register("t1", SpecialistSpec("reviewer", "r", "deepseek-chat", frozenset({"read_file"})))
    d = Delegator(r, "t1", root_budget=1000)
    c = d.delegate("root", "coder", "k1", "g", {"edit_file"}, 600)
    assert c.depth == 1 and d.total_budget() == 1000  # conserved
    with pytest.raises(RuntimeDenied) as e:  # budget beyond what parent holds
        d.delegate("coder", "reviewer", "k2", "g", {"read_file"}, 700)
    assert e.value.code == "delegation.budget"
    with pytest.raises(RuntimeDenied) as e:  # child asks tool the parent lacks
        d.delegate("coder", "tester", "k3", "g", {"run_tests"}, 100)
    assert e.value.code == "delegation.tool_escalation"
    d.delegate("coder", "reviewer", "k4", "g", {"read_file"}, 100)
    with pytest.raises(RuntimeDenied) as e:  # cycle back to ancestor
        d.delegate("reviewer", "coder", "k5", "g", {"read_file"}, 10)
    assert e.value.code == "delegation.cycle"
    assert d.total_budget() == 1000


# ---- F075 contexts
def test_context_isolation_and_eviction():
    cm = ContextManager()
    shared = {"secret_a": "alpha", "pub": "visible"}
    a = cm.seed("A", 1000, shared, ("secret_a",))
    b = cm.seed("B", 1000, shared, ("pub",))
    assert a.render() == ["secret_a: alpha"] and b.render() == ["pub: visible"]
    b.add("only-b")
    assert "only-b" not in a.render()
    w = cm.window("C", 10)
    for _ in range(10):
        w.add("x" * 20)
    assert w.tokens() <= 10 or len(w.render()) == 1


# ---- F073/F079 board
def test_board_transitions_cas_scope_and_escalation():
    b = TaskBoard()
    c = b.create("t1", "do it", "u1")
    b.assign("t1", c.id, "agent:coder", "u1")
    v = c.version
    b.move("t1", c.id, "in_progress", "u1", expect_version=v)
    with pytest.raises(RuntimeDenied) as e:
        b.move("t1", c.id, "done", "u1", expect_version=v)  # stale version
    assert e.value.code == "board.conflict"
    with pytest.raises(RuntimeDenied) as e:
        b.move("t1", c.id, "todo", "u1", expect_version=c.version)
    assert e.value.code == "board.bad_transition"
    with pytest.raises(RuntimeDenied) as e:  # foreign team sees nothing
        b.move("t2", c.id, "done", "x", expect_version=c.version)
    assert e.value.code == "board.not_found"
    with pytest.raises(RuntimeDenied):
        b.assign("t1", c.id, "robot", "u1")
    item = b.escalate("t1", c.id, "needs human call", "agent:coder")
    assert c.status == "blocked" and b.human_queue("t1") == [item] and b.human_queue("t2") == []
    assert b.escalate("t1", c.id, "again", "x") is item  # idempotent
    b.resolve("t1", c.id, "user:42", "approved")
    assert c.status == "todo" and b.human_queue("t1") == []
    with pytest.raises(RuntimeDenied):
        b.resolve("t1", c.id, "user:42", "twice")


# ---- F078 synthesis
def test_synthesis_surfaces_conflicts_not_winners():
    out = synthesize(
        [
            [Claim("db", "postgres", "A", ("doc1",)), Claim("lang", "py", "A")],
            [Claim("db", "mysql", "B", ("doc2",)), Claim("lang", "py", "B", ("repo",))],
        ]
    )
    assert out["needs_human"] and out["conflicts"][0]["key"] == "db"
    assert [p["value"] for p in out["conflicts"][0]["positions"]] == ["mysql", "postgres"]
    assert "db" not in out["agreed"]
    assert out["agreed"]["lang"]["corroborated"] and out["unsupported"] == []
    lone = synthesize([[Claim("x", "1", "A")]])
    assert lone["unsupported"] == ["x"] and not lone["needs_human"]


# ---- F074 swarm
def test_swarm_runs_parallel_layers_isolated_and_skips_on_failure():
    r = _reg()
    g = TaskGraph.build(
        [
            TaskNode("a", "impl", "coder"),
            TaskNode("b", "tests", "tester"),
            TaskNode("c", "merge", "coder", deps=("a", "b"), inputs=("a",)),
            TaskNode("d", "after-b", "coder", deps=("b",)),
        ]
    )
    seen_ctx: dict[str, list[str]] = {}
    running = {"now": 0, "max": 0}

    async def ex(spec, goal, ctx):
        running["now"] += 1
        running["max"] = max(running["max"], running["now"])
        await asyncio.sleep(0.05)
        running["now"] -= 1
        seen_ctx[goal] = ctx
        if goal == "tests":
            raise RuntimeError("boom")
        return f"{spec}:{goal}"

    board = TaskBoard()
    res = asyncio.run(
        run_swarm(g, team_id="t1", registry=r, board=board, executor=ex, ctx=ContextManager())
    )
    assert running["max"] == 2  # a and b ran concurrently
    assert res["a"]["status"] == "done" and res["b"]["status"] == "failed"
    assert res["c"]["status"] == res["d"]["status"] == "skipped"
    assert len(board.human_queue("t1")) == 1  # failure reached a human
    assert seen_ctx["impl"] == [] and seen_ctx["tests"] == []  # no cross-agent leakage


# ---- F080/F024 scheduler
def test_scheduler_fair_no_starvation_and_quotas():
    s = FairScheduler(global_limit=4, default=Quota(max_running=2, max_queued=50))
    for i in range(30):
        s.submit("noisy", f"n{i}")
    for i in range(3):
        s.submit("quiet", f"q{i}")
    picks = []
    for _ in range(4):
        picks.append(s.next())
    assert s.next() is None  # global cap
    teams = [p[0] for p in picks]
    assert teams.count("quiet") == 2 and teams.count("noisy") == 2  # per-team cap 2 + fair
    s.release("quiet", picks[0][1] if picks[0][0] == "quiet" else picks[1][1])
    assert s.next()[0] == "quiet"  # quiet is not starved by 30 queued noisy jobs
    tiny = FairScheduler(1, Quota(max_queued=1))
    tiny.submit("t", "j1")
    with pytest.raises(RuntimeDenied) as e:
        tiny.submit("t", "j2")
    assert e.value.code == "scheduler.queue_full"


def test_scheduler_weights():
    s = FairScheduler(100, Quota(max_running=100))
    s.set_quota("big", Quota(max_running=100, weight=3))
    for i in range(40):
        s.submit("big", f"b{i}")
        s.submit("small", f"s{i}")
    got = [s.next()[0] for _ in range(40)]
    assert got.count("big") > got.count("small") * 2
