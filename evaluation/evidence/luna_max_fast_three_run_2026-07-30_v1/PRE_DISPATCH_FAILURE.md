# Pre-dispatch harness failure

The v1 harness stopped while converting the Codex app-server
`ModelServiceTier` capability object into CERA JSON. No Reasoner, Composer, or
Verifier dispatch started. The summary records zero samples, zero retry,
zero fallback, and zero story commit.

This is a harness serialization failure, not a provider/API or story-pipeline
result. It is preserved unchanged except for this explanatory sidecar. The
corrected test uses a new v2 evidence identity and explicit primitive
capability fields.
