# Model card

This document describes the language model that writes agent teammates' conversational replies: which model it is, how it is served, the decoding settings, what is and is not reproducible, and what is not pinned. It is for researchers who want to run the platform with the same model or interpret the model-generated text in the pilot data. Status labels are defined in `docs/README.md`.

The model only writes conversational replies. Task information, the timing of agent teammate actions, and answers to questions about whether a teammate is automated never come from the model (see `README.md`).

## Model and serving

| Item | Value | Status |
|---|---|---|
| Model tag bound by the AI profiles used with participants | `gemma4:26b` | **Implemented** in the profile bindings |
| Weight digest pinned on every model call | `sha256:5571076f3d70050487b26b341705799e0ab29b808164f90d20d4cf84f699d251` | **Implemented** as the pin. Not re-checked against a running Ollama server. |
| Server image | `ollama/ollama:0.32.1`, digest `sha256:6345fbc18bd73a1e16404be681dbc6fd291a027cab43ed541abe78c4c81051b0` | Set in `docker-compose.yml` |
| Quantization | Not stated | **Described only.** The code does not specify it. |
| Hardware | A GPU that can serve this model under Ollama. The inventory of the host machine is not included. | **Described only** |
| Commercial API adapters | Present in the code, but refused for session traffic | **Implemented** |

The model runs locally under Ollama. The local adapter (`backend/app/providers/local.py`) will not start without a pinned weight digest. The weights themselves are not included in this repository, and Gemma's published terms of use apply to them.

## Decoding settings

These values are set in `INFERENCE_POLICY` in `backend/app/services/generation_policy.py`.

| Setting | Value | Notes |
|---|---|---|
| Temperature | 0.2 | |
| Maximum tokens | 400 | |
| Seed | `derived_session_job_v1` | A seed derived from the session and job identifiers. The derived seed is stored with each model call. The platform does not replay generated wording from it, so the same seed is not a guarantee of the same text. |
| Reasoning effort | `none_when_supported` | Asks the server to skip the model's hidden reasoning ("thinking") tokens when the provider supports it. |
| top_p, top_k, num_ctx (context length), stop sequences | Not set | The Ollama server's and model's defaults apply. |

## Determinism

The task world's contents, the block order, the choice of bank lines, and the timing draws are all computed from seeds, so they can be regenerated. Model wording cannot. In addition, `world_id` is a random UUID that is not derived from the seed. `scripts/regenerate_task.py` hashes the world without that identifier.

## What is not pinned

- Quantization is not stated in the code.
- The decoding parameters listed as "Not set" above are left to the server's defaults.
- The weight digest is pinned in the code, but the pin has not been re-checked against a running Ollama server.
- The hardware the model ran on is not recorded here.
