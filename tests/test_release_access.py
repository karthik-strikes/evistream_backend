"""Offline regression tests for the public release's authorization and review fixes."""
import asyncio
import socket
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID
from unittest.mock import AsyncMock

import pytest

USER, PROJECT, FOREIGN, FORM, DOC = [UUID(int=i) for i in range(1, 6)]


class MemoryDB:
    """Small data store; reject the obsolete assignment column like PostgREST."""
    def __init__(self, **tables):
        self.tables = tables

    def table(self, name):
        db = self

        class Query:
            def __init__(self):
                self.filters = []
                self.updates = None

            def select(self, *args, **kwargs): return self
            def limit(self, *args): return self
            def order(self, *args, **kwargs): return self
            def range(self, *args): return self

            def eq(self, key, value):
                if name == 'review_assignments' and key == 'form_id':
                    raise ValueError('review_assignments.form_id does not exist')
                self.filters.append((key, value))
                return self

            def update(self, values):
                self.updates = values
                return self

            def execute(self):
                rows = [r for r in db.tables.get(name, [])
                        if all(r.get(k) == v for k, v in self.filters)]
                if self.updates:
                    for row in rows: row.update(self.updates)
                return SimpleNamespace(data=rows)

        return Query()


@pytest.fixture(scope='module')
def api(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        for key, value in {
            'PYTHON_DOTENV_DISABLED': '1', 'AWS_EC2_METADATA_DISABLED': 'true',
            'SECRET_KEY': 'synthetic-test-secret-' + 'x' * 48,
            'SUPABASE_URL': 'http://127.0.0.1:1', 'SUPABASE_KEY': 'test',
            'SUPABASE_SERVICE_KEY': 'test', 'AWS_ACCESS_KEY_ID': 'test',
            'AWS_SECRET_ACCESS_KEY': 'test', 'S3_BUCKET': 'test',
            'REDIS_PORT': '1', 'DSPY_CACHEDIR': str(tmp_path_factory.mktemp('dspy')),
        }.items(): mp.setenv(key, value)

        def deny_network(*args, **kwargs):
            raise AssertionError('Offline regression tests must not contact real services')

        def fake_aws(client, operation, params):
            assert operation == 'GetCallerIdentity'
            return {'Account': '000000000000', 'UserId': 'test'}

        import botocore.client
        import redis
        mp.setattr(socket.socket, 'connect', deny_network)
        mp.setattr(botocore.client.BaseClient, '_make_api_call', fake_aws)
        mp.setattr(redis.Redis, 'ping', lambda *_: False)
        from app.main import app
        from app.dependencies import get_current_user
        async def current_user():
            return USER
        app.dependency_overrides[get_current_user] = current_user
        yield app
        app.dependency_overrides.clear()


def request(app, method, path, **kwargs):
    import httpx
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            return await client.request(method, path, **kwargs)
    return asyncio.run(run())


def test_summary_checks_membership_even_after_cache_is_populated(api, monkeypatch):
    from fastapi import HTTPException
    from app.api.v1 import results
    check = AsyncMock(return_value={'can_view_results': True})
    monkeypatch.setattr(results, 'check_project_access', check)
    monkeypatch.setattr(results.cache_service, 'get', lambda _: {'documents': ['synthetic']})
    path = f'/api/v1/results/consensus-summary?project_id={PROJECT}&form_id={FORM}'
    assert request(api, 'GET', path).status_code == 200
    check.side_effect = HTTPException(403, 'Membership revoked')
    denied = request(api, 'GET', path)
    assert denied.status_code == 403
    assert 'synthetic' not in denied.text
    assert check.await_count == 2


def test_audit_history_is_limited_to_authorized_project(api, monkeypatch):
    from app.api.v1 import audit
    from app.services import audit_service
    row = dict(id=str(DOC), user_id=str(USER), project_id=str(FOREIGN),
               entity_type='consensus_result', entity_id=str(DOC), action='updated',
               field_name=None, old_value=None, new_value={'private': 'synthetic'},
               metadata={}, created_at=datetime.now(timezone.utc).isoformat())
    db = MemoryDB(audit_trail=[row])
    monkeypatch.setattr(audit_service, 'get_supabase', lambda: db)
    monkeypatch.setattr(audit, 'check_project_access', AsyncMock())
    path = f'/api/v1/audit/entity/consensus_result/{DOC}?project_id={PROJECT}'
    assert request(api, 'GET', path).json() == []
    row['project_id'] = str(PROJECT)
    assert request(api, 'GET', path).json()[0]['new_value'] == {'private': 'synthetic'}


@pytest.mark.parametrize('role,expected', [('reviewer_1', ['r1', 'ai']), ('reviewer_2', ['r2', 'ai']), ('adjudicator', ['r1', 'r2', 'ai'])])
def test_document_assignments_support_blinding_for_every_form(api, monkeypatch, role, expected):
    from app.services import blinding_service as service
    db = MemoryDB(
        forms=[{'id': str(FORM), 'review_settings': {'blinding': 'full'}}],
        review_assignments=[{'reviewer_user_id': str(USER), 'document_id': str(DOC), 'reviewer_role': role}],
    )
    monkeypatch.setattr(service, 'get_supabase', lambda: db)
    rows = [dict(id='r1', reviewer_role='reviewer_1', extracted_by=str(USER if role == 'reviewer_1' else UUID(int=7)), extraction_type='manual'),
            dict(id='r2', reviewer_role='reviewer_2', extracted_by=str(USER if role == 'reviewer_2' else UUID(int=8)), extraction_type='manual'),
            dict(id='ai', extraction_type='ai')]
    visible = asyncio.run(service.filter_results_for_user(rows, USER, DOC, FORM))
    assert [r['id'] for r in visible] == expected


def test_adjudicator_assignment_and_summary_use_document_scope(api, monkeypatch):
    from app.services import blinding_service, adjudication_service
    db = MemoryDB(review_assignments=[
        dict(id=str(UUID(int=i)), reviewer_user_id=str(USER), project_id=str(PROJECT),
             document_id=str(DOC), reviewer_role=role, status='completed')
        for i, role in enumerate(['adjudicator', 'reviewer_1', 'reviewer_2'], 10)
    ])
    monkeypatch.setattr(blinding_service, 'get_supabase', lambda: db)
    monkeypatch.setattr(adjudication_service, 'get_supabase', lambda: db)
    assert asyncio.run(blinding_service.can_view_adjudication(USER, DOC, FORM, PROJECT))
    assert not asyncio.run(blinding_service.can_view_adjudication(USER, DOC, FORM, FOREIGN))
    summary = asyncio.run(adjudication_service.get_adjudication_summary(PROJECT, FORM))
    assert summary['ready_for_adjudication'] == 1


def test_password_reset_token_is_stored_consumed_and_not_reusable(api, monkeypatch):
    from app.api.v1 import auth
    from urllib.parse import urlparse, parse_qs
    user = dict(id=str(USER), email='researcher@example.org', full_name='Researcher', hashed_password='old')
    monkeypatch.setattr(auth, 'supabase', MemoryDB(users=[user]))
    email = AsyncMock()
    monkeypatch.setattr(auth, '_send_password_reset_email', email)
    assert request(api, 'POST', '/api/v1/auth/forgot-password', json={'email': user['email']}).status_code == 200
    raw = parse_qs(urlparse(email.await_args.args[2]).query)['token'][0]
    assert user['password_reset_token'] != raw
    payload = {'token': raw, 'new_password': 'NewResearchPassword9!'}
    assert request(api, 'POST', '/api/v1/auth/reset-password', json=payload).status_code == 200
    assert user['password_reset_token'] is None
    assert auth.auth_service.verify_password(payload['new_password'], user['hashed_password'])
    assert request(api, 'POST', '/api/v1/auth/reset-password', json=payload).status_code == 400
