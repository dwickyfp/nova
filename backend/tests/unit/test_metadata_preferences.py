import pytest

from app.common.preferences import PreferenceChange, PreferenceRepository


@pytest.mark.parametrize("revision", [-1, True, 1.0, "1", 2**63 - 1])
def test_invalid_revision_is_rejected(revision):
    with pytest.raises(ValueError):
        PreferenceChange(revision, "synthetic-canary")


@pytest.mark.parametrize("value", ["\x00", "\ud800", "a" * 65534, 42, {}])
def test_invalid_value_is_rejected_without_readback(value):
    with pytest.raises(ValueError, match="Invalid preference value"):
        PreferenceChange(0, value)


@pytest.mark.parametrize(
    "revision,value,deleted", [(0, None, True), (1, "secret", True), (1, None, 1)]
)
def test_invalid_tombstone_is_rejected(revision, value, deleted):
    with pytest.raises(ValueError):
        PreferenceChange(revision, value, deleted)


def test_change_repr_omits_payload():
    assert "synthetic-canary" not in repr(PreferenceChange(1, "synthetic-canary"))


@pytest.mark.parametrize(
    "owner,keys",
    [
        ("", ["studio.theme"]),
        ("a" * 129, ["studio.theme"]),
        ("alice", []),
        ("alice", [""]),
        ("alice", ["a" * 129]),
        ("alice", ["a", "a"]),
        ("alice", [str(i) for i in range(101)]),
    ],
)
def test_invalid_or_unbounded_selection_is_rejected(owner, keys):
    with pytest.raises(ValueError):
        PreferenceRepository._validate(owner, keys)
