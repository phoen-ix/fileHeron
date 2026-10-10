# Background jobs

Engineering deep dive, split out of `CLAUDE.md`. The root file keeps a short
summary and points here. Read this before changing the code listed below.

**Code:** `backend/app/workers/{worker,cron_dispatch}.py`, `backend/app/services/{cron_schedule,cron_tracker,job_queue}.py`

## Background jobs

ARQ worker (`workers/worker.py::WorkerSettings`), queue `fileheron:default`,
`max_tries=5`. → README §ARQ workers + cron for the full schedule. Cadences are
admin-tunable via `services/cron_schedule.py::REGISTRY` + the minute
`cron_dispatch`; all jobs idempotent.

- **`release_check` is DAILY** (1440-minute interval in `REGISTRY`), not hourly. Filter `RELEASE_TAG_RE`, exact match, drafts and prereleases skipped.
- **Cadence/enable/kind (`interval`|`daily`; daily uses the site timezone) are runtime-editable** via `cron.<name>.*` kv; defaults reproduce the historical cadence, so an upgrade is behaviour-neutral until edited. `REGISTRY` doubles as the **Run-now allowlist**.
- **`is_due` allows a job to be `_DUE_SLACK` (5s) EARLY, and that is what keeps cadences honest.** `cron_dispatch` ticks once a minute, takes `now` ONCE per tick and stores that same value as `last_run_at`, so the elapsed time it measures next tick is the tick SPACING - and whenever that landed a hair under the interval the job waited a whole further tick (a 1-minute job ran every 91s, hourly jobs at 3641s). **Do not scale the slack to a fraction of the tick**: half a tick (30s) would let a 1-minute job fire at 30s elapsed, halving the shortest cadence instead of steadying it.
- **A daily slot is built from the local DATE (fold=0) and compared in UTC** (`cron_schedule._daily_slot_utc`). `now_local.replace(hour=, minute=)` kept `now`'s fold, so on the night the clocks go back an 02:xx job ran twice, and inside the spring-forward gap the wall-clock check passed while the slot's UTC instant still lay ahead - due on every tick until then, 52 runs of a 02:51 job, possibly overlapping (`cron_dispatch` has no in-flight check). Six of the seven daily defaults sit in that hour. An ambiguous time is its FIRST occurrence, a nonexistent one moves forward by the gap; `test_cron_schedule.py` replays the dispatcher minute by minute across both transitions.
- **`cron_tracker._prune_old_runs` deletes SUCCESSES only past the per-job cap.** It runs on the success path alone, and `_KEEP_PER_JOB` is a flat 200 rows regardless of cadence (~3.3h for a 1-minute cron), so an unfiltered cap meant a job erased the evidence it was ever broken as soon as it recovered. Failures still age out via `_PRUNE_AFTER_DAYS` (30). **The admin "last 24h" counters are still structurally incomplete for the most frequent jobs** - 200 rows is under four hours of a 1-minute cron.
- **The status pages read every task at once**: `cron_tracker.latest_runs` (row_number per job over `started_at desc, id desc` - the id tiebreaker rule) and `run_counts_since` (one GROUP BY), plus `cron_schedule.snapshot` (every `cron.*` key in one read). `/admin/system/status` and `/admin/crons` ran ~2 and ~9 queries per task. `test_cron_status_queries.py` pins that both cost the same for 2 tasks as for the whole REGISTRY, and that `snapshot` agrees with the per-task `effective` the dispatcher still uses.
- **`mark_ran` persists BEFORE enqueue** - a failed commit retries next minute rather than enqueue-without-record. First sight seeds the clock (no thundering start after boot); `cron_dispatch` is deliberately NOT `@track_cron` (1440×/day would flood `cron_runs`).
- **`send_email_job`** resolves SMTP per job, retries transient, permanent 5xx → audit `email_undeliverable` + admin alert. **It registers its own try budget** (`func(send_email_job, max_tries=SEND_EMAIL_MAX_TRIES)`): the retry schedule outlasts an hour-long outage, which the worker-wide 5 would cut short - see `docs/engineering/mail.md`.
- **The reclaim cron must count what it FREED**, not what it attempted - it incremented `reclaimed`/`bytes_freed` regardless and emailed every admin "Reclaimed N orphaned file(s)" for bytes still on the volume, having just moved the row out of its own filter forever.
- **A job that may outlive `job_timeout` gets its own limits via `arq.worker.func(...)`** - the two encryption jobs run 6 h, `max_tries=1` (a dead run's files are picked up by the next kick or tick), `keep_result=0`. Code that iterates `WorkerSettings.functions` must read `.name` as well as `__name__`.
