# Testing ContaBu — a guide for judges

Everything runs in PayPal's **sandbox**: no real money moves. All businesses, customers and merchants are fictional.

There are two ways in. The first needs nothing; the second lets you send and pay a real sandbox invoice with your own
PayPal developer account.

---

## Option 1 — The guided tour (no account, read-only)

1. Open **https://paypal.contabu.com** and choose **Take the guided tour**.
2. A start screen says in one paragraph what ContaBu is and who it is for. Choose **Start the tour**.
3. Fourteen stops dim the page and explain one result at a time. Use **Next** and **Back**; **Skip the tour** or `Esc` closes it.
   The tour calls no AI; it shows results from a seeded book, Rivera HVAC LLC, including two measured AI runs:

| # | Screen | What to look at |
|---|---|---|
| 1 | Owner · Summary | The year from closed periods only: totals, never transactions |
| 2 | Owner · Summary | Income and costs by category: PayPal sales at gross, the fee in *Merchant fees*, a refund in *Returns and allowances* |
| 3 | Owner · Invoices | *PayPal income*: the paid PayPal invoices, added up by the code |
| 4 | Owner · Invoices | A paid invoice marked **in your books** |
| 5 | Owner · Invoices | **+ New invoice**: create or dictate, review, and nothing is sent until the owner taps Send invoice |
| 6 | What the AI did | One real, measured dictation: NVIDIA Nemotron 3 Nano read it, the code filled the PayPal Agent Toolkit's `create_invoice` as a draft and checked it, and `send_invoice` waits for the owner |
| 7 | Owner · Invoices › Insights | **Who owes me**, on an AG Studio dashboard: what each customer owes, the overdue part and the days late; tapping a customer filters the whole page |
| 8 | What the assistant did | One real, measured answer from the dashboard assistant (4.1 seconds): Qwen3 235B asked for the list ordered by the code, wrote the answer, and the code checked its two figures |
| 9 | Owner · Contractors | A subcontractor paid with **PayPal Payouts**, booked once as contract labor with its fee |
| 10 | Owner · Accounts | **PayPal, the business's main account**: the sandbox balance next to what the books add up to |
| 11 | Bookkeeper · Bank feeds | **Nothing is counted twice**: each PayPal withdrawal matched to its bank deposit, a PayPal purchase to its bank charge |
| 12 | Bookkeeper · Settings | **Month-end close**: July to September closed and clean, October open with four pending items |
| 13 | Bookkeeper · Transactions → Rules | **How the books get categorized**: each rule categorizes what meets its condition; what no rule covers stays *Pending*. The AI never categorizes |
| 14 | Bookkeeper · Audit | **The AI team that reviews the books**: AI agents on NVIDIA Nemotron find mistakes and propose rules for the bookkeeper to approve; each agent shows what it did, its time and its cost (a full review of a real book, 10 months and 197 transactions: about 30 seconds, less than a cent) |

Outside the stops you can look around freely. In **Invoices**, the overdue invoice has a **Collect** button, and in **Invoices ›
Insights** the table has **Remind**: the collect panel opens with its four options (PayPal's official reminder, a personal message
drafted by AI, a QR code, a payment received outside PayPal), but in the tour nothing is sent and the AI is not called.

To start the tour again, press **Guide** in the top bar. **Back to the console** and **Owner portal** switch between the
owner's view and the bookkeeper's view.

## Option 2 — Your own sandbox (send and pay a real sandbox invoice)

**Before you start**, in [developer.paypal.com](https://developer.paypal.com/dashboard/applications/sandbox):
1. Open (or create) a **sandbox app** and turn on **Invoicing** and **Transaction search** in its features.
2. Copy its **Client ID** and **Secret**.
3. In **Testing tools → Sandbox accounts**, note the email of a **Personal** account: it will play your customer.

**In ContaBu:**
1. Open **https://paypal.contabu.com**, choose **Continue with Google**, and enter the **access code** from our Devpost testing
   instructions. You get your own seeded book and act as its owner.
2. **Accounts → Try with my sandbox account**: paste your Client ID and Secret, then **Connect my account**. ContaBu tests the keys
   with PayPal first and stores the secret encrypted. Sandbox keys only.
3. **Invoices → New customer**: add a customer whose email is your sandbox **Personal** account.
4. **+ New invoice**: pick that customer, or **dictate** the invoice (the microphone works in Chrome; you can also type), e.g.
   *"Invoice [your customer] 125 dollars for a filter replacement, due next Friday"*. Under the dictation, **What the AI did** shows
   each step with the model, the time and the cost, and that `send_invoice` waits for you.
5. **Review**, then **Send invoice**. PayPal emails the invoice to your sandbox Personal account.
6. Sign in at [sandbox.paypal.com](https://www.sandbox.paypal.com) as that Personal account, open the invoice and pay it (by
   PayPal, or as a guest with a sandbox card). The same page also offers Pay Later (four installments or monthly): a
   PayPal option on its invoice page, not something ContaBu built.
7. Back in ContaBu, **Invoices → Refresh status**: the invoice is **Paid** and **in your books** — a sale at gross and its fee.
   The full movement in Transaction Search can take a few hours to appear in the sandbox; *Bank feeds → PayPal → Sync* brings it.
8. Press **Ask the assistant**, at the bottom right of any screen, and ask who to remind first, which customers pay late or how
   much PayPal charged in fees. Above its window, **What the AI did** shows each step: the model, the SQL queries with their time,
   and the check of every figure. It replies in the language of your question (English or Spanish), whatever the screen's
   language. The conversation stays open as you move between screens. In **Invoices › Insights**, tap a
   customer or the **Overdue** card to filter the dashboard, and **Remind** on an unpaid invoice to go to it.

**Also worth trying in your own book:** on an open invoice, **Collect** → a friendly or firm reminder (the code suggests the tone
from the customer's payment history; the AI only writes the words) or a QR code to pay in person; **Settings → Month-end close**
to close a month and see it reach the owner's Summary.

## Automated tests

Every check runs offline from `backend/` (`python check_<name>.py`) against the database in `DATABASE_URL` (a local one,
never the deployed demo): it creates its own test data and removes it at the end, and ends with "todo bien" (all good) or a
count of failures. Each line inside a script is one assertion. The results below are from
12 November 2026, on the code in this repository:

- `check_permissions.py`: 147 API routes tried with every role; 205 assertions, **0 leaks** (the owner reaches only the 54
  routes on its list).
- `check_paypal.py`: 49 assertions, 0 failures. Sales at gross with the fee apart, refunds, withdrawal and purchase matching,
  invoices, your own keys, and the webhook: a signature check on the notice's original bytes (accented names included), a
  partial payment booked for its own amount, and a repeated notice that books nothing twice.
- `check_voice.py`: 18, 0 failures. The customer's name never reaches the model; the model gets no tools; the PayPal Agent
  Toolkit's `create_invoice` is filled by the code and validated against its schema; `send_invoice` can never be offered
  to an AI; the code computes the due date and drops a weekday or a month the owner never said.
- `check_reminders.py` 17, `check_autoremind.py` 16, `check_cancel.py` 12, `check_series.py` 24, `check_offline.py` 30,
  `check_profile.py` 24, `check_disputes.py` 43, `check_payouts.py` 20: invoicing, reminders, recurring invoices, payments
  outside PayPal, disputes and Payouts. 0 failures.
- `check_demo_paypal.py`: 25, 0 failures. The seeded book: fictional names only, closed months clean, and two open sample
  invoices (one overdue and one not yet due, dated from the day the book is seeded) that never reach PayPal.
- `check_pnl_sign.py`: 19, 0 failures. Refunds and returns subtract within their section of the P&L.
- `check_formula.py`: 26 cases, 0 failures. The rule validator: nothing the AI writes is ever executed as code.

With the real model (needs `NEBIUS_API_KEY`, costs about half a cent): `measure_voice.py` sends 100 dictations, 50 in
English and 50 in Spanish, through the same path as the app. Run twice on 12 November 2026: 195 of 200 correct (amount,
due date and description), 0.4 to 2.7 seconds each, and no customer name reached the model. In the other 5, the model
gave no answer (3) or no due date (2), and the code said so instead of guessing.

## Limits of the demo

- AI calls are capped per office per day, so a demo can't run up a bill. If you reach the cap, the screen says so.
- In production each business would connect through PayPal Partner Referrals, which needs PayPal's approval; the demo uses your
  app's credentials instead.
- The webhook confirms a paid invoice at once on the deployed site; Transaction Search may lag by hours in the sandbox.

Questions or something not working? Please write to us through Devpost.
