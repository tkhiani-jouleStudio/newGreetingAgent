# Greeting Agent

A conversational AI agent that greets users when they interact with it.

## Business challenge

Build an AI agent that welcomes and greets users upon interaction, providing a friendly and personalized greeting experience.

## Key Milestones

1. **User Initiates Interaction** – User sends a message or starts a conversation with the agent.
2. **Agent Recognizes User** – Agent identifies the user and any contextual details available.
3. **Greeting Delivered** – Agent responds with a personalized, friendly greeting.
4. **Conversation Continues** – Agent remains available for follow-up interaction after the greeting.

## Business Architecture (RBA)

### End-to-End Process

Lead to Cash (E2E)

### Process Hierarchy

```
Lead to Cash
└── Manage Customers and Channels (generic)
    └── Manage customers (generic) [BPS-370]
        └── Manage customer experience
```

### Summary

A user-greeting AI agent maps to the "Manage Customer Experience" activity within the Lead to Cash process, covering real-time user engagement and conversational interaction.

## Fit Gap Analysis

| Requirement (business)         | Standard asset(s) found        | API ORD ID | MCP Server ORD ID | MCP Server Version | Gap? | Notes / assumptions |
| ------------------------------ | ------------------------------ | ---------- | ----------------- | ------------------ | ---- | ------------------- |
| Greet users on interaction     | No standard SAP product covers this as a standalone greeting agent | — | — | — | Yes | Custom AI agent required |
| Conversational AI runtime      | SAP AI Core (LLM runtime)      | — | — | — | No | Available on BTP |

### Key findings
- No standard SAP product provides a standalone greeting agent out of the box.
- SAP AI Core on BTP provides the LLM runtime needed to power the conversational greeting.
- The solution is lightweight — a simple pro-code Python agent is the best fit.
- No external MCP servers are required for a basic greeting use case.
- The agent can be extended later to handle more complex interactions beyond greetings.

## Recommendations

### Greeting AI Agent on SAP BTP

#### Executive Summary

Build a lightweight Python AI agent that greets users on SAP BTP.

#### Recommended Solution

A pro-code Python AI agent using SAP AI Core for LLM-powered greetings. The agent listens for incoming user messages and responds with a personalized greeting. Deployed on SAP BTP following the A2A protocol.

#### Recommended solution category

AI Agent

#### Intent fit
95%
