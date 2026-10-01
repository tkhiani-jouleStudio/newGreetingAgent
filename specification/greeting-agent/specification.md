# Specification: greeting-agent

> **Guidelines**: Read [guidelines.md](../guidelines.md) and [guidelines-agent.md](../guidelines-agent.md) before executing ANY tasks below. Follow all constraints described there throughout execution.

## Basic Setup

- [ ] Read the project input (`product-requirements-document.md`, `intent.md`)
- [ ] Bootstrap agent code in `assets/greeting-agent/` using skill `sap-agent-bootstrap` (invoke from inside `assets/greeting-agent/`, use copy commands — do NOT create files manually)
- [ ] Install dependencies, validate the agent starts and responds at `/.well-known/agent.json`

## Greeting Agent Implementation

- [ ] Update the agent system prompt in `app/agent.py` (`@prompt_section`) to instruct the agent to greet the user warmly and naturally when they send any message
- [ ] Ensure the agent responds with a friendly, personalized greeting on the first user message (R1: Greet the User on Interaction)
- [ ] Ensure the greeting tone is natural and conversational, not robotic (R2: Personalized Greeting)
- [ ] Ensure the agent remains responsive and can handle follow-up messages after the greeting (R3: Continue Conversation)
- [ ] No MCP tools or external SAP API integrations are required for this agent

## Business Step Instrumentation

- [ ] Implement milestone M1 (`M1.achieved: user interaction received` / `M1.missed: no user message received`) in the agent's message handling logic
- [ ] Implement milestone M2 (`M2.achieved: user context identified` / `M2.missed: user context could not be established`) after processing the incoming message
- [ ] Implement milestone M3 (`M3.achieved: greeting delivered to user` / `M3.missed: greeting delivery failed`) after the greeting response is generated
- [ ] Implement milestone M4 (`M4.achieved: follow-up interaction handled` / `M4.missed: follow-up interaction not handled`) for subsequent user messages after the initial greeting
- [ ] Add OpenTelemetry custom spans for each milestone using the **decorator or context manager form** on non-generator async methods. Extract business logic from `stream()` into a `_run_agent()` helper and instrument that — never wrap `yield` inside a span context
- [ ] Verify `auto_instrument()` is called at the top of `main.py` before any AI framework imports

## Cleanup & Validation

- [ ] Delete the template runtime skill: `rm -rf assets/greeting-agent/app/skills/template-skill/`
- [ ] Verify `assets/greeting-agent/app/agent.py` has exactly 5 decorated functions: run `grep -c "^@agent_model\|^@agent_config\|^@prompt_section" assets/greeting-agent/app/agent.py` and confirm it returns 5

## Testing

- [ ] `conftest.py` only sets `IBD_TESTING=true`
- [ ] Write one unit test for the greeting response logic in `assets/greeting-agent/tests/`
- [ ] Write one integration test executing the full end-to-end agent flow (mock the LLM — AI Core credentials not available in tests)
- [ ] Run `pytest` from `assets/greeting-agent/` (no args) — if coverage < 70%, add tests until threshold met
- [ ] Run `pytest` again from `assets/greeting-agent/` (no args) to generate final `test_report.json`
- [ ] Verify `test_report.json` exists in `assets/greeting-agent/`
