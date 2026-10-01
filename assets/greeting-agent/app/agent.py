import logging
from dataclasses import dataclass
from typing import Any, AsyncGenerator, Literal, Sequence

from opentelemetry import trace

from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langchain_litellm import ChatLiteLLM
from langgraph.graph.state import CompiledStateGraph
from litellm.exceptions import APIConnectionError, APIError, Timeout
from sap_cloud_sdk.agent_decorators import agent_config, agent_model, prompt_section
try:
    from sap_cloud_sdk.agent_memory.factory.langgraph_checkpoint import create_checkpointer
except ImportError:
    from langgraph.checkpoint.memory import MemorySaver
    def create_checkpointer(ttl_seconds=None):
        return MemorySaver()

from mcp_tools import get_user_sub

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


@agent_model(
    key="config.model",
    label="LLM Model",
    description="The language model powering this agent",
)
def get_model_name() -> str:
    return "sap/anthropic--claude-4.5-sonnet"


@agent_model(
    key="config.fallback_model",
    label="Fallback LLM Model",
    description="Fallback model used when the primary model is unavailable. Leave empty to disable fallback.",
)
def get_fallback_model_name() -> str:
    return ""


@agent_config(
    key="config.temperature",
    label="LLM Temperature",
    description="Controls randomness of responses (0.0 = deterministic, 1.0 = creative)",
)
def get_temperature() -> float:
    return 0.0

@agent_config(
    key="config.checkpointer.ttl_seconds",
    label="Thread TTL (seconds)",
    description="Evict inactive conversation threads after this period of "
                "inactivity. Set to 0 to disable eviction.",
)
def thread_ttl_seconds() -> int:
    return 3600 # 1 hour

@prompt_section(
    key="prompts.system",
    label="System Prompt",
    description="The full system prompt defining the agent's role and behavior",
    validation={"format": "markdown", "max_length": 5000},
)
def get_system_prompt() -> str:
    return """You are a friendly greeting agent. When a user sends you a message, greet them warmly and naturally. Make the user feel welcomed. After the greeting, remain available for any follow-up questions or conversation.\n\nIMPORTANT: Never fabricate, guess, or invent data. Be genuine and conversational."""


@dataclass
class AgentResponse:
    status: Literal["input_required", "completed", "error"]
    message: str


class SampleAgent:
    SUPPORTED_CONTENT_TYPES = ["text", "text/plain"]

    def __init__(self):
        ttl = thread_ttl_seconds()
        self._primary_model = get_model_name()
        self._fallback_model = get_fallback_model_name().strip()
        self._temperature = get_temperature()

        self.llm = ChatLiteLLM(model=self._primary_model, temperature=self._temperature)
        self._fallback_llm = (
            ChatLiteLLM(model=self._fallback_model, temperature=self._temperature)
            if self._fallback_model
            else None
        )

        self._checkpointer = create_checkpointer(ttl_seconds=ttl or None)
        self._summarization_middleware = SummarizationMiddleware(
            model=self.llm,
            trigger=("tokens", 100_000),
            keep=("messages", 4),
        )

    def _create_graph(
        self,
        llm: ChatLiteLLM,
        tools: Sequence[BaseTool],
        system_prompt: str,
    ) -> CompiledStateGraph:
        """Create a LangGraph agent with the specified LLM."""
        return create_agent(
            llm,
            tools=list(tools),
            system_prompt=system_prompt,
            checkpointer=self._checkpointer,
            middleware=[self._summarization_middleware],
        )

    async def _invoke_with_fallback(
        self,
        tools: Sequence[BaseTool],
        system_prompt: str,
        query: str,
        context_id: str,
    ) -> dict[str, Any]:
        """Invoke the agent and fall back only for transient LLM failures."""
        config = {"configurable": {"thread_id": f"{get_user_sub()}:{context_id}"}}
        messages = {"messages": [HumanMessage(content=query)]}

        try:
            graph = self._create_graph(self.llm, tools, system_prompt)
            return await graph.ainvoke(messages, config)
        except (APIConnectionError, APIError, Timeout) as primary_error:
            if not self._fallback_llm:
                raise

            logger.warning(
                "Primary model '%s' failed. Retrying with fallback model '%s'. Error: %s",
                self._primary_model,
                self._fallback_model,
                primary_error,
            )

        graph = self._create_graph(self._fallback_llm, tools, system_prompt)
        result = await graph.ainvoke(messages, config)
        logger.info(
            "Request completed with fallback model '%s' after primary model '%s' failed.",
            self._fallback_model,
            self._primary_model,
        )
        return result

    async def _run_agent(
        self,
        query: str,
        context_id: str,
        tools: Sequence[BaseTool] | None = None,
    ) -> dict[str, Any]:
        """Execute agent business logic with milestone instrumentation.

        Args:
            query: User query to process
            context_id: Context identifier for the conversation
            tools: Optional sequence of LangChain tools.

        Returns:
            Dict with is_task_complete, require_user_input, and content keys.
        """
        # M1: User Initiates Interaction
        with tracer.start_as_current_span("M1_user_interaction_received"):
            if query:
                logger.info("M1.achieved: user interaction received")
            else:
                logger.warning("M1.missed: no user message received")
                return {
                    "is_task_complete": True,
                    "require_user_input": True,
                    "content": "I didn't receive a message. Please say something!",
                }

        # M2: Agent Recognizes User / Context
        with tracer.start_as_current_span("M2_user_context_identified"):
            try:
                user_sub = get_user_sub()
                logger.info("M2.achieved: user context identified (sub=%s)", user_sub or "anonymous")
            except Exception:
                logger.warning("M2.missed: user context could not be established")

        # M3: Generate and deliver greeting
        with tracer.start_as_current_span("M3_greeting_delivered"):
            system_prompt = get_system_prompt()
            if not tools:
                system_prompt += "\n\nIMPORTANT: No tools are currently available. Do not attempt to call any tools."

            tool_names = [tool.name for tool in tools] if tools else []
            logger.info("Running agent with %d tool(s): %s", len(tool_names), tool_names)

            try:
                result = await self._invoke_with_fallback(
                    tools=tools or [],
                    system_prompt=system_prompt,
                    query=query,
                    context_id=context_id,
                )
                response = result["messages"][-1].content
                logger.info("M3.achieved: greeting delivered to user")
            except Exception:
                logger.error("M3.missed: greeting delivery failed")
                raise

        # M4: Follow-up readiness (mark conversation as ongoing)
        with tracer.start_as_current_span("M4_followup_ready"):
            logger.info("M4.achieved: follow-up interaction handled")

        return {
            "is_task_complete": True,
            "require_user_input": False,
            "content": response,
        }

    async def stream(
        self,
        query: str,
        context_id: str,
        tools: Sequence[BaseTool] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Stream agent responses.

        Args:
            query: User query to process
            context_id: Context identifier for the conversation
            tools: Optional sequence of LangChain tools. If None or empty, agent runs without tools.

        Yields:
            Status updates and final response with structure:
            - is_task_complete: Whether the task is complete
            - require_user_input: Whether user input is needed
            - content: The response content or status message
        """
        yield {
            "is_task_complete": False,
            "require_user_input": False,
            "content": "Processing...",
        }

        try:
            result = await self._run_agent(query, context_id, tools=tools)
            yield result
        except Exception:
            logger.exception("Agent stream() failed")
            yield {
                "is_task_complete": True,
                "require_user_input": False,
                "content": "I encountered an error while processing your request. Please try again.",
            }

    async def invoke(
        self,
        query: str,
        context_id: str,
        tools: Sequence[BaseTool] | None = None,
    ) -> AgentResponse:
        """Invoke agent and return final response.

        Args:
            query: User query to process
            context_id: Context identifier for the conversation
            tools: Optional sequence of LangChain tools. If None or empty, agent runs without tools.

        Returns:
            AgentResponse with status and message
        """
        last: dict = {}
        async for chunk in self.stream(query, context_id, tools=tools):
            last = chunk
        if last.get("is_task_complete"):
            return AgentResponse(status="completed", message=last["content"])
        if last.get("require_user_input"):
            return AgentResponse(status="input_required", message=last["content"])
        return AgentResponse(
            status="error", message=last.get("content", "Unknown error")
        )
