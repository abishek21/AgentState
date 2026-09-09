from agentstate.models import StateRecordInput


def test_get_or_create_project_is_idempotent_by_remote_fingerprint(store):
    p1 = store.get_or_create_project("github.com/acme/repo", name="repo")
    p2 = store.get_or_create_project("github.com/acme/repo", name="repo-again")
    assert p1.id == p2.id


def test_different_remotes_get_different_projects(store):
    p1 = store.get_or_create_project("github.com/acme/repo-a")
    p2 = store.get_or_create_project("github.com/acme/repo-b")
    assert p1.id != p2.id


def test_explicit_project_id_joins_existing_project(store):
    p1 = store.get_or_create_project("github.com/acme/repo")
    p2 = store.get_or_create_project(None, project_id=p1.id)
    assert p1.id == p2.id


def test_create_and_get_record(store, project):
    rec = store.create(StateRecordInput(
        project_id=project.id, type="finding", title="baseline", content="142 tok/s",
    ))
    fetched = store.get(rec.id)
    assert fetched.title == "baseline"
    assert fetched.status == "active"
    assert fetched.is_current is True


def test_update_record(store, project):
    rec = store.create(StateRecordInput(
        project_id=project.id, type="task", title="do thing", content="...",
    ))
    updated = store.update(rec.id, status="in_progress")
    assert updated.status == "in_progress"
    assert updated.updated_at >= rec.created_at
