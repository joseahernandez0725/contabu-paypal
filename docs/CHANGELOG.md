# ContaBu — what was built during the PayPal AI Hackathon

The hackathon's submission period opened on **October 1, 2026**. This log lists what was built since then, in two
parts, so judges can see exactly what is new:

- **October 2026 — ContaBu, the bookkeeper's console.** Built from October 1 to October 30. The same work was also
  submitted to the **Nebius x NVIDIA** hackathon on October 30, 2026 (public repo `contabu-nebius`, frozen since).
- **November 2026 — the business owner's portal and PayPal.** New for this hackathon: none of it is in the Nebius
  submission.

Dates are commit dates in this repository. Kept up to date with every commit.

---

## November 2026 — the owner portal and PayPal (new)

| Date | What |
|---|---|
| Oct 31 | Phase 16 opened: plan for the owner portal (Summary · Accounts · Invoices) and research of PayPal's official docs and API schemas (seller onboarding, Transaction Search, Invoicing, Agent Toolkit) and in-browser speech input. Docs only. |
| Oct 31 | Owner-portal decisions recorded (the owner sees closed months only, never the P&L) and a sandbox check script, `backend/paypal_sandbox_check.py`, that answers what PayPal's docs don't: seller onboarding, invoicing and transaction search in sandbox. |
| Oct 31 | Sandbox results recorded: invoicing and transaction search work; seller onboarding (Partner Referrals) needs an approved partner, so the demo connects one test account directly. |
| Oct 31 | The owner's door: a business owner invited by their bookkeeper gets their own portal (Summary · Accounts · Invoices) instead of the console, and the bookkeeper can "View as the owner". The permission check covers the new routes (0 leaks). |
| Oct 31 | Month-end close: the bookkeeper closes a finished month and it freezes (no rule, edit or bank update changes it); the owner's Summary shows that month's money in and out by category, with uncategorized items declared. |
| Oct 31 | The owner's Summary becomes the year: money in and out, a running-total chart, spending and income by category, and a table view, all computed in SQL from the closed-month snapshots. |
| Oct 31 | Month-end close waits for the book's settlement days; platform wording uses professional accounting terms (Income, Costs and expenses). |
| Oct 31 | PayPal becomes an account of the book: Transaction Search sync, bookkeeper rules first, then the PayPal event code (sale at gross, fee in Merchant fees, refund reduces income, bank withdrawal as transfer), the rest pending. The owner's Accounts window shows banks and PayPal. |
| Nov 1 | PayPal withdrawals are matched to their bank deposits (same amount, within 7 days) so a sale is counted once; automatic only when there is no doubt, otherwise the bookkeeper confirms. |
| Nov 1 | Owner invoices: saved customers, a draft created with the PayPal Invoicing API, a review screen before anything is sent, and a paid invoice booked automatically as a sale (its fee arrives with the next sync). |
| Nov 1 | "Connect with PayPal": the owner signs in to PayPal (Log in with PayPal) and the demo business account connects; the button is visible on Summary, Accounts, Invoices and Bank feeds. |
| Nov 1 | Voice invoices: the owner dictates and the form fills itself — code finds the saved customer and hides the name; NVIDIA Nemotron only extracts amount, date and description; code validates; nothing is sent without review. |
| Nov 1 | Owner portal polish: a real invoice preview with the estimated PayPal fee, dates computed by code from the dictation, the PayPal balance, the owner can link their own bank, and Summary gets a quick invoice action. |
| Nov 1 | The owner can upload a bank statement from Accounts (same chunked import as the console; rules categorize; the account label is only filled on rows that have none). |
| Nov 1 | Collect an open invoice: PayPal's official reminder (one per 24 h), a personal message NVIDIA Nemotron drafts with placeholders — code fills the customer, amount and payment link, so the AI never sees them, and the owner sends it from their own email — and a PayPal QR code to collect in person. |
| Nov 2 | Try it with your own account: connect your PayPal sandbox app's credentials (tested first, stored encrypted). A signed webhook books paid invoices after re-reading them from PayPal. |
| Nov 2 | A welcome card for business owners: invoice, customer pays by card, recorded in the books; a tour that stays on top in the owner portal and the console. |
| Nov 2 | The hosted demo: a seeded fictional book with closed months and PayPal activity, opening on the owner portal. |
| Nov 2 | A purchase paid with PayPal is matched to its bank charge, so the expense counts once: automatic only when there is no doubt, otherwise the bookkeeper confirms. |
| Nov 3 | PayPal disputes in the owner portal: the amount in dispute is declared, NVIDIA Nemotron explains each case in plain words (it never sees names, contact details or amounts, and never decides), the owner accepts the claim or writes to the customer, and lost disputes reduce sales in the books. |
| Nov 3 | Paid or refunded outside PayPal (cash, check, transfer): the owner records it on the invoice in PayPal, and ContaBu books it from the bank deposit instead of creating a second sale — automatic when only one bank row matches, otherwise the bookkeeper picks it. |
| Nov 3 | Cancel a sent invoice (PayPal can email the customer) and discard a draft. The books do not change: an unpaid invoice has no entries. |
| Nov 3 | PayPal automatic reminders: the owner sets the account's rules (before and after the due date) and PayPal emails customers on its own. |
| Nov 4 | Paid on the spot: the owner creates an invoice the customer pays right there — by card, with the PayPal QR code on screen, or already paid in cash or check, recorded as paid in one step. |
| Nov 4 | Branded invoices: the owner's own invoice number (PayPal suggests the next one in their format and rejects repeats), PO number, business address, phone, website, tax ID, note and terms. |
| Nov 4 | Recurring invoices for maintenance plans: the owner schedules them once (weekly, monthly, quarterly or yearly, with a payment term); PayPal creates and emails each one, and ContaBu brings it into Collections, where its payment is booked like any other. |
| Nov 4 | The business logo on its invoices: uploaded once, resized in the browser to PayPal's limit (250 × 90) and served at a public address for PayPal. |
| Nov 4 | Pay contractors from the PayPal balance (PayPal Payouts, behind a feature switch): confirmed payments enter the books as a job cost with PayPal's fee apart, and the screen explains that PayPal payments go on a 1099-K, not the owner's 1099-NEC. A Settings tab holds invoice details and automatic reminders. |
| Nov 5 | Partial payments and refunds outside PayPal; edit a recurring series without moving its schedule; offer a refund or upload evidence on a dispute; stop one invoice's automatic reminders; edit or remove contractors. |
| Nov 5 | Demo review 1, the product side: closing a month asks to confirm accounts with a negative total (contra accounts such as Returns and allowances excluded); the owner's Accounts lists banks fed by uploaded statements and compares the PayPal balance with what the books add up to; a PayPal refund no longer returns the original fee (US fee page); the tour folds to one line on phones. |
| Nov 5 | PayPal Agent Toolkit (`paypal-agent-toolkit` 1.1.0): its tool definitions and permission model decide what the AI may be offered (create a draft, read) and what only code calls after the owner confirms (send, remind, cancel, QR). The Toolkit's own `create_invoice` sends immediately, so ContaBu never runs its handlers: a dictated invoice becomes the Toolkit's `create_invoice` arguments, checked against its official schema, and is created as a draft. Reminder tone is chosen by code from the customer's payment history. A "What the AI did" panel shows each step with the real model, time and cost of that call. |
| Nov 5 | The demo book gets its own bank account with invented merchants, bank, payroll and insurer (each name searched, none a known company), so no third-party brand appears; July to September close with nothing pending, October stays open with four items to review. |
| Nov 5 | A spotlight tour for judges: a start screen and nine stops across the owner portal and the bookkeeper console that dim the page and explain one result at a time (no AI involved); the PayPal card in Bank feeds now shows read-only in the tour. |
| Nov 5 | The README for the hackathon (the PayPal APIs and Agent Toolkit use, AI time and cost measured with real calls) and a step-by-step testing guide for judges, `docs/TESTING.md`. |
| Nov 6 | The story becomes "run your business from PayPal": a one-paragraph start screen; the tour restarts from a **Guide** button in the top bar and has twelve stops, adding a measured AI run (shown, not called), contractors paid with **PayPal Payouts** (a payout now books to the book's existing contract-labor account) how the books get categorized (the rules) and the AI team that reviews them, with each agent's time and cost. Payouts verified in the sandbox to book once. The public repo now carries the commit history with its real dates, from June 14, 2026. |

---

## October 2026 — ContaBu, the bookkeeper's console (also submitted to Nebius x NVIDIA)

**Spreadsheet, rules and engine**
- Oct 1 · Rules moved into the sheet: import, export, priority.
- Oct 3 · Every categorization change is logged; AI removed from the last categorization paths.
- Oct 4 · Excel-style editing; rule date windows; deleting a rule returns its rows to the engine.
- Oct 7–10 · Rule order made visible; amount conditions on rules; the formula view.
- Oct 15–22 · Rule editing, duplicate-bank guard, Ctrl+C copies what the cell shows, readable amount labels.
- Oct 19 · Every book gets its own chart of accounts with IRS form lines (Schedule C and Form 1120-S).

**Banks**
- Oct 4 · Bank linking through **Plaid** (sandbox); "Bank feeds" as the way in; sync from the sheet.

**Firm, roles and access**
- Oct 1–2 · The firm layer above books, the `CLIENT` role (invitation only), every signup becomes a firm.
- Oct 4 · A demo office per visitor.
- Oct 22–23 · Demo doors: a read-only guided tour and a code-gated live demo.

**AI team on NVIDIA Nemotron (Nebius Token Factory)** — the AI never categorizes and never calculates
- Oct 7 · Book notes and the formula validator.
- Oct 8 · Formula agents on Nemotron; merchant verification with Tavily.
- Oct 10 · The Audit tab rebuilt with stored findings.
- Oct 13 · All AI moved to Nemotron; agents measured against an answer key.
- Oct 14–17 · Personal-use agent per business; agents split; Tavily only when needed; one measured model per agent;
  the note translator.
- Oct 18–19 · Agents named by what they do; the AI team shown working step by step; bilingual reviewer observations.
- Oct 20–24 · Evidence for Pending merchants; merchant-key fixes; prompts rewritten without test-book examples.

**Reports**
- Oct 4 · The P&L declares Pending in every output (screen, PDF, Excel).
- Oct 18–19 · The year-to-date P&L as the main block of the Summary; compact mode.

**Design**
- Oct 13 · One shared page layout and a transaction side panel.
- Oct 18–22 · Design rounds: no repeated header stats, book notes page, narrow sidebar drawer.

**Demo and delivery**
- Oct 17 · Public demo deployed (`demo.contabu.com`).
- Oct 24–30 · Guided quick tour, sample books for a fresh install, MPL 2.0 license, contest docs and video.
