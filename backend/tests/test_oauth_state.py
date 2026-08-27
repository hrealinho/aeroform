from app.services.oauth_state import create_state, verify_state


def test_oauth_state_round_trip():
    state = create_state(42)
    assert verify_state(state) == 42
