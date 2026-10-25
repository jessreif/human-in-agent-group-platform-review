# Prompts

`PROMPT_BUILDER_VERSION` in `backend/app/services/prompts.py` is "grounded_prompts_v7".

Social replies are assembled by `render_social` and `build_request`.
The assistant prompt is `render_assistant`. Task solutions are not put in the social prompt.

Decoding constants live in `backend/app/services/generation_policy.py`:
temperature 0.2, max_tokens 400, reasoning_effort none_when_supported.
top_p, top_k, context length, and stop sequences are not set there.
