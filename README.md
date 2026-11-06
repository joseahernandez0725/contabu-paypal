# ContaBu — run your business from PayPal

**PayPal invoicing, payments and bookkeeping for small businesses and their bookkeepers, in English and Spanish.
The AI prepares, the code checks, a person confirms.**

*Built on the PayPal REST APIs and the PayPal Agent Toolkit (sandbox), with open AI models.*

**Live demo:** [paypal.contabu.com](https://paypal.contabu.com) · **Video:** [YouTube](VIDEO_URL_HERE) ·
**How to test it:** [docs/TESTING.md](docs/TESTING.md) · **What we built since Oct 1:** [docs/CHANGELOG.md](docs/CHANGELOG.md) ·
**Every commit since the project began (June 14, 2026):** [GitHub commits](https://github.com/joseahernandez0725/contabu-paypal/commits/main) ·
**License:** [MPL 2.0](LICENSE)

> Everything runs in PayPal's **sandbox**. No real money moves.

---

## Summary

ContaBu began as a tool for bookkeepers, for the small offices that keep the books of many businesses. That is why it has two
doors to the same set of books: the bookkeeper works in the console, where rules categorize the transactions and a team of AI
agents running on open models reviews the books, finds mistakes and proposes new rules for the bookkeeper to approve (a full
review of a real book, 10 months and 197 transactions, takes about 30 seconds and costs less than a cent); and the business owner
gets a portal with only what they need — their invoices, their accounts and the summary of the periods the bookkeeper closed. For
this hackathon we built a portal for small-business owners, managed by their bookkeeper, with PayPal at its center: invoices that
customers pay by card and that the owner can dictate from a phone, contractor payments with PayPal Payouts, and every sale booked
at gross with its fee apart, with nothing counted twice. We built it on PayPal's APIs and the PayPal Agent Toolkit, with open AI
models: the code checks every figure, and the owner confirms before anything is sent. We want PayPal to become the main account of
small businesses, where they get paid, invoice and pay, while the bookkeeper keeps their books.

## What ContaBu does

ContaBu is the back office of a small business, split between the two people who run it.

**The owner gets a portal with four windows:**
- **Invoices** — pick a saved customer or **dictate the invoice from a phone**; review it with the estimated PayPal fee; send it.
  The customer pays by credit or debit card, even without a PayPal account. Reminders, a QR code to collect in person, recurring
  invoices, payments received outside PayPal and PayPal disputes all live here.
- **Contractors** — pay subcontractors with PayPal Payouts; each payment and its fee are booked as contract labor, once.
- **Accounts** — the PayPal balance next to what the books add up to, and the bank accounts that feed the books.
- **Summary** — the year's income and costs by category, **only from periods the bookkeeper closed**. Totals, never transactions.

**The bookkeeper gets the console that keeps the books:**
- **PayPal, the business's main account, enters the books correctly.** Sale at gross, PayPal's fee as a separate expense, a refund subtracts from income,
  a withdrawal stays off the P&L. The bookkeeper's rules run first; what no rule or PayPal event code covers stays *Pending*.
- **Nothing is counted twice.** Code matches each PayPal withdrawal to its bank deposit and each purchase to its bank charge. It
  acts alone only when there is no doubt; otherwise the bookkeeper confirms.
- **Month-end close.** A closed period freezes: no rule, edit or bank update can change it. Closing asks to confirm anything still
  pending or any account with a negative total, and publishes the period to the owner.

## What makes it different

QuickBooks, Xero and Wave also send invoices that customers pay by card. The difference is **who the product is for**:

- **The owner never has to learn accounting.** The bookkeeper and the code keep the books; the owner gets the tools of a big
  company — dictated invoices, card payments with no monthly fee (PayPal charges only per payment received), reminders, a QR code.
- **Two people, one set of books.** The owner invoices and connects accounts; the bookkeeper keeps the books. Neither waits for
  the other, and the owner only ever sees what the bookkeeper closed.
- **PayPal money enters the books correctly**, gross and fee apart, refunds and transfers recognized, not as one net deposit.
- **AI with a short leash, shown on screen.** Every AI step says what it read, which PayPal Agent Toolkit tool it prepared, what the
  code checked, how long it took and what it cost — and that sending waits for the owner.

## How PayPal is used

| PayPal API | What it does in ContaBu | Where |
|---|---|---|
| **Invoicing v2** | Draft, send, cancel and read invoices; next invoice number; reminders (manual and automatic); QR code to pay in person; payments and refunds recorded outside PayPal; recurring invoices | `backend/invoices.py`, `invoice_reminders.py`, `invoice_offline.py`, `invoice_series.py` |
| **Webhooks** | A signed `INVOICING.INVOICE.PAID` books the sale; the code verifies the signature and re-reads the invoice, because that event also fires on partial payments. Locally, *Refresh status* polls instead | `backend/invoices.py`, `backend/paypal.py` |
| **Transaction Search** | Sales, fees, refunds, withdrawals and purchases into the books, de-duplicated by PayPal id | `backend/paypal.py` |
| **Balances** | The PayPal balance, compared with what the books add up to | `backend/paypal.py` |
| **Disputes** | What is in dispute, a short AI summary, and the owner's reply: accept, message, offer a refund or upload evidence | `backend/paypal_disputes.py` |
| **Payouts** | Pay subcontractors from the portal; each payout is booked as contract labor (behind the `PAYPAL_PAYOUTS` flag) | `backend/contractor_payouts.py` |
| **Log in with PayPal** | The *Connect with PayPal* button for the demo's business account | `backend/paypal.py` |

### How ContaBu uses the PayPal Agent Toolkit

ContaBu uses [`paypal-agent-toolkit`](https://github.com/paypal/agent-toolkit) 1.1.0 (Apache-2.0) for **its tool definitions and
its permission model**, never for the functions that execute (`backend/paypal_toolkit.py`):

- **What the AI may be offered:** `create_invoice` (as a draft), `get_invoice`, `list_invoices` — set with the Toolkit's own
  `Configuration(actions=…)`.
- **What only the code calls, after the owner taps the button:** `send_invoice`, `send_invoice_reminder`, `cancel_sent_invoice`,
  `generate_invoice_qr_code`. A test fails if any of them could ever be offered to the model.
- **Why:** reading the package, we found that its own `create_invoice` handler **sends the invoice right away** (it calls
  `send_invoice` with `send_to_recipient=True`). So a dictated invoice becomes the Toolkit's `create_invoice` arguments, built by
  the code from the saved customer and **validated against the Toolkit's official schema**, and ContaBu creates it as a draft.
  Telling the model "and send it right away" does nothing: it has no tool to send with.

## How AI is used

**The AI never decides, never computes and never sends.** Every total in ContaBu comes from SQL.

| Step | Model and service | What the AI does / what the code does | Measured (Nov 5, 2026) |
|---|---|---|---|
| **Dictated invoice** | NVIDIA Nemotron 3 Nano on Nebius Token Factory, no reasoning, strict JSON | The code finds the saved customer and replaces the name with `[CUSTOMER]` (if it recognizes nobody, the model isn't called). The model returns the amount, the description and the *parts* of the due date. The code validates every field, computes the date and fills the Toolkit's `create_invoice` draft | 5 of 5 dictations correct (English and Spanish) · 0.6–1.3 s · 543–593 tokens · **$0.00004–0.00005 each** |
| **Payment reminder** | Same model | The code picks the tone from the customer's payment history. The model writes the message with placeholders; it only sees the language, the tone and the invoice state. The code fills in the real name, amount, date and link and rejects any figure the model wrote | 3 drafts · 0.8–1.2 s · 455–517 tokens · **$0.00004–0.00005 each** |
| **Dispute summary** | Same model | A short summary of what the customer claims, so the owner can decide; the owner chooses what to do | — |
| **The bookkeeper's AI team** *(October, also in ContaBu)* | Five Nemotron agents on Token Factory, each the model that won its seat in measurement, plus a Tavily web search | Review the books: hidden personal spending, a merchant booked two ways, rules that drifted; they propose rules; they never categorize | A full review of a real book (197 transactions, January to October 2026): **31–34 s and $0.007–0.008** |

The voice and reminder figures come from the same `agent_calls` record that feeds the "What the AI did" panel. The October figures
were measured for the Nebius x NVIDIA hackathon (public repo `contabu-nebius`).

## Try it

The [testing guide](docs/TESTING.md) has the steps. In short:

1. **Guided tour** — *Take the guided tour* on the sign-in page: no account, read-only, a business already connected to PayPal. A
   start screen and twelve stops dim the page and explain one result at a time, across the owner's portal and the bookkeeper's console.
2. **Your own sandbox** — *Continue with Google* and the access code from our Devpost testing instructions. You get your own
   seeded book, act as its owner, connect **your** PayPal sandbox app, dictate an invoice and pay it with a sandbox Personal account.

### Honest limits

- **Connecting real businesses needs PayPal's approval.** In production each business would connect through Partner Referrals,
  available to approved partners only (it returns 403 in our sandbox). That's why the demo uses a business's own app credentials.
- **Transaction Search can take more than 3 hours** to show a movement in the sandbox, as we measured. Fees and balances arrive
  late; a paid invoice is booked at once.
- **Only USD and settled movements** are booked. Anything else is counted and declared, never dropped silently.
- **A seeded book can't match a live sandbox balance:** the sandbox account also holds every test payment made outside the demo
  book, so Accounts shows the difference and says where it comes from.

## Run it locally

Tested with **Python 3.14**, **Node 24** and **PostgreSQL 18**.

```sql
CREATE USER contabu WITH PASSWORD 'choose-a-password';
CREATE DATABASE contabu OWNER contabu;
```

```bash
cd backend
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
cp .env.example .env                   # Windows: copy .env.example .env
```

Fill in `backend/.env`:

| Variable | Needed for |
|---|---|
| `DATABASE_URL` | everything — `postgresql+psycopg://contabu:choose-a-password@localhost:5432/contabu` |
| `BANK_TOKEN_KEY` | encrypting stored credentials — a Fernet key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_ENV=sandbox` | optional: you can connect your own sandbox app from the screen instead |
| `PAYPAL_RETURN_URL` | *Connect with PayPal* — `http://127.0.0.1:5173/paypal-return` (the sandbox rejects `localhost`; set the same URL in your app's Log in with PayPal settings) |
| `NEBIUS_API_KEY` | dictation, reminders and the agents (optional: without it, only those steps report they're unavailable) |

```bash
python -m alembic upgrade head
AUTH_DEV_MODE=true PAYPAL_DEMO=true python demo_paypal.py local-dev   # the seeded book
AUTH_DEV_MODE=true PAYPAL_DEMO=true python -m uvicorn main:app --reload --port 8000
```

On Windows PowerShell, set the variables first: `$env:AUTH_DEV_MODE="true"; $env:PAYPAL_DEMO="true"`, then run the same commands.
`AUTH_DEV_MODE` accepts a header instead of a login: never turn it on in a public deployment.

```bash
cd frontend
npm install
VITE_DEV_UID=local-dev VITE_PAYPAL_DEMO=true npm run dev -- --host 127.0.0.1
```

Open **http://127.0.0.1:5173** and press **Guide** for the tour.

**Checks**, from `backend/` (no network; each ends with "todo bien" or a count of failures):

```bash
python check_permissions.py   # every route; must report 0 leaks
python check_paypal.py        # sales, fees, matching, invoices, webhook, own keys
python check_voice.py         # dictation: the customer's name never reaches the model; the AI can't send or cancel
python check_reminders.py     # reminders: tone from payment history, no figures from the model
python check_demo_paypal.py   # the seeded book: no third-party brands, closed months clean
```

## Stack

React, Vite, TypeScript and Tailwind · Python, FastAPI, PostgreSQL, SQLModel and Alembic · Firebase Auth · PayPal REST APIs through
`httpx` and the PayPal Agent Toolkit's tool definitions · NVIDIA Nemotron on Nebius Token Factory · Plaid (sandbox) for banks.

All businesses, customers and merchants in the demo are fictional.

## License

[Mozilla Public License 2.0](LICENSE). The PayPal Agent Toolkit is a dependency under Apache-2.0.
