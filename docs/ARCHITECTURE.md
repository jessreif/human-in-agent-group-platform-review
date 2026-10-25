# Architecture

Four processes, from `docker-compose.yml`. The model service is optional for reading the code and is required only if you want generated replies. This source tree was not booted for the documentation.

```mermaid
flowchart LR
  browser[Browser]
  ui[investigators-ui nginx]
  api[investigators-api FastAPI]
  worker[investigators-worker]
  db[(PostgreSQL)]
  ollama[Ollama gemma4:26b]
  browser --> ui
  ui --> api
  api --> db
  worker --> db
  worker --> ollama
```

State that survives a restart is in Postgres: the session row, the event log, scheduled jobs, and model-call records. The browser holds a view. It does not decide the next stage. `backend/app/domain/stage_graph.py` is the allowed transition set. The stage names are `STAGES` in `backend/app/db/schema.py`.

```mermaid
flowchart TD
  entry --> consent
  consent --> instructions
  consent --> declined
  instructions --> training
  training --> ai_qa
  training --> scripted_connecting
  ai_qa --> scripted_connecting
  scripted_connecting --> ready
  ready --> task
  ready --> solo_review
  solo_review --> task
  task --> checkin_collect
  checkin_collect --> checkin_wait
  checkin_wait --> checkin_reveal
  checkin_reveal --> task
  task --> answers_locked
  answers_locked --> confidence
  confidence --> post_questionnaire
  post_questionnaire --> open_ended
  open_ended --> debrief
  post_questionnaire --> debrief
  debrief --> data_use_choice
  data_use_choice --> completion
```

Peer evaluation uses `peer_ratings` and does not use the check-in. That branch is omitted from the diagram so the time-pressure path stays readable. The code, not this picture, is the authority. Some arrows depend on feature flags.

One teammate turn:

```mermaid
sequenceDiagram
  participant Clock as Scheduler
  participant Class as engines
  participant Prompt as prompts
  participant Model as Provider
  participant Log as Postgres
  Clock->>Class: job is due
  Class->>Class: is_automation_probe or route the ask
  alt probe
    Class->>Log: identity_line, no model call
  else generation
    Class->>Prompt: render_social / build_request
    Prompt->>Model: local call, or skip if provider is none
    Model->>Class: validate_teammate_output
    Class->>Log: deliver model text or the bank line
  end
```

**Implemented** as the shape of the code. Not executed as a live session for this documentation.
