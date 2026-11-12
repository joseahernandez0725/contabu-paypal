# Technological implementation

## Summary

With the PayPal platform, ContaBu covers the whole cycle of a small business's money (invoicing, collecting, reminding,
booking, handling disputes and paying contractors). The AI touches PayPal at three points and is also used in the
assistant and to review the books as a tool for bookkeeping, always with open models on Nebius Token Factory: NVIDIA
Nemotron and Qwen3 235B. The AI never runs an action that moves money: the PayPal Agent Toolkit defines the permissions,
and the code fills, validates and executes.

## 1. PayPal across the whole cycle of the money

The owner invoices with Invoicing v2 (a draft reviewed before sending, PayPal's numbering, manual and automatic
reminders, QR code, recurring invoices and payments recorded outside PayPal), Transaction Search and Balances bring the
money from the account into the books, and Disputes and Payouts complete the cycle. Every call goes through our own
`httpx` client, with no SDK. The `INVOICING.INVOICE.PAID` webhook books nothing on the event alone: it has PayPal verify
the signature on the notice's original bytes, because a re-serialized JSON may not match, and it re-reads the invoice,
because the same event fires on partial payments. So every payment enters for its own amount: in the checks, a $30
payment on $80 leaves $50 outstanding, the final payment brings it to zero and a repeated notice books nothing twice.
Each Transaction Search movement is stored once by its PayPal id, the sale enters at gross with the fee apart, and the
code matches each withdrawal to its deposit and each purchase to its charge in the bank; Payouts payments reach
Transaction Search with the same id, so they aren't counted twice either. On the deployed site, the webhook booked the
payment of a sandbox invoice without polling PayPal in between. The code is in `backend/invoices.py`,
`invoice_reminders.py`, `invoice_series.py`, `invoice_offline.py`, `paypal.py`, `paypal_disputes.py` and
`contractor_payouts.py`.

## 2. The PayPal Agent Toolkit as a permission contract

Dictated invoices use `paypal-agent-toolkit` 1.1.0 for its definitions and its permission model, not for its functions.
Before using it we read the package and found that its `create_invoice` sends the invoice right away (it calls
`send_invoice` with `send_to_recipient=True`), even though its description says the invoice "can be sent". That is why
the dictation model gets no tools at all: it returns in strict JSON the amount, the description and the parts of the
date, and with that the code builds the arguments of `create_invoice`, validates them against the official schema and
creates a draft. The permissions are declared with the Toolkit's own `Configuration(actions=…)`, in
`backend/paypal_toolkit.py`: an AI could only be offered creating a draft, `get_invoice` and `list_invoices`, while
`send_invoice`, `send_invoice_reminder`, `cancel_sent_invoice` and the QR code are left to the code, after the owner taps
the button. `backend/check_voice.py` fails if the model were given a tool or if `send_invoice` could be offered to an AI,
and saying "and send it now" in a dictation sends nothing.

## 3. Open models, chosen by measuring

Dictation, reminders and the dispute summary use NVIDIA Nemotron 3 Nano. The dashboard assistant uses Qwen3 235B
Instruct, chosen after trying seven models with the same two questions: Nemotron 3 Nano, Nemotron 3.5 Lightning,
Nemotron 3 Super and gpt-oss 120B couldn't use AG Studio's query tools, and Qwen3 235B, DeepSeek V4 Flash and GLM 5.3
Flash answered correctly. Each call is stored in the `agentcall` table with its model, tokens, time and cost; that same
row feeds the "What the AI did" panel, and each office has a daily cap on calls. What was measured for each use is in
[the guide's table](../JUDGES.md#technological-implementation), and the assistant's, in
[Best use of AG Grid](../JUDGES.md#best-use-of-ag-grid).

## 4. Checks with no network

The checks are scripts in `backend/` that run offline and end with "todo bien" (all good) or a count of failures: they
cover the permissions, PayPal in the books, the bank matching, the invoices, the webhook, the privacy of dictation, the
reminders and the demo book. Every new API route enters the permission check in the same change. The output of each
check, with the route count, is in [TESTING.md](../TESTING.md).

## 5. What PayPal taught us

The paid-invoice event also fires on partial payments, and its signature covers the exact bytes of the notice; editing a
recurring series with `PUT` restarts its schedule from the start date; PayPal schedules automatic reminders about 30
seconds after the invoice is sent; in the United States a refund doesn't return the original fee; and Transaction Search
can take more than three hours to show a movement in the sandbox. Each of these findings changed the code, and each is
noted where it was solved.
