# <img src="docs/img/logo.png" alt="ContaBu logo" width="48" height="48"> ContaBu — PayPal, from the invoice to the books

**The owner invoices, their customer pays by card, the bookkeeper keeps the books, and AI helps them both.**
ContaBu is an AI bookkeeping tool for the offices that keep the books of many small businesses, in English and Spanish.
The bookkeeper works in a console with all their clients, and each owner gets a portal with what is theirs. It began on
June 14, 2026 as a prototype; [during the submission period](docs/JUDGES.md#before-and-during-the-hackathon) we built
the console for many books, its AI team and everything PayPal: the owner invoices, collects and pays from their portal,
and that money enters the books on its own.

*Built on the PayPal REST APIs, the PayPal Agent Toolkit (sandbox) and AG Studio, with NVIDIA Nemotron and Qwen3.*

**Live demo:** [paypal.contabu.com](https://paypal.contabu.com) · **Video:** [YouTube, 2:46](https://www.youtube.com/watch?v=bHiJpu7pj20) ·
**For judges:** [docs/JUDGES.md](docs/JUDGES.md) · **How to test it:** [docs/TESTING.md](docs/TESTING.md) ·
**What we built since October 1:** [docs/CHANGELOG.md](docs/CHANGELOG.md) ·
**Every commit of the project:** [GitHub commits](https://github.com/joseahernandez0725/contabu-paypal/commits/main) ·
**License:** [MPL 2.0](LICENSE)

> Everything runs in PayPal's **sandbox**. No real money moves.

![The PayPal cycle in ContaBu, in real screens: the owner dictates an invoice (Invoicing API) and the AI fills the PayPal Agent Toolkit's create_invoice as a draft; the owner reviews it with PayPal's fee and sends it; the customer pays by card without a PayPal account; the payment enters the books on its own (Webhooks); each PayPal withdrawal is matched to its bank deposit (Transaction Search); a contractor is paid with Payouts; and the AI explains a dispute while the owner decides (Disputes API)](docs/img/paypal-cycle.gif)

---

## Summary

**We want PayPal to become the main account of small businesses, where they get paid, invoice and pay, and to reach them
through the bookkeeper who already keeps their books.** Today 76% of US construction subcontractors are usually or
always paid by paper check
([PYMNTS Intelligence, 2024](https://www.pymnts.com/report/tracker/from-the-ground-up-rebuilding-payments-in-the-construction-industry/)),
and from what we see in a bookkeeper's daily work, those who already get paid with PayPal move the money to the bank,
because to their books PayPal is a black box. ContaBu solves both. It brings PayPal to the owner through their
bookkeeper: from the portal their office gives them, the owner invoices with PayPal (even by dictating from a phone),
their customer pays by card, they pay their contractors with PayPal Payouts, answer their disputes and see who owes them
on a dashboard built with AG Studio. And it lets the money stay in PayPal: every sale enters the books on its own at
gross, with PayPal's fee apart, and every withdrawal is matched to its bank deposit, with nothing counted twice. That is
seven PayPal APIs and the PayPal Agent Toolkit, which sets the AI's permissions: the AI prepares, the code checks and the
owner confirms. And Spanish is not an extra: 5.3 million US businesses without employees are Hispanic-owned
([Census Bureau, 2025](https://www.census.gov/newsroom/press-releases/2025/business-owner-characteristics.html)).

**Best use of AG Grid.** Insights, a tab of Invoices, is an AG Studio dashboard over the owner's invoices, contractor
payments and closed periods: three widgets of our own inside the canvas ("Who owes me", the indicators and a customer
search), the portal's own theme (`studioTheme.withParams`), AG Studio's native table with a "Remind" button on each unpaid
invoice, and on top of it an assistant built on AG Studio's Agent Framework with a swapped-in harness of our own
(`createAiHarness`). [How it is built →](docs/JUDGES.md#best-use-of-ag-grid)

[Read the full version →](docs/JUDGES.md)

## For the business owner

In their portal, the owner invoices with PayPal by picking a saved customer or dictating from a phone: the AI drafts the
invoice [in about a second and for less than a hundredth of a cent](docs/JUDGES.md#technological-implementation), and
nothing is sent until the owner reviews it. Their customer pays by card even without a PayPal account. PayPal's invoice
payment page already offers [Pay Later](https://www.paypal.com/us/business/accept-payments/installment-payments): the
customer can pay in four installments or monthly, and the owner gets the full amount up front. ContaBu didn't build it;
it gets it because every invoice goes out through PayPal. From the same portal the owner sends reminders, collects in
person with a QR code, schedules recurring invoices, answers disputes and pays contractors with PayPal Payouts. In the
Insights tab, a dashboard built with AG Studio shows who owes them and how much is overdue. And from any screen they can
ask an AI assistant about their business (who pays late, how much PayPal charged in fees, how much they spent on gas
last month), and the code checks every figure it writes before showing it. The owner never has to learn accounting: they
see the totals of the months their bookkeeper closed.

## For the bookkeeper

The United States has
[71,408 bookkeeping offices, with four employees on average](https://data.bls.gov/cew/data/api/2024/a/industry/541219.csv)
(BLS, 2024). In ContaBu's console, each person in the office has their own permissions
([147 routes tried with every role, no leaks](docs/TESTING.md)). The bookkeeper's rules categorize, and what no rule
covers stays pending and is declared, never hidden. PayPal enters the books the way a bookkeeper would record it, and
closing a month freezes the period and publishes it to the owner. A team of AI agents on NVIDIA Nemotron, with Tavily web
searches, reviews the books and proposes rules the bookkeeper approves: on a blind exam it found
[14 to 15 of 22 hidden errors, against 8 for the code without AI](docs/JUDGES.md#innovation--idea), and
[reviewing a real book of 197 transactions takes about 30 seconds and costs less than a cent](docs/JUDGES.md#potential-impact).

## PayPal at the center

ContaBu uses seven PayPal APIs that cover the whole cycle of the money: Invoicing to invoice, Webhooks to know an invoice
was paid, Transaction Search and Balances to bring the money into the books, Disputes to answer claims, Payouts to pay
contractors and Log in with PayPal to connect the account. The webhook doesn't take the notice at its word: it has PayPal
verify the signature and re-reads the invoice before booking anything. The PayPal Agent Toolkit sets what an AI could do:
create a draft or read; sending, reminding and canceling are called only by the code, when the owner taps the button.
Reading the package, we found that its own `create_invoice` sends the invoice right away, so we never run its functions.
[How we did it →](docs/judges/technological-implementation.md)

## The AI prepares, the code checks, a person confirms

The AI never computes, never categorizes and never sends: totals come from SQL, categories from a rule or the
bookkeeper, and sending is always the owner's decision. Dictation uses NVIDIA Nemotron 3 Nano and never sees the
customer's name. The assistant uses Qwen3 235B, chosen after trying seven models, and if it writes a figure that isn't in
the data, the answer isn't shown: [in testing it hid the three answers with made-up figures](docs/JUDGES.md#innovation--idea).
Every AI step shows on screen what it read, what the code checked, how long it took and what it cost.

## Try it

The [testing guide](docs/TESTING.md) has the steps. In short:

1. **Guided tour** — *Take the guided tour* on the sign-in page of
   [paypal.contabu.com](https://paypal.contabu.com): no account, read-only, a business already connected to PayPal. A
   start screen and fourteen stops dim the page and explain one result at a time, across the owner's portal and the
   bookkeeper's console, including the Insights dashboard and one measured answer from its assistant.
2. **Your own sandbox** — *Continue with Google* and the access code from our Devpost testing instructions. You get your
   own seeded book, act as its owner, connect **your** PayPal sandbox app, dictate an invoice and pay it with a sandbox
   Personal account. The **Ask the assistant** button, at the bottom right of every screen, opens the assistant.

All businesses, customers and merchants in the demo are fictional.

## Limits

- Everything runs in PayPal's sandbox and no real money moves; ContaBu has no paying customers yet.
- For an office to connect other people's businesses, PayPal has to approve it as a partner (Partner Referrals);
  meanwhile, each business connects its own PayPal app.
- Transaction Search can take more than three hours to show a movement in the sandbox, and only US dollar movements are
  booked.
- The dashboard assistant sends the model the customers' names and their totals (in dictation, by contrast, the name
  never reaches the model); in the demo all data is made up, and with a real book the owner should know it.
- The measurements of the bookkeeper's agents come from a single real book.
- A seeded book can't match the live balance of a sandbox account, which also holds the test payments made outside the
  demo: that is why Accounts shows a difference.

---

## For developers

The code is open source under the MPL 2.0 license: you can review it, run it and modify it. Its comments and the output
of the checks are in Spanish, the language of the bookkeeper who built it; "todo bien" means "all good".

### Run it locally

Tested with **Python 3.14**, **Node 24** and **PostgreSQL 18**.

Create the database as the PostgreSQL superuser (`sudo -u postgres psql` on Linux). On Windows the installer does not
add `psql` to the PATH: `& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres`.

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
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_ENV=sandbox` | optional: you can also connect your own sandbox app from the screen |
| `PAYPAL_RETURN_URL` | *Connect with PayPal* — `http://127.0.0.1:5173/paypal-return` (the sandbox rejects `localhost`; set the same URL in your app's Log in with PayPal settings) |
| `NEBIUS_API_KEY` | dictation, reminders, the assistant and the agents (optional: without it, only those steps report they're unavailable) |
| `TAVILY_API_KEY` | the web merchant search of the bookkeeper's agents (optional: without it, everything else works) |

```bash
python -m alembic upgrade head
AUTH_DEV_MODE=true PAYPAL_DEMO=true python demo_paypal.py local-dev   # the seeded book
AUTH_DEV_MODE=true PAYPAL_DEMO=true python -m uvicorn main:app --reload --port 8000
```

`AUTH_DEV_MODE` accepts a header instead of a login: never turn it on in a public deployment.

```bash
cd frontend
npm install
VITE_DEV_UID=local-dev VITE_PAYPAL_DEMO=true npm run dev -- --host 127.0.0.1
```

**On Windows (PowerShell)**, set the variables on their own line and run the commands without the `NAME=value` prefix:

```powershell
# backend
$env:AUTH_DEV_MODE="true"; $env:PAYPAL_DEMO="true"
python demo_paypal.py local-dev
python -m uvicorn main:app --reload --port 8000

# frontend (in another terminal)
$env:VITE_DEV_UID="local-dev"; $env:VITE_PAYPAL_DEMO="true"
npm run dev -- --host 127.0.0.1
```

If PowerShell refuses to run `.venv\Scripts\Activate.ps1`, run `Set-ExecutionPolicy -Scope Process RemoteSigned` first,
or skip the activation and call `.venv\Scripts\python.exe` instead of `python`.

Open **http://127.0.0.1:5173** and press **Guide** for the tour. AG Studio runs without a license key, with its trial
watermark; to remove it, set `VITE_AG_STUDIO_LICENSE` in `frontend/.env.local` (AG Grid offers a free 45-day trial
license).

### Checks

From `backend/`, no network; each ends with "todo bien" (all good) or a count of failures. The full list and its output
are in [TESTING.md](docs/TESTING.md).

```bash
python check_permissions.py   # every route; must report 0 leaks
python check_paypal.py        # sales, fees, matching, invoices, webhook, own keys
python check_voice.py         # dictation: the customer's name never reaches the model; the AI can't send or cancel
python check_reminders.py     # reminders: tone from payment history, no figures from the model
python check_demo_paypal.py   # the seeded book: no third-party brands, closed months clean, and two sample open
                              # invoices (one overdue, one not yet due) that never reach PayPal
```

### Stack

React, Vite, TypeScript and Tailwind · Python, FastAPI, PostgreSQL, SQLModel and Alembic · Firebase Auth · the PayPal
REST APIs through `httpx` and the PayPal Agent Toolkit's tool definitions · AG Studio 3.1 and AG Charts · NVIDIA
Nemotron and Qwen3 235B on Nebius Token Factory · Tavily for web search · Plaid (sandbox) for banks.

### License

[Mozilla Public License 2.0](LICENSE). The PayPal Agent Toolkit is a dependency under Apache-2.0; AG Studio is a
commercial dependency.
