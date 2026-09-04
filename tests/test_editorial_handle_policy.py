from smartlib.editorial.policy import normalize_editorial_handle_policy


def test_editorial_handle_policy_reads_config_creator_shape():
    policy = normalize_editorial_handle_policy({
        "editorial": {"handle_policy": {"head": 12, "tail": "6"}}
    })
    assert (policy.head, policy.tail) == (12, 6)


def test_editorial_handle_policy_uses_safe_defaults():
    policy = normalize_editorial_handle_policy({
        "editorial": {"handle_policy": {"head": "bad", "tail": -3}}
    })
    assert (policy.head, policy.tail) == (8, 0)
