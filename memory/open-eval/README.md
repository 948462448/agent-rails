# Agent Rails Memory Cards

Memory Cards are compact, verifiable project knowledge used by `agent-rails pack`.

## Rules

- Keep each card small: roughly 60-120 lines.
- Add `triggers`, `applies_to`, `staleness`, and `source` front matter.
- Include a `## Verify` section whenever the card makes an operational claim.
- Do not store secrets, cookies, tokens, account credentials, or full sensitive service responses.
- Treat `verify-first` cards as hypotheses until the verification step confirms them in the current checkout or environment.

## Local vs Online

These markdown files are the local fallback provider and reviewable seed data. An `OnlineMemoryProvider` such as OpenMemory can return the same logical `MemoryCard` shape from another service. Business projects should depend on the `MemoryProvider` abstraction, not on a specific online memory backend.

When a task produces a new memory candidate, update flow should be:

1. Deduplicate against existing cards.
2. Confirm the trigger, scope, staleness policy, and verification step.
3. Write a local card or update the online provider.
4. Regenerate a Task Pack to make sure the card is actually discoverable.
