# OpenClaw Critic Prompt (v1)

Review the following social media post draft against the brand guidelines and risk policy.

Draft:
{{ draft }}

Brand Guidelines:
{{ brand_profile }}

Risk Policy:
- No sensitive PII.
- No forbidden claims: {{ daily_input.forbidden_claims }}
- Accuracy: Does it reflect the evidence?

Output your review in JSON:
{
  "approved": boolean,
  "risk_score": float (0-1),
  "feedback": "string",
  "revisions_needed": ["list of strings"]
}
