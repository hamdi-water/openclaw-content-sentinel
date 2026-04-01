from openclaw_content_sentinel.config import AppConfig
from openclaw_content_sentinel.langgraph_workflow import (
    create_sentinel_graph,
    run_langgraph_pipeline,
)


def test_graph_compilation():
    graph = create_sentinel_graph()
    assert graph is not None

def test_initial_state_and_first_node(tmp_path):
    # Setup mock workspace
    (tmp_path / "data").mkdir()
    (tmp_path / "runs").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / ".env").write_text("OPENCLAW_SENTINEL_DATA_DIR=data\n")

    # Use from_env to get a valid config with defaults
    config = AppConfig.from_env(base_dir=tmp_path)

    result = run_langgraph_pipeline(
        config,
        prompt="Test Prompt",
        competitor_url="https://example.com/test",
        keywords=["test"],
        targets=["x"]
    )

    assert result["status"] == "langgraph_execution_started"
    assert "thread_id" in result
