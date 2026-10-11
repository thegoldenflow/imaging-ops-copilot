# Engineering notes: concurrency and the database layer

Small technical problems found while running the platform, each with its cause, the fix, the evidence and where the
code lives. They come from three follow-ups after WP10 (2026-10-11: commits b95d2a3 and 5eed672, and the fix to the
workflow tests' harness); the full records are in [PROGRESS.md](PROGRESS.md) under "WP10 follow-up". Open items are at
the end.

## 1. `functools.cache` is not a lock

- **Problem.** Right after the standalone Temporal worker started, one activity failed with "Table 'sites' is already
  defined for this MetaData instance".
- **Cause.** The worker runs activities in 8 threads, each with its own unit of work. They opened their first Store at
  the same moment, and `schema.mapping` (a `functools.cache`) built the same SQLAlchemy `Table` in two threads at once.
  The cache stores a result, but it does not stop two threads from computing it at the same time.
- **Fix.** Each table is built once under a lock, with the model import kept outside the lock, because importing a
  module can itself need a mapping. `repo._codec` keeps whichever codec is stored first (`dict.setdefault`), and
  `get_engine` creates the engine under a lock, so two threads can no longer create two pools.
- **Evidence.** Eight threads behind a barrier, with a slow model import to line them up, build one codec and one
  `Table`. Before the fix, 7 of the 8 failed.
- **Code.** `apps/api/app/core/db/schema.py`, `repo.py`, `store.py`; `tests/test_db_threads.py`.

## 2. A cancelled query leaves the connection busy

- **Problem.** The worker log showed "another command is already in progress", followed by the pool's "Exception
  during reset" traceback.
- **Cause.** Temporal cancels a timed-out synchronous activity by raising `CancelledError` inside its thread. On
  Windows, psycopg waits for results in a Python loop, so the exception can land in the middle of a query. The unit of
  work then sent ROLLBACK into a connection that still had a query running.
- **Fix.** `EngineSource` checks psycopg's `transaction_status`. When it is ACTIVE, the connection is not rolled back:
  its query is cancelled on the server (`cancel_safe`), so it stops waiting for a lock, and the connection is
  invalidated rather than returned to the pool. The original exception comes out unchanged.
- **Evidence.** A test raises an exception into a thread that is waiting on an advisory lock (with
  `PyThreadState_SetAsyncExc`, which is how Temporal does it), using psycopg's Python wait loop on every platform.
  Afterwards the pool logs nothing, the server stops waiting and the next unit of work runs.
- **Code.** `apps/api/app/core/store.py` (`_busy`, `_release`); `tests/test_db_threads.py`.

## 3. Hold a lock only for the work that needs it

- **Problem.** A demo reset held `REWRITE_LOCK` (an exclusive, transaction-scoped advisory lock) for its whole run,
  including generating and encoding the data set in memory. Workflow activities, which take the lock shared,
  exceeded `recordProgress`'s 30-second timeout and were retried until the reset ended.
- **Fix.** Generating (`build_store`) and encoding (`encode_rows`: PHI encryption and JSON) now run before the lock is
  taken. Under the lock run only TRUNCATE, the inserts and the id sequences. The lock is still transaction-scoped, so
  whatever the caller writes afterwards in the same transaction (the storyline's preset, the audit record) is covered
  until it commits.
- **Evidence.** Measured on a seeded test database:

  | | Before | After |
  | --- | --- | --- |
  | Lock held | 52.6 s | 18–20 s (storyline reset: 27.6 s) |
  | Wait of an activity arriving 2 s into the reset | 51.4 s | 0.1 s |

- **Code.** `apps/api/app/core/store.py` `reset_store`; `tests/test_reset_lock.py`.

## 4. Process caches are hidden shared state

- **Problem.** Moving generation out of the lock is safe only if generation reads nothing that other units of work
  can change.
- **How it was checked.** `build_store()` ran with `DATABASE_URL` pointing at a closed port, while a SQLAlchemy
  `before_cursor_execute` listener on every engine and recording wrappers around `get_engine`, `ambient_store` and
  both process caches logged every access. Generation ran no SQL and used no engine and no ambient store, but it did
  read and write the process-wide cache dict.
- **Cause.** Module state of kind "cache" (`noshow`, `phipa_cache`, `inspection_index`) went into the process-wide
  `_PROCESS_CACHE`, even for a detached (in-memory) store. The PHIPA seed calls `detect()`, which returns the cached
  entry whenever the audit-event counts match. A unit of work running in the same process could therefore feed alerts
  computed from the live database into the generator.
- **Fix.** A detached store keeps its own caches (`ModuleSpace._caches()`). Database stores still share the process
  cache.
- **Code.** `apps/api/app/core/db/repo.py`; `tests/test_reset_lock.py::test_a_detached_store_keeps_its_caches_to_itself`.

## 5. Testing concurrency without sleeps

- **Barrier inside a stub.** Two resets run concurrently, each with a stubbed generation step that waits on a
  `threading.Barrier(2)`. If generation still ran under the lock, the second reset could never reach the barrier, and
  the barrier would break.
- **`pg_locks` instead of timing.** "The second reset is waiting" is read from `pg_locks` (`NOT granted`). That view
  lists the locks of every database on the server, and other test databases use the same lock key, so the query
  filters on `database = current_database()`.
- **Rolled-back transactions.** Each reset writes an empty data set inside a transaction that is rolled back.
  TRUNCATE, including RESTART IDENTITY, is transactional in PostgreSQL, so the shared test database keeps its data.
- **Fail before, pass after.** All three new tests were run against the previous code to confirm they fail there.
- **Determinism check.** Two generations in parallel threads produced the same data as a single one, compared
  collection by collection after decoding. The only differences, wall-clock timestamps and random survey tokens, also
  appear between two sequential runs.

## 6. Measure before optimizing

- Where the reset's 18–30 s under the lock goes (seeded database, this machine):

  | Step | Time |
  | --- | --- |
  | TRUNCATE of 64 tables | 1.3 s |
  | Inserts | 20.9 s, of which `fhir_resources` (64,653 rows) 14.0 s and `appointments` 3.0 s |
  | Encoding (now before the lock) | 4–6 s |
  | Generating (before the lock) | 14–18 s |

  The README's "1–3 minutes" for a whole reset was measured under heavier load; here it took 39–54 s.
- **Timing tests and load.** A 5-second test, such as the backfill test in `test_flagships.py`, fails when other
  suites hold the CPU (7.1 s) and passes on its own (3.4 s). A failure of that kind has to be re-run in isolation
  before anyone draws a conclusion from it.

## 7. A worker shutdown cancels what is still running

- **Problem.** The storyline-journey workflow test passed, then sometimes failed at teardown: the fixture's rollback
  raised "another command is already in progress" (1 of 10 runs on b95d2a3, 3 of 6 on 5eed672).
- **Cause.** The test harness stops its worker with `Worker.shutdown()`. temporalio's default
  `graceful_shutdown_timeout` is zero, so the shutdown at once raises `CancelledError` inside every running activity's
  thread, as in note 2. In the tests, every activity uses the test's one connection (`ConnectionSource`), which has no
  busy-connection check. A `workflows.recordProgress` cut off mid-query left that connection busy, and the fixture's
  ROLLBACK was the next command sent into it.
- **Fix.** The test worker stops with a 30-second grace period, so running activities finish first. If one still
  runs after that and is cancelled mid-query, the fixture drops the busy connection (`_release`, as in note 2), and
  closing the connection ends the outer transaction. Cancelling the query and then rolling back would not work: the
  cancelled query's result still has to be read first. The harness also undoes its steps with an `ExitStack`.
  Before, a worker start or stop that timed out under load skipped the rest of the teardown, and the Temporal test
  server process kept running after pytest.
- **Evidence.** A probe activity waits for an advisory lock that the test holds while the worker is told to stop, and
  the lock is released 1 s after the worker has told its activities it is shutting down. With a zero grace period the
  test failed in each of 3 runs, with the `CancelledError` inside psycopg's `wait_select`. With the fix, the
  storyline test passed 8 of 8 runs on its own, and the backend suite passed (778 of 778). A second test leaves the
  connection busy on purpose: without the fixture's check, its teardown fails with the original errors.
- **Code.** `apps/api/tests/temporal_harness.py` (`GRACE`, `harness()`), `tests/conftest.py` (`fresh_state`);
  `tests/test_workflows.py::test_stopping_the_worker_lets_an_activity_finish_the_query_it_is_in`,
  `tests/test_db_threads.py::test_a_test_whose_connection_is_left_mid_query_still_ends_cleanly`.

## Open optimizations

- **Shorter writes under the lock.** Use COPY instead of INSERT for `fhir_resources`, or give the activities that take
  the lock a longer start-to-close timeout. While the writes alone can exceed 30 s, an activity that starts waiting
  just as they begin can still time out. It is then retried, without tracebacks.
- **A failed unit of work in the tests.** The ambient branch of `unit_of_work()` only skips its `save()` when the
  block raises, so the next unit of work's `save()` writes the changes a failed activity left pending, and its SQL
  stays in the outer transaction. In production the activity's transaction is rolled back. A savepoint per ambient
  unit of work would close the gap.
- **The harness's start timeout.** `start_worker` gives the worker 120 s, including building it, which validates
  each workflow in Temporal's sandbox, on the event loop. Under 100% CPU that limit was reached once. Where the time
  went was not measured.
