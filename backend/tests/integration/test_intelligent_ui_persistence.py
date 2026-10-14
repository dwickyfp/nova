"""Seeded StarRocks/Redis migration, cross-device state and journal replay."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core.database import db
from app.modules.agents.run_journal import EVENTS_DDL, RUNS_DDL, run_journal
from app.modules.assistant.intelligent_ui import compose_block
from app.modules.assistant.repository import assistant_repository
from app.modules.assistant.security import observation_context
from tests.integration import test_intelligence_metadata
from tests.unit.test_intelligent_ui import CALCULATOR

intelligence_db = test_intelligence_metadata.intelligence_db

pytestmark = pytest.mark.engine


async def test_additive_migration_ui_state_and_replay(intelligence_db):
    scope = intelligence_db
    await assistant_repository.ensure_schema()
    migration = Path('migrations/20261013_studio_intelligent_ui.sql').read_text()
    await db.execute_system(migration.rstrip().removesuffix(';'))
    await db.execute_system(migration.rstrip().removesuffix(';'))
    assert migration.strip() in Path('../docker/init-nova.sql').read_text()
    for ddl in (RUNS_DDL, EVENTS_DDL):
        await db.execute_system(ddl)
    stamp = observation_context(scope)
    thread = await assistant_repository.create_thread(user_name=scope.principal, agent_id='ui-test')
    block = await compose_block(CALCULATOR, 'Adjust assumptions.', tables={}, business=False,
                                question='Calculator', claims=None, language='en')
    content_id = 'content-1'
    message = await assistant_repository.append_message(
        thread['thread_id'], user_name=scope.principal, role='assistant', content=block['fallback'],
        agent_id='ui-test', security_context=stamp,
        steps=[{'kind': 'ui', 'content_id': content_id, 'content_index': 1, **block}],
    )
    try:
        source = await assistant_repository.ui_source(
            thread['thread_id'], message['message_id'], content_id, user_name=scope.principal,
            security_context={**stamp, 'session_id': 'another-device'},
        )
        assert source['program'] == CALCULATOR
        assert await assistant_repository.ui_source(
            thread['thread_id'], message['message_id'], content_id, user_name='another-user',
            security_context=stamp,
        ) is None
        for change in ({'active_role': 'OTHER'}, {'security_context_version': 2}):
            assert await assistant_repository.ui_source(
                thread['thread_id'], message['message_id'], content_id,
                user_name=scope.principal, security_context={**stamp, **change},
            ) is None
        current = await assistant_repository.get_ui_state(
            thread['thread_id'], message['message_id'], content_id, user_name=scope.principal,
            security_context=stamp, block=source,
        )
        assert current['revision'] == 0
        saved = await assistant_repository.put_ui_state(
            thread['thread_id'], message['message_id'], content_id, user_name=scope.principal,
            security_context=stamp, block=source, revision=0, state={'$units': 8},
        )
        assert saved['revision'] == 1
        device = await assistant_repository.get_ui_state(
            thread['thread_id'], message['message_id'], content_id, user_name=scope.principal,
            security_context={**stamp, 'session_id': 'another-device'}, block=source,
        )
        assert device['state'] == {'$units': 8}
        with pytest.raises(HTTPException) as conflict:
            await assistant_repository.put_ui_state(
                thread['thread_id'], message['message_id'], content_id, user_name=scope.principal,
                security_context=stamp, block=source, revision=0, state={'$units': 9},
            )
        assert conflict.value.status_code == 409
        await assistant_repository.set_feedback(thread['thread_id'], message['message_id'],
                                                 'like', user_name=scope.principal)
        history = await assistant_repository.list_messages(thread['thread_id'],
                                                           user_name=scope.principal)
        assert history[0]['steps'][0]['program'] == CALCULATOR
        assert history[0]['feedback'] == 'like'
        operation_id = str(uuid4())
        await assistant_repository.append_message(
            thread['thread_id'], user_name=scope.principal, role='user', content='Continue',
            agent_id='ui-test', security_context=stamp,
            steps=[{'kind': 'ui_action', 'operation_id': operation_id, 'state': {'$units': 8}}],
        )
        assert await assistant_repository.ui_action_seen(
            thread['thread_id'], operation_id, user_name=scope.principal,
        )
        assert not await assistant_repository.ui_action_seen(
            thread['thread_id'], operation_id, user_name='another-user',
        )
        for ddl in (RUNS_DDL, EVENTS_DDL):
            await db.execute_system(ddl)
        run_id = str(uuid4())
        await run_journal.start(run_id=run_id, owner_name=scope.principal, agent_id='ui-test',
                                thread_id=thread['thread_id'], role_name=scope.active_role,
                                session_id=scope.session_id,
                                security_version=scope.security_context_version)
        from app.modules.assistant.service import (
            LoopContext,
            PendingArtifact,
            _ordered_output_frames,
        )

        frames = _ordered_output_frames(block['fallback'], [PendingArtifact('ui', block)],
                                         LoopContext(user_name=scope.principal))
        from app.modules.assistant import events

        for sequence, frame in enumerate(frames, start=1):
            name = frame.split('\n', 1)[0].removeprefix('event: ')
            payload = json.loads(frame.split('data: ', 1)[1])
            await run_journal.append(run_id, events.format_sse(name, {
                **payload, 'run_id': run_id, 'sequence': sequence,
            }))
        replay = await run_journal.events_after(run_id, 0)
        assert sum('event: ui\n' in frame for frame in replay) == 1
        assert sum('event: ui_delta\n' in frame for frame in replay) == 1
        assert json.dumps(CALCULATOR)[1:-1] in ''.join(replay)
    finally:
        await run_journal.delete_thread(thread['thread_id'], owner_name=scope.principal)
        await assistant_repository.delete_thread(thread['thread_id'], user_name=scope.principal)
