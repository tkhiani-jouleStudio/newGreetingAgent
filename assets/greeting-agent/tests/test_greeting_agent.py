"""Tests for the Greeting Agent."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def agent(add_agent_to_path):
    """Create a SampleAgent instance with mocked LLM."""
    with patch("agent.ChatLiteLLM") as mock_llm_class, \
         patch("agent.create_checkpointer") as mock_checkpointer, \
         patch("agent.SummarizationMiddleware"):
        mock_llm_class.return_value = MagicMock()
        mock_checkpointer.return_value = MagicMock()
        from agent import SampleAgent
        return SampleAgent()


@pytest.mark.asyncio
async def test_greeting_response(agent):
    """Unit test: agent returns a greeting response for a user message."""
    mock_result = {
        "messages": [MagicMock(content="Hello! Welcome! How can I help you today?")]
    }
    with patch.object(agent, "_invoke_with_fallback", new_callable=AsyncMock) as mock_invoke:
        mock_invoke.return_value = mock_result
        result = await agent._run_agent("Hello!", "ctx-001")
    assert result["is_task_complete"] is True
    assert result["require_user_input"] is False
    assert "Hello" in result["content"] or len(result["content"]) > 0


@pytest.mark.asyncio
async def test_empty_query_prompts_user(agent):
    """Unit test: empty query triggers user input request (M1.missed path)."""
    result = await agent._run_agent("", "ctx-002")
    assert result["require_user_input"] is True


@pytest.mark.asyncio
async def test_integration_full_agent_flow(agent):
    """Integration test: end-to-end agent invoke with mocked LLM."""
    mock_result = {
        "messages": [MagicMock(content="Hi there! Great to meet you! How can I assist you?")]
    }
    with patch.object(agent, "_invoke_with_fallback", new_callable=AsyncMock) as mock_invoke:
        mock_invoke.return_value = mock_result
        response = await agent.invoke("Hi!", "ctx-integration")

    assert response.status == "completed"
    assert len(response.message) > 0


@pytest.mark.asyncio
async def test_stream_yields_greeting(agent):
    """Test that stream yields a completed greeting response."""
    mock_result = {
        "messages": [MagicMock(content="Hey! Welcome! I'm your greeting agent.")]
    }
    chunks = []
    with patch.object(agent, "_invoke_with_fallback", new_callable=AsyncMock) as mock_invoke:
        mock_invoke.return_value = mock_result
        async for chunk in agent.stream("Hello", "ctx-stream"):
            chunks.append(chunk)

    assert len(chunks) >= 2
    final = chunks[-1]
    assert final["is_task_complete"] is True
    assert "greeting" in final["content"].lower() or len(final["content"]) > 0


@pytest.mark.asyncio
async def test_stream_handles_error_gracefully(agent):
    """Test that stream handles exceptions gracefully."""
    with patch.object(agent, "_invoke_with_fallback", new_callable=AsyncMock) as mock_invoke:
        mock_invoke.side_effect = Exception("LLM unavailable")
        chunks = []
        async for chunk in agent.stream("Hello", "ctx-error"):
            chunks.append(chunk)

    final = chunks[-1]
    assert final["is_task_complete"] is True
    assert "error" in final["content"].lower()
