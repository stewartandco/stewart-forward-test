from pipeline.registry import edge_numbers, edge_label


def test_numbers_follow_chain_order_one_based():
    entries = [
        {"entry_type": "strategy_registered", "payload": {"strategy_id": "aaa"}},
        {"entry_type": "verdict", "payload": {"strategy_id": "aaa"}},
        {"entry_type": "strategy_registered", "payload": {"strategy_id": "bbb"}},
    ]
    assert edge_numbers(entries) == {"aaa": 1, "bbb": 2}


def test_label_is_hash_prefixed_zero_padded_and_grows():
    assert edge_label(7) == "#0007"
    assert edge_label(123456) == "#123456"
