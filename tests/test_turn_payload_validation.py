"""应用在保存和回放之前校验完整轮次响应及其归属。"""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ecom.application.shopping.contracts import TurnRequest, TurnResponse
from ecom.application.shopping.orchestrator import CommerceOrchestrator
from ecom.kernel.intent import ShoppingIntent
from ecom.modules.sessions.schemas import TurnClaim, TurnCompletion, TurnReplay
from ecom.modules.sessions.service import CommerceError
from ecom.shared.principal import make_principal


class SessionSaveSpy:
    def __init__(self, replay=None):
        self.replay = replay
        self.completions = []

    async def claim_turn(self, session_id, principal, turn_id, request_hash):
        return self.replay or TurnClaim(
            session_id=session_id, principal_key=principal.principal_key,
            turn_id=turn_id, lease_token='lease', session_version=1,
        )

    async def complete_turn(self, claim, completion):
        self.completions.append(completion)


def application(sessions, response):
    async def invoke(state):
        return {'response': response}

    return CommerceOrchestrator(sessions, SimpleNamespace(ainvoke=invoke), None, None)


def test_invalid_status_is_not_a_valid_turn_response():
    with pytest.raises(ValidationError):
        TurnResponse(
            request_id='r', session_id='s', turn_id='t', intent='shop',
            status='paid',
        )


@pytest.mark.parametrize('confirmation', [False, True])
def test_application_rejects_invalid_response_before_save(confirmation):
    response = TurnResponse(
        request_id='r', session_id='s', turn_id='t', intent='shop',
        status='clarify',
    )
    response.status = 'paid'
    sessions = SessionSaveSpy()
    service = application(sessions, response)
    if confirmation:
        async def confirm(*args):
            return response
        service.chat_confirmation = confirm
    with pytest.raises(ValidationError):
        asyncio.run(service.turn(
            make_principal('test', 'a'), 's',
            TurnRequest(turn_id='t', message='确认' if confirmation else ''),
        ))
    assert sessions.completions == []


@pytest.mark.parametrize('field', ['session_id', 'turn_id'])
def test_application_rejects_wrong_response_owner_before_save(field):
    response = TurnResponse(
        request_id='r', session_id='s', turn_id='t', intent='shop',
        status='clarify',
    )
    setattr(response, field, 'different')
    sessions = SessionSaveSpy()
    with pytest.raises(CommerceError, match='invalid_completion'):
        asyncio.run(application(sessions, response).turn(
            make_principal('test', 'a'), 's', TurnRequest(turn_id='t'),
        ))
    assert sessions.completions == []


def test_application_rejects_non_json_response_before_save():
    response = TurnResponse(
        request_id='r', session_id='s', turn_id='t', intent='shop',
        status='clarify', errors=[{'detail': object()}],
    )
    sessions = SessionSaveSpy()
    with pytest.raises(ValueError):
        asyncio.run(application(sessions, response).turn(
            make_principal('test', 'a'), 's', TurnRequest(turn_id='t'),
        ))
    assert sessions.completions == []


def test_application_rejects_nested_model_before_save():
    response = TurnResponse(
        request_id='r', session_id='s', turn_id='t', intent='shop',
        status='clarify', errors=[{'detail': ShoppingIntent()}],
    )
    sessions = SessionSaveSpy()
    with pytest.raises(ValueError):
        asyncio.run(application(sessions, response).turn(
            make_principal('test', 'a'), 's', TurnRequest(turn_id='t'),
        ))
    assert sessions.completions == []


@pytest.mark.parametrize(('field', 'invalid'), [
    ('errors', ({'detail': 'not-json-tuple'},)),
    ('request_id', b'r'),
])
def test_application_rejects_raw_graph_non_json_before_dto(field, invalid):
    response = {
        'request_id': 'r', 'session_id': 's', 'turn_id': 't',
        'intent': 'shop', 'status': 'clarify',
    }
    response[field] = invalid
    sessions = SessionSaveSpy()
    with pytest.raises(ValueError):
        asyncio.run(application(sessions, response).turn(
            make_principal('test', 'a'), 's', TurnRequest(turn_id='t'),
        ))
    assert sessions.completions == []


def test_application_accepts_raw_graph_json_with_known_constraints():
    response = {
        'request_id': 'r', 'session_id': 's', 'turn_id': 't',
        'intent': 'shop', 'status': 'clarify',
        'constraints': ShoppingIntent(budget_max_minor=100),
    }
    sessions = SessionSaveSpy()
    result = asyncio.run(application(sessions, response).turn(
        make_principal('test', 'a'), 's', TurnRequest(turn_id='t'),
    ))
    assert result.status == 'clarify'
    assert result.constraints.budget_max_minor == 100
    assert len(sessions.completions) == 1
    assert sessions.completions[0].response == result.model_dump(mode='json')


def test_application_rejects_mutated_non_json_replay():
    payload = {
        'request_id': 'r', 'session_id': 's', 'turn_id': 't',
        'intent': 'shop', 'status': 'clarify',
    }
    replay = TurnReplay(session_id='s', turn_id='t', response=payload)
    replay.response['errors'] = [{'detail': ShoppingIntent()}]
    sessions = SessionSaveSpy(replay)
    with pytest.raises(ValueError):
        asyncio.run(application(sessions, None).turn(
            make_principal('test', 'a'), 's', TurnRequest(turn_id='t'),
        ))
    assert sessions.completions == []


@pytest.mark.parametrize(('envelope_type', 'field'), [
    (TurnCompletion, 'response'),
    (TurnCompletion, 'state'),
    (TurnReplay, 'response'),
])
@pytest.mark.parametrize('mutated', [False, True])
def test_envelope_rejects_raw_bytes_key(envelope_type, field, mutated):
    payload = {
        'session_id': 's', 'turn_id': 't',
        'response': {'session_id': 's', 'turn_id': 't'},
    }
    if field == 'state':
        payload['state'] = {}
    if mutated:
        envelope = envelope_type(**payload)
        getattr(envelope, field)[b'non_json_key'] = True
        with pytest.raises(ValueError):
            envelope_type.model_validate(dict(envelope))
    else:
        payload[field][b'non_json_key'] = True
        with pytest.raises(ValueError):
            envelope_type(**payload)


def test_application_rejects_replay_with_mutated_bytes_key():
    replay = TurnReplay(session_id='s', turn_id='t', response={
        'request_id': 'r', 'session_id': 's', 'turn_id': 't',
        'intent': 'shop', 'status': 'clarify',
    })
    replay.response[b'question'] = 'non-json-key'
    sessions = SessionSaveSpy(replay)
    with pytest.raises(ValueError):
        asyncio.run(application(sessions, None).turn(
            make_principal('test', 'a'), 's', TurnRequest(turn_id='t'),
        ))
    assert sessions.completions == []


@pytest.mark.parametrize('field', ['session_id', 'turn_id'])
def test_application_rejects_replay_envelope_with_wrong_owner(field):
    payload = {
        'request_id': 'r', 'session_id': 's', 'turn_id': 't',
        'intent': 'shop', 'status': 'clarify',
    }
    replay = TurnReplay(session_id='s', turn_id='t', response=payload)
    setattr(replay, field, 'different')
    sessions = SessionSaveSpy(replay)
    with pytest.raises(CommerceError, match='invalid_completion'):
        asyncio.run(application(sessions, None).turn(
            make_principal('test', 'a'), 's', TurnRequest(turn_id='t'),
        ))
    assert sessions.completions == []
