"""A plan read as a graph: every edge points at a node, every vendor chosen is there, and nothing is invented."""

from eventmate.planning import plan_graph
from eventmate.service import EventMate
from tests.conftest import EXAMPLE, TODAY


def test_the_graph_has_the_event_its_tasks_its_vendors_and_its_checks(tmp_path):
    plan = EventMate(log_dir=tmp_path / "l", plan_dir=tmp_path / "p", run_id="graph").plan(EXAMPLE, today=TODAY).plan
    g = plan_graph.build(plan)
    ids = [n["id"] for n in g["nodes"]]
    assert len(ids) == len(set(ids)), "node ids are unique"
    assert all(e["source"] in ids and e["target"] in ids for e in g["edges"]), "every edge joins two nodes"
    kinds = {n["kind"] for n in g["nodes"]}
    assert {"event", "task", "vendor", "check"} <= kinds
    assert {n["label"] for n in g["nodes"] if n["kind"] == "vendor"} == {s.name for s in plan.selections}
    assert sum(1 for n in g["nodes"] if n["kind"] == "task") == len(plan.items)
    assert sum(1 for n in g["nodes"] if n["kind"] == "check") == len(plan.validation.checks)
    assert any(e["kind"] == "precedes" for e in g["edges"]) and any(e["kind"] == "books" for e in g["edges"])
    for s in plan.selections:
        for a in s.alternatives:
            assert any(e["kind"] == "not_chosen_over" and e["label"] == a.reason_not_chosen for e in g["edges"])
