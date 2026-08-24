# Decisions

One entry per nontrivial choice, written when the choice was made.

## Analyst seam now, Dwarpal later

The brief places Copilot on Dwarpal's Postgres-Analyst MCP server. Dwarpal is
project 5 and does not exist yet; Copilot is project 11. Rather than block, the
analyst layer (`copilot/analyst.py`) exposes one tool-call-shaped surface: a
statement plus caps go in, columns/rows/timings come out. When Dwarpal ships,
this module becomes an adapter to its MCP endpoint and nothing above it changes.
Cost of the choice: no MCP conformance value accrues here. Benefit: the project
builds at all, against real Postgres with real enforcement.

## Warehouse lives in the existing Neon project

No local Postgres exists on this machine (no Docker, no psql), and a separate
cloud project needs an account this build does not have. So
`warehouse/provision.py` creates a `warehouse` schema plus a `copilot_readonly`
role inside the existing project, grants that role SELECT only, sets
default_transaction_read_only, pins search_path, and bounds statement_timeout at
the role level. Isolation comes from the role boundary, which is the same
boundary a separate database would rely on for read paths anyway. Verified by
the provisioning script itself: insert, mutating CTE, cross-schema create and
pg_authid reads all fail as expected before it prints success.

## Tollgate first, Groq direct as the escape hatch

Model traffic was designed to flow through Tollgate like Dastavez's does. That
worked for small prompts and broke for real ones: the gateway returned
zero-byte streaming completions for multi-KB schema prompts while the identical
prompt streamed fine straight to upstream Groq (see What Broke). Both routes now
exist behind one provider class; which one runs is COPILOT_LLM_PROVIDER. The
published eval ran direct because the published number could not wait on a fix
in another project's repo. Swapping back is one environment variable.

## Keep the naive allowlist, deliberately

G1 (leading keyword plus semicolon check) blocks five of seventeen attacks. A
stronger filter would raise that number, and hide the point: keyword filters
cannot see what a WITH prefix hides. The safety suite measures each layer in
isolation precisely so the gap stays visible. The real filters are G2 (sqlglot:
root shape, mutating CTEs, policy functions, system relations) and G3 (the role).

sqlglot rather than a hand-rolled parser: Postgres grammar is a career, and one
dialect library pinned in pyproject beats twenty regexes drifting apart. Cost:
one dependency and its opinionated AST (note `args["with_"]` in v30).

## Corrected SQL never auto-runs

When execution fails, the error goes back to the model once and a corrected
statement streams in. It lands as a NEW preview requiring a NEW approval. The
first approval covered one exact statement; letting attempt two ride on it would
mean code the user never saw executes anyway. The self-correcting loop is still
one click, which is what it is for.

## Row cap enforced while fetching

Appending LIMIT to arbitrary generated text is its own injection bug: a crafted
statement can end in something that makes appended syntax part of a string or a
subquery. The cap is applied during fetchmany, truncation is reported, and the
surrounding read-only transaction rolls back whatever was left.

## Deterministic seed via keyed hashes, not random()

Gold answers downstream depend on the warehouse staying put across provisions.
random() drifts when plan shapes change evaluation order; warehouse.frac(seed)
hashes row keys through hashtextextended and cannot drift. Two provisions of the
same commit produce identical aggregates, which validate.py checks every run.

## Seeding runs statement by statement

A single multi-statement string became one enormous transaction that hung
through the pooler and failed all at once with no hint which statement broke.
apply_seed() splits on top-level semicolons (comments stripped first), runs each
statement, and names failures precisely. Same reason provision prints progress:
a script that goes quiet for ten minutes looks broken whether or not it is.

## Empty completions are failures

An upstream window exhausted mid-run returned zero-byte completions with HTTP
200. Treating those as success let them stream into the preview as empty SQL and
let an upstream cache pin the empty result onto future identical prompts. The
provider now raises ProviderError("empty completion") when a stream produces no
content, which routes into retry and then into an honest provider_error outcome.

## Eval model: openai/gpt-oss-20b, dated

The intended model (gpt-oss-120b) burned its entire 200k tokens-per-day bucket
during development, partly on unpaced early attempts. llama-3.3-70b-versatile no
longer exists on the service. gpt-oss-20b has its own daily bucket, so the full
three-run evaluation fits inside one day. The tradeoff shows up in the headline:
0.272 overall is a small model's number, and the per-difficulty gradient
(0.67 / 0.17 / 0.12 easy-medium-hard) is the interesting part, not the mean.
Model string and date are printed beside every accuracy figure.

## Untrusted tagging alone does not stop metadata injection here

The safety suite plants an instruction in a column comment and asks the model
for that column. With tagging ON the model was steered anyway; OFF, also
steered; in both cases static analysis blocked the resulting payload. Reported
as-is: prompt-layer defense did not hold for this 20B model, and the parse-level
layer is what actually contained it. This matches the brief's warning that most
projects miss this attack entirely; the honest extra finding is that tagging is
not sufficient defense at this model size.
