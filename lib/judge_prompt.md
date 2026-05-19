You are auditing one assistant turn for a specific failure mode. Read the prior user message, the assistant response, and the taxonomy below.

Output exactly one JSON object: {"label": "<class_name>" or null, "evidence_quote": "<≤30 word span from the assistant response>", "confidence": 0.0–1.0}

Output null label when no failure is present. Be conservative — false positives are worse than false negatives for our use case. confidence < 0.7 → output null label.

Taxonomy:
{TAXONOMY_JSON}

Prior user message:
{USER_TURN}

Assistant response under audit:
{ASSISTANT_TURN}
