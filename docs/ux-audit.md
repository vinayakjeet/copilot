# UX audit protocol and findings

The brief asks for five findings from watching three real people, each with the
change made and a before/after. The human sessions have not happened yet; they
are written here as a protocol so they can be run in an afternoon, and the
heuristic findings below are labelled as exactly that. Nothing on this page
pretends a person said it.

## Protocol for the live sessions

Three participants, each alone with the running app, no help unless they are
stuck past ninety seconds (note the stall either way). Task script:

1. "Find out which city had the most cancelled orders last quarter."
2. "You changed your mind about a query after seeing it. Back out."
3. "Something is taking forever. Stop it."
4. "Ask anything you actually want to know about this data."

Record per participant: where they looked first, whether they read the SQL
before approving or just clicked through, what they expected Stop to do,
words they used for confusing states. Five findings minimum, prioritised by
how many participants hit them.

## Heuristic findings from self-review (not user-tested)

1. **The preview asks a yes/no question about a foreign language.** The SQL is
   shown in full, which the brief demands, but nothing tells a non-SQL reader
   what the query will do in words. Change queued: one plain-English line under
   the statement ("Counts every row in orders"), generated alongside the SQL.
   Before: modal shows only code. After: modal shows code plus a sentence.
2. **"Attempt 2" appears without explaining attempt 1 failed.** The badge says
   attempt 2; the reason sits in smaller text above. Change queued: lead with
   the failure ("Postgres rejected the first version: table not found") and put
   the badge next to it. Before: badge-only. After: cause first, then badge.
3. **Stop looks dangerous.** The dialog says the server cancels the query,
   which is true, but "Stop this run?" reads like losing your work. Change
   queued: state the consequence positively ("Nothing has been lost; the
   database stops computing."). Before: neutral warning. After: reassurance
   plus mechanism.
4. **Cold-start stages use internal names.** "warehouse", "schema" mean nothing
   to a visitor. They are already rendered as sentences ("connecting to the
   warehouse") but the mapping lives in JavaScript strings rather than being
   obviously maintained. Change queued: none required beyond a test pinning the
   wording, so a rename cannot silently regress it.
5. **Empty results look like a bug.** A question can legitimately match zero
   rows; today the table renders headers and nothing else, with no line saying
   so. Change queued: "No rows matched." after stats when row_count is zero.
   Before: bare skeleton. After: explicit empty state.

Items 1, 2 and 5 need model or renderer changes tracked in BACKLOG; 3 shipped
with this text already adjusted once during writing; 4 is a test-only change.
None of the five is claimed as user-validated. That happens when the sessions
run.
