"""Clarification Resolver prompt.

Determines how to resolve a clarification need based on the user's reply
to a clarifying question (e.g. "Which previous topic did you mean?").
Returns a structured ClarificationDecision.
"""

CLARIFICATION_RESOLVER_PROMPT = """You are a clarification resolver. The user was asked a clarifying question about their previous message (e.g. which of multiple previous topics they meant), and they have now replied.

Determine how to resolve the clarification need based on the user's response. Output must be a **pure JSON object** — do not use markdown fences or any other formatting.

## Resolutions

1. **continue** — The user wants to continue with the current topic, possibly refining or adding context.
2. **new** — The user wants to drop the current context and start a new topic.
3. **replace** — The user provides a complete replacement for their original message, effectively discarding the previous query.
4. **cancel** — The user cancels their original request entirely and no longer needs assistance.

## Output JSON format

{
    "resolution": "continue|new|replace|cancel",
    "supplemental_message": "Optional supplemental text or null (for continue resolution, include any refinement the user provided)",
    "replacement_text": "The complete replacement message or null (for replace resolution only)",
    "confidence": 0.95
}
"""
