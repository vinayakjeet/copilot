# Accessibility

How the brief's accessibility MUST is met, and how each claim was checked.

## The flow, keyboard only

1. Page load: focus lands on the question input once the warehouse reports
   ready (the cold-start checklist is announced through `aria-live` while
   warming).
2. Type a question, press Enter. Tokens stream into the SQL preview.
3. When the preview appears, focus moves to the "Run query" button.
4. Enter approves; Escape rejects. Tab cycles only between Run and Reject
   while the preview has focus.
5. During generation or execution a visible Stop button appears. It opens a
   dialog (`role="dialog"`, `aria-modal`, labelled and described). Enter in
   the dialog confirms the stop, Escape dismisses it, focus returns to where
   it was.
6. Results render as a real table: `caption` names the question, every header
   carries `scope="col"`. Row arrival is announced ("N rows.") through an
   `aria-live="polite"` region.

Nothing requires a pointer anywhere in this sequence.

## Verification

- Lighthouse 12, accessibility category only, against the running app:
  score 100. Report committed at `docs/lighthouse-a11y.json`; the command was
  `npx lighthouse http://127.0.0.1:8000/ --only-categories=accessibility`.
  One iteration was needed: the first run scored 95 because the dark-theme
  accent (#6cb2ff under white button text) failed color-contrast at 2.22:1.
  The dark accent is now #1550b4, which clears 7:1 against its ink.
- DOM assertions for the dialog markup, aria-live region and table semantics
  live in `tests/app/test_ui.py`.
- Screenshot of the served page: `docs/screenshot-cold-start.png`.

## Known gaps

- The chart SVGs carry `role="img"` but no per-series text alternative; the
  full data table sits beside them, which covers the information but not the
  shape.
- Focus trapping inside the preview card relies on two buttons being the only
  tab stops; a third interactive element would need a real trap loop.
