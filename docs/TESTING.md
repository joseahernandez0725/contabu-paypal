# Testing ContaBu — a guide for judges

Everything runs in PayPal's **sandbox**: no real money moves. All businesses, customers and merchants are fictional.

There are two ways in. The first takes about three minutes and needs nothing; the second lets you send and pay a real sandbox
invoice with your own PayPal developer account.

---

## Option 1 — The guided tour (no account, read-only)

1. Open **https://paypal.contabu.com** and choose **Take the guided tour**.
2. A start screen compares PayPal without and with ContaBu. Choose **Start the tour**.
3. Twelve stops dim the page and explain one result at a time. Use **Next** and **Back**; **Skip the tour** or `Esc` closes it.
   The tour calls no AI; it shows results from a seeded book, Rivera HVAC LLC, including one measured AI run:

| # | Screen | What to look at |
|---|---|---|
| 1 | Owner · Summary | The year from closed periods only: totals, never transactions |
| 2 | Owner · Summary | Income and costs by category: PayPal sales at gross, the fee in *Merchant fees*, a refund in *Returns and allowances* |
| 3 | Owner · Invoices | *PayPal income*: the paid PayPal invoices, added up by the code |
| 4 | Owner · Invoices | A paid invoice marked **in your books** |
| 5 | Owner · Invoices | **+ New invoice**: create or dictate, review, and nothing is sent until the owner taps Send invoice |
| 6 | What the AI did | One real, measured dictation: an open AI model read it, the PayPal Agent Toolkit's `create_invoice` was prepared as a draft, the code checked it, and `send_invoice` waits for the owner |
| 7 | Owner · Contractors | A subcontractor paid with **PayPal Payouts**, booked once as contract labor with its fee |
| 8 | Owner · Accounts | **PayPal, the business's main account**: the sandbox balance next to what the books add up to |
| 9 | Bookkeeper · Bank feeds | **Nothing is counted twice**: each PayPal withdrawal matched to its bank deposit, a PayPal purchase to its bank charge |
| 10 | Bookkeeper · Settings | **Month-end close**: July to September closed and clean, October open with four pending items |
| 11 | Bookkeeper · Transactions → Rules | **How the books get categorized**: each rule categorizes what meets its condition; what no rule covers stays *Pending*. The AI never categorizes |
| 12 | Bookkeeper · Audit | **The AI team that reviews the books**: AI agents on open models find mistakes and propose rules for the bookkeeper to approve; each agent shows what it did, its time and its cost (a full review of a real book, 10 months and 197 transactions: about 30 seconds, less than a cent) |

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
   PayPal, or as a guest with a sandbox card).
7. Back in ContaBu, **Invoices → Refresh status**: the invoice is **Paid** and **in your books** — a sale at gross and its fee.
   The full movement in Transaction Search can take a few hours to appear in the sandbox; *Bank feeds → PayPal → Sync* brings it.

**Also worth trying in your own book:** on an open invoice, **Collect** → a friendly or firm reminder (the code suggests the tone
from the customer's payment history; the AI only writes the words) or a QR code to pay in person; **Settings → Month-end close**
to close a month and see it reach the owner's Summary.

## Limits of the demo

- AI calls are capped per office per day, so a demo can't run up a bill. If you reach the cap, the screen says so.
- In production each business would connect through PayPal Partner Referrals, which needs PayPal's approval; the demo uses your
  app's credentials instead.
- The webhook confirms a paid invoice at once on the deployed site; Transaction Search may lag by hours in the sandbox.

Questions or something not working? Please write to us through Devpost.
