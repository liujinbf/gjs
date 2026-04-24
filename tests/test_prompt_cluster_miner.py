import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from prompt_cluster_miner import PROMPT_LLM_CLUSTER_LOSS, PROMPT_LLM_GOLDEN_SETUP


def test_cluster_loss_prompt_format_renders_without_key_error():
    rendered = PROMPT_LLM_CLUSTER_LOSS.format(
        regime_tag="trend_expansion",
        symbol="XAUUSD",
        count=3,
        transactions_text="[2026-04-16 10:00:00] MFE:1.00%, MAE:3.00%",
    )

    assert '"category": "risk"' in rendered
    assert '"asset_scope": "XAUUSD"' in rendered
    assert '"logic": {' in rendered
    assert '{"field": "signal_side"' in rendered
    assert "{regime_tag}" not in rendered


def test_golden_setup_prompt_format_renders_without_key_error():
    rendered = PROMPT_LLM_GOLDEN_SETUP.format(
        regime_tag="trend_expansion",
        symbol="XAUUSD",
        count=2,
        transactions_text="[2026-04-16 11:00:00] MFE:4.00%, MAE:1.00%",
    )

    assert '"category": "entry"' in rendered
    assert '"asset_scope": "XAUUSD"' in rendered
    assert '"logic": {' in rendered
    assert '{"field": "trade_grade"' in rendered
    assert "{symbol}" not in rendered
