from types import SimpleNamespace

from api.services.pipecat.event_handlers import _start_opening_needs_fetch_context


def _engine(*, greeting=None, prompt=None, context=None, start_id="start"):
    node = SimpleNamespace(greeting=greeting, prompt=prompt)
    workflow = SimpleNamespace(start_node_id=start_id, nodes={start_id: node})
    return SimpleNamespace(workflow=workflow, _call_context_vars=context or {})


def test_static_greeting_does_not_need_fetch_context():
    engine = _engine(greeting="Thanks for calling, how can I help?")
    assert _start_opening_needs_fetch_context(engine) is False


def test_templated_greeting_needs_missing_var():
    engine = _engine(greeting="Hi {{customer_name}}, this is Sam.")
    assert _start_opening_needs_fetch_context(engine) is True


def test_templated_greeting_already_in_context_does_not_wait():
    engine = _engine(
        greeting="Hi {{customer_name}}, this is Sam.",
        context={"customer_name": "Jane"},
    )
    assert _start_opening_needs_fetch_context(engine) is False


def test_prompt_checked_when_no_greeting():
    engine = _engine(prompt="You are calling {{account_id}} about a bill.")
    assert _start_opening_needs_fetch_context(engine) is True
