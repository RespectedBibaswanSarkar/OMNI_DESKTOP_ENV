# General-purpose task execution

How OMNI turns an arbitrary request into a verified result. Everything here extends the existing
pipeline (`omni/pipeline.py`) and desktop layer (`omni/desktop/`); IARS routing, AMPL, KRLS, plan memory,
the action allowlist and the confirmation tiers are unchanged.

## Planning
* `AppIndex.rank_for_task()` ranks installed apps against the goal using only live `.desktop` metadata
  (name, generic name, comment, keywords, categories) plus the optional embedder. There is no task taxonomy.
  The ranking only fills the planner's catalogue; launching still goes through `app.launch` and its validation.
* The desktop planner prompt is backend-neutral (it names the active backend, not a compositor) and allows up to
  `MAX_PLAN_STEPS` (12) steps per round. Follow-up rounds (`max_followups`) and replans extend longer workflows.
* Plans may mix desktop actions, file steps (`mkdir`/`write_file`/`write_json`) and `reply` steps.
* Steps may carry `id` and `depends_on`. `_sanitize_steps` keeps a step only if it validates and every dependency
  is an earlier kept step. At run time a step whose dependency failed is **skipped and reported**, never run.
* A real (non-null) desktop backend selects the desktop planner even if IARS routed to a file/document agent.

## Verification
* Execution success is not task success. `verify()` results can be `conclusive: False` (e.g. a browser process is
  alive but no window title matched); such steps pass (they are not failures) but are listed in
  `verification["unconfirmed"]`, and `verification["complete"]` is `False` unless the critic model confirms them.
  Read-only actions never count as unconfirmed.
* Written artifacts are parsed by type (`json`, `py`, `toml`, `xml`/`svg`; non-empty for text-like formats) and
  reported as `valid_format` checks. Unknown formats get no format check.
* `execution["artifacts"]` lists produced files with absolute paths and sizes; the result text repeats them,
  and states unconfirmed or skipped steps explicitly.
* Plans that were not fully confirmed are not stored in plan memory.

## Safety (unchanged, relied upon)
Model output is validated against `DesktopActionRegistry` (allowlisted actions, argument validation, path
containment). Reversible actions need `auto_approve_reversible` or user approval; consequential ones always
need approval. Nothing in this change executes model-authored shell commands.

## Limitations
* Dependencies reflect *execution* success; a desktop step that executes but fails its later post-condition is
  handled by the existing verify/replan loop, not by skipping dependents.
* Without a window-tracking layer (no AT-SPI/compositor data) many navigation results remain unconfirmed.
* Format validation covers only the types listed above.
