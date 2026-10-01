"""MCP tool loader.

Owned indirection layer between agent code and the Agent Gateway.
All agent code imports get_mcp_tools from here.

Behaviour is controlled by the IBD_TESTING environment variable:

  Production (IBD_TESTING not set):
      Uses Agent Gateway client directly from the SDK to connect via mTLS.
      Credentials are loaded from the UMS volume mount (/etc/ums/credentials/credentials)
      or the AGW_CREDENTIALS_JSON environment variable.

  Local / test mode (IBD_TESTING=1):
      Reads mcp-mock.json from the directory containing this file's parent
      (i.e. <asset-root>/mcp-mock.json) and returns LangChain StructuredTool
      instances built from the mock data — no network calls.
"""

import base64
import json
import logging
import os
import time
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any, Optional

from sap_cloud_sdk.agentgateway import create_client
from pydantic import create_model
from langchain_core.tools import StructuredTool

from util import enhance_tool_description, enhance_tool_name, call_mcp_tool_with_retry

logger = logging.getLogger(__name__)

# Context variable to pass user token from request to tool execution
# This allows cached tools to access per-request user credentials
_user_token_context: ContextVar[str | None] = ContextVar('user_token', default=None)

# Reusable AGW client for connection pooling
_agw_client: Optional[Any] = None

# mcp-mock.json lives at the asset root (one level above app/)
_MOCK_FILE = Path(__file__).parent.parent / "mcp-mock.json"


def _build_mock_tools() -> list:
    """Build LangChain StructuredTool instances from mcp-mock.json.

    Returns an empty list (without error) when mcp-mock.json is absent or
    cannot be parsed — add/fix the file to enable tool mocking.
    """
    if not _MOCK_FILE.exists():
        return []

    try:
        mock_data = json.loads(_MOCK_FILE.read_text())
    except Exception:
        logger.warning(
            "Failed to parse mcp-mock.json at %s — returning empty tool list",
            _MOCK_FILE,
            exc_info=True,
        )
        return []

    tools = []

    from langchain_core.tools import StructuredTool
    from pydantic import Field, create_model

    for _server_slug, server in mock_data.get("servers", {}).items():
        for tool_name, tool_def in server.get("tools", {}).items():
            description = tool_def.get("description", "")
            mock_response = tool_def.get("mock_response", {})
            input_schema = tool_def.get("input_schema", {})

            props = input_schema.get("properties", {})
            required_fields = set(input_schema.get("required", []))
            field_definitions: dict = {}
            for field_name, field_info in props.items():
                json_type = field_info.get("type", "string")
                if json_type == "integer":
                    python_type = int
                elif json_type == "number":
                    python_type = float
                elif json_type == "boolean":
                    python_type = bool
                else:
                    python_type = str

                if field_name in required_fields:
                    field_definitions[field_name] = (
                        python_type,
                        Field(description=field_info.get("description", "")),
                    )
                else:
                    field_definitions[field_name] = (
                        python_type,
                        Field(
                            default=None, description=field_info.get("description", "")
                        ),
                    )

            args_schema = (
                create_model(f"{tool_name}_args", **field_definitions)
                if field_definitions
                else create_model(f"{tool_name}_args")
            )
            _response = json.dumps(mock_response)

            async def _coroutine(_resp=_response, **kwargs) -> str:
                return _resp

            tools.append(
                StructuredTool(
                    name=tool_name,
                    description=description,
                    args_schema=args_schema,
                    coroutine=_coroutine,
                    # Catch ToolException and forward it to the LLM as an error
                    # message rather than propagating as a Python exception.
                    handle_tool_error=True,
                )
            )

    logger.info("Loaded %d mock MCP tool(s) from %s", len(tools), _MOCK_FILE)
    return tools


def _convert_mcp_tool_to_langchain(mcp_tool: Any, agw_client: Any) -> StructuredTool:
    """
    Convert an MCP tool to a LangChain StructuredTool.

    Args:
        mcp_tool: The MCP tool to convert (MCPTool object from SDK)
        agw_client: Agent Gateway client for tool execution

    Returns:
        LangChain StructuredTool

    Raises:
        ValueError: If mcp_tool is None

    Note:
        Uses the SDK's namespaced_name property (format: 'server_name__tool_name')
        to prevent naming conflicts when multiple MCP servers provide tools
        with the same name.

        User authentication: The user token is retrieved from the context variable
        _user_token_context at call time, allowing these cached tools to use
        per-request credentials without being recreated for each user.
    """
    if mcp_tool is None:
        raise ValueError("mcp_tool parameter cannot be None")

    async def run(**kwargs) -> str:
        """Execute the MCP tool via Agent Gateway client with retry logic.

        Retrieves the user token from the context variable set by agent_executor.
        """
        user_token = _user_token_context.get()
        return await call_mcp_tool_with_retry(agw_client, mcp_tool, user_token=user_token, **kwargs)

    # Build args schema from input_schema
    properties = mcp_tool.input_schema.get("properties", {})
    required = set(mcp_tool.input_schema.get("required", []))

    fields = {}
    for name, prop in properties.items():
        # Map JSON schema types to Python types
        prop_type = prop.get("type", "string")
        python_type = str  # Default to string
        if prop_type == "integer":
            python_type = int
        elif prop_type == "number":
            python_type = float
        elif prop_type == "boolean":
            python_type = bool

        # Required fields use ... (Ellipsis), optional use None default
        if name in required:
            fields[name] = (python_type, ...)
        else:
            fields[name] = (python_type | None, None)

    args_schema = create_model(f"{mcp_tool.name}_args", **fields) if fields else None

    # Enhance description and name with server context
    enhanced_description = enhance_tool_description(mcp_tool)
    namespaced_tool_name = enhance_tool_name(mcp_tool)

    return StructuredTool.from_function(
        coroutine=run,
        name=namespaced_tool_name,
        description=enhanced_description,
        args_schema=args_schema,
        # Catch ToolException raised inside `run` (e.g. after all retries are
        # exhausted) and forward it to the LLM as a ToolMessage error string.
        # This prevents the LLM from hallucinating results when the real MCP
        # call fails.
        handle_tool_error=True,
    )


async def get_mcp_tools(user_token: str | None) -> list:
    """Return LangChain-compatible MCP tools.

    In local/test mode (IBD_TESTING=1): returns mock tools from mcp-mock.json.
    In production: uses Agent Gateway client directly from SDK to connect via mTLS.

    IMPORTANT: Both tool listing and tool calling require user credentials.
    The user_token parameter is passed explicitly to ensure it cannot be forgotten.

    Note: Tools are fetched per-request since tool listings are user-specific.
    Each user may have access to different tools based on their permissions.

    Args:
        user_token: User authentication token (required in production) for listing and calling tools.
                    In local testing mode (IBD_TESTING=1), this can be None or empty.

    Returns:
        List of LangChain StructuredTool objects for the current user

    Raises:
        ValueError: If user_token is None or empty string in production mode
    """
    global _agw_client

    # In local/test mode, return mock tools without validating user_token
    if os.environ.get("IBD_TESTING") == "1":
        return _build_mock_tools()

    # Validate user_token is provided and non-empty in production mode
    if not user_token:
        raise ValueError("user_token is required for listing and calling MCP tools")

    try:
        # Reuse AGW client for connection pooling (mTLS is expensive to establish)
        if _agw_client is None:
            _agw_client = create_client()
            logger.info("Agent Gateway client created successfully")

        agw_client = _agw_client

        # Get MCP tools from Agent Gateway with user token
        logger.info("Listing MCP tools with user credentials")
        mcp_tools = await agw_client.list_mcp_tools(user_token=user_token)

        if not mcp_tools:
            logger.warning("Agent Gateway returned 0 tools - MCP servers may not be available")
            return []

        logger.info(f"Successfully retrieved {len(mcp_tools)} tool(s) from Agent Gateway")

        # Convert to LangChain tools (they retrieve user token at call time from context)
        langchain_tools = []
        for mcp_tool in mcp_tools:
            try:
                langchain_tool = _convert_mcp_tool_to_langchain(mcp_tool, agw_client)
                langchain_tools.append(langchain_tool)
            except Exception as e:
                logger.warning(f"Failed to convert tool '{mcp_tool.name}': {e}")
                # Continue with other tools

        # Return empty list when no tools were successfully converted
        if not langchain_tools:
            logger.warning("No tools were successfully converted - returning empty list")
            return []

        return langchain_tools

    except Exception as e:
        logger.exception("Failed to load MCP tools from Agent Gateway")
        # Reset client on failure to force reconnection on next attempt
        _agw_client = None
        return []

def set_user_token(user_token: str | None) -> Token:
    """Set the user token for MCP tool calls in the current async context.

    This must be called before invoking any tools to ensure they use the correct
    user credentials. The token is stored in a context variable that is automatically
    isolated per async task/request.

    IMPORTANT: Always reset the token after use to prevent cross-request contamination:
        token_ctx = set_user_token(user_token)
        try:
            # ... use tools ...
        finally:
            reset_user_token(token_ctx)

    Args:
        user_token: The user's authentication token, or None to clear it

    Returns:
        Token object that must be passed to reset_user_token() to restore
        the previous value
    """
    if user_token:
        logger.debug("User token set for tool execution")
    else:
        logger.debug("User token cleared for tool execution")
    return _user_token_context.set(user_token)


def reset_user_token(token: Token) -> None:
    """Restore the user token context to its previous value.

    Args:
        token: The Token returned by a prior set_user_token() call. Passing it to
            ContextVar.reset() unwinds the context stack to the value that was in
            effect before that set_user_token() call, rather than leaving a stale
            or None value behind.
    """
    _user_token_context.reset(token)
    logger.debug("User token context reset to previous value")


def get_user_token() -> str | None:
    """Get the current user token from the async context.

    Returns:
        The user token string, or None if not set
    """
    return _user_token_context.get()


def get_user_sub() -> str:
    """Extract the JWT subject claim from the current request's token.

    Decodes the JWT payload (middle segment) without verifying the signature —
    The platform has already verified it before the request reaches this code.

    Returns:
        The 'sub' claim from the token.

    Raises:
        ValueError: If the token is missing or the sub claim cannot be extracted,
                    unless IBD_TESTING=1, in which case returns 'unknown'.
    """
    token = _user_token_context.get()
    if not token:
        if os.environ.get("IBD_TESTING") == "1":
            return "unknown"
        raise ValueError("No user token in context — cannot extract sub claim")

    try:
        payload_segment = token.split(".")[1]
        # Add padding if needed
        padding = 4 - len(payload_segment) % 4
        if padding != 4:
            payload_segment += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_segment))
        sub = payload.get("sub")
        if not sub:
            raise ValueError("JWT payload contains no 'sub' claim")
        return sub
    except (IndexError, ValueError):
        raise
    except Exception as e:
        raise ValueError(f"Failed to decode JWT payload: {e}") from e
