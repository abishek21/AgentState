"""Tests for the core correctness requirement: superseded state must not
be presented as current, and the original record must remain available
historically."""
from agentstate.models import StateRecordInput


def test_supersede_marks_old_record_stale_and_links_both_ways(store, project):
    old = store.create(StateRecordInput(
        project_id=project.id, type="decision", title="batch_size=16", content="use 16",
    ))
    old_after, new = store.supersede(old.id, StateRecordInput(
        project_id=project.id, type="decision", title="batch_size=8",
        content="16 causes OOM, use 8",
    ))

    assert old_after.status == "superseded"
    assert old_after.superseded_by_id == new.id
    assert old_after.is_current is False

    assert new.supersedes_id == old.id
    assert new.superseded_by_id is None
    assert new.is_current is True


def test_current_state_excludes_superseded_records(store, project):
    old = store.create(StateRecordInput(
        project_id=project.id, type="decision", title="batch_size=16", content="use 16",
    ))
    store.supersede(old.id, StateRecordInput(
        project_id=project.id, type="decision", title="batch_size=8", content="use 8",
    ))

    current_titles = {r.title for r in store.get_current_state(project.id)}
    assert "batch_size=8" in current_titles
    assert "batch_size=16" not in current_titles


def test_history_includes_both_current_and_superseded_records(store, project):
    old = store.create(StateRecordInput(
        project_id=project.id, type="decision", title="batch_size=16", content="use 16",
    ))
    store.supersede(old.id, StateRecordInput(
        project_id=project.id, type="decision", title="batch_size=8", content="use 8",
    ))

    history_titles = [r.title for r in store.get_history(project.id)]
    assert history_titles == ["batch_size=16", "batch_size=8"]


def test_chain_of_supersession_only_last_is_current(store, project):
    r1 = store.create(StateRecordInput(
        project_id=project.id, type="decision", title="v1", content="...",
    ))
    _, r2 = store.supersede(r1.id, StateRecordInput(
        project_id=project.id, type="decision", title="v2", content="...",
    ))
    _, r3 = store.supersede(r2.id, StateRecordInput(
        project_id=project.id, type="decision", title="v3", content="...",
    ))

    current = store.get_current_state(project.id)
    assert [r.title for r in current] == ["v3"]

    r1_after = store.get(r1.id)
    r2_after = store.get(r2.id)
    assert r1_after.status == "superseded" and r1_after.superseded_by_id == r2.id
    assert r2_after.status == "superseded" and r2_after.superseded_by_id == r3.id
