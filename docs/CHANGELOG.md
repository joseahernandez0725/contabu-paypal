# ContaBu — project history

ContaBu began on June 14, 2026. The PayPal AI Hackathon's submission period opened on **October 1, 2026**. This table
tells the whole story in order, as a single project: above the October 1 line is the starting point, summed up by week;
below it, what was built during the period, day by day. The "Part" column says which part of the product each change
went into: the bookkeeper's console, the owner's portal, or the whole product (offices, demo, tour, checks and
documents). PayPal shows up in both doors: in the console, where its money enters the books, and in the portal, where
the owner invoices, collects and pays.

Dates are the commit dates in this repository. Up to date as of the submission day.

| Date | Part | What |
|---|---|---|
| Jun 14–15 | Prototype | The data model (SQLModel and PostgreSQL), AI categorization, the profit and loss statement computed in SQL and the import of CSV bank statements. |
| Jun 15–16 | Prototype | The first interface (React and Tailwind), designed for the phone, in English and Spanish. |
| Jun 17–19 | Prototype | A rules engine consulted before the AI; an editable table where correcting a category creates a rule; import with account, duplicates and settlement window; Firebase sign-in and several companies per account; a date filter and the P&L as PDF. |
| Jun 21–28 | Prototype | An editable rules table; roles and permissions per company; the payee of checks; the AI proposes rules and the owner approves them; importing rule files; an audit agent without AI; statements in Excel and with debit and credit columns; chunked uploads; transfers classified without AI. |
| Jun 28–30 | Prototype | A first subscription billing with an AI quota (retired on November 9); brand identity and an installable app. |
| Jul 2–3 | Prototype | A receipt photo, read by the AI, books a cash expense; the source of each category and keyword validation; an automatic auditor and an interview with the bookkeeper. |
| Jul 8–12 | Prototype | Charts of accounts for six more industries; a full review as a new user, with ten fixes. |
| Sep 26–27 | Prototype | Email verification and password recovery; the repository, ready to be public; the console's first sheet, pivot and rules sheet, and the AI stops categorizing in the sheet; the interface narrows to English and Spanish. |
| **Oct 1** | | **The submission period opens. Everything below was built during the period.** |
| Oct 1 | Console | Rules move into the sheet: import, export, priority and criteria. |
| Oct 1–2 | Console | The amount band, its criteria and the initial interview are retired. |
| Oct 1–2 | Whole product | The office layer above the books, the `CLIENT` role (invitation only), and every signup becomes an office. |
| Oct 3 | Console | Every category correction is logged, and the AI leaves the last categorization paths. |
| Oct 4 | Console | Banks connected through Plaid (sandbox), Bank feeds as the way in and sync from the sheet; Excel-style editing; the P&L declares what is pending in every output, and the AI stops categorizing. |
| Oct 4 | Whole product | A demo office per visitor, with seeded books. |
| Oct 7 | Console | Book notes and the formula validator; the order of the rules becomes visible. |
| Oct 8 | Console | The formula agents run on NVIDIA Nemotron and verify merchants with Tavily. |
| Oct 10 | Console | The Audit tab rebuilt, with stored findings; amount rules and the formula view. |
| Oct 13 | Console | The transaction side panel and a shared page layout; all AI moves to Nemotron; the agents are measured against an answer key. |
| Oct 14 | Console | A second answer key the agents never saw, for the blind exam; the personal-use agent judges by the business. |
| Oct 14 | Whole product | Real client data stays out of the deployment image. |
| Oct 15–17 | Console | The agents are split and each gets the model that won its seat by measuring; Tavily only when needed; the rule writer moves to Nemotron 3.5 Super VL and the note translator joins; larger models are measured in the judge seats. |
| Oct 17 | Whole product | The public demo, deployed. |
| Oct 18 | Console | The agents are named by what they do and the AI team moves to the front of Audit, showing each agent at work; the year's P&L as the main block of the Summary, with a compact mode; book notes get their own page. |
| Oct 18–20 | Whole product | The language packs declared; English by default for new visitors; reconnection when the demo database closes an idle connection. |
| Oct 19 | Console | Every book gets its own chart of accounts with IRS form lines (Schedule C and Form 1120-S); quick notes from Transactions and the transaction's note in the side panel; the reviewer writes in English and Spanish; Ctrl+C copies what the cell shows. |
| Oct 20–22 | Console | Evidence for pending merchants, and an explanation when no proposed rule passes; fixes to the merchant keys; a narrow side menu and a collapsible detail panel; a rule's amount condition in a single readable label; a script to measure the models on a real book. |
| Oct 22–25 | Whole product | The demo splits into a read-only guided tour and a live demo with an access code; a spotlight tour with a welcome screen, which becomes a fourteen-stop quick guide; sample books for a local install. |
| Oct 24 | Console | The sheet and the rules, read-only for the VIEWER role; the five agent prompts rewritten without examples from the test books; the merchant verifier ranks the web results with Nemotron Super; the rule writer is measured against the bookkeeper's decisions. |
| Oct 25–28 | Whole product | The README in English and the project's public documents, in English and Spanish; a local install that works on Windows. |
| Oct 27–29 | Console | The personal-use agent is measured with and without the merchant types from the web; the P&L PDF, in English. |
| Oct 31 | Console | PayPal becomes an account of the book: Transaction Search brings the movements, the sale enters at gross, the fee in Merchant fees, the refund subtracts from income and the withdrawal to the bank is a transfer. Month-end close: the closed period freezes and its rows lock in the sheet. Rows in the opposite direction subtract within their section of the P&L. |
| Oct 31 | Portal | The owner's door: an owner invited by their bookkeeper gets their own portal (Summary, Accounts, Invoices), and the bookkeeper can see it "as the owner". The Summary shows the owner's year from the closed months. |
| Nov 1 | Console | Each PayPal withdrawal is matched to its bank deposit, so a sale counts once; automatic only when there is no doubt, otherwise the bookkeeper confirms. |
| Nov 1 | Portal | Invoices with PayPal's Invoicing API: saved customers, a draft, a review screen, and the paid invoice booked as a sale. Voice invoices: the code recognizes the customer and hides their name, NVIDIA Nemotron extracts the amount, the date and the description, and the code validates. "Connect with PayPal". A preview with the estimated fee, the PayPal balance, uploading statements and linking their bank. Collecting an open invoice: PayPal's official reminder, a message the AI drafts with placeholders and a QR code. |
| Nov 2 | Console | Each purchase paid with PayPal is matched to its bank charge, so the expense counts once. |
| Nov 2 | Portal | Try it with your own sandbox: the credentials are tested first and stored encrypted. A signed webhook books paid invoices after re-reading them from PayPal. A welcome card about taking cards. |
| Nov 2 | Whole product | The PayPal demo deployed, with a tour that spends no AI credits. |
| Nov 3 | Portal | PayPal disputes, with an AI summary and the owner's reply; payments and refunds outside PayPal, booked from the bank deposit; canceling a sent invoice and discarding a draft; PayPal's automatic reminders. |
| Nov 4 | Portal | Paid on the spot (by card, with the QR code or in cash); invoices with the business's brand and logo; recurring invoices for maintenance plans; contractor payments with PayPal Payouts. |
| Nov 5 | Console | Month-end close asks to confirm the accounts with a negative total; a PayPal refund no longer returns the original fee. |
| Nov 5 | Portal | Partial payments; editing a recurring series without moving its schedule; offering a refund or uploading evidence in a dispute. Accounts compares the PayPal balance with what the books add up to. The PayPal Agent Toolkit defines what the AI can prepare and what only the code calls, and the "What the AI did" panel shows each step with its model, time and cost. |
| Nov 5 | Whole product | The demo book, with its own bank and invented merchants, and July to September closed with nothing pending; a spotlight tour across the owner's portal and the console; the README and the testing guide, `docs/TESTING.md`. |
| Nov 6 | Console | A PayPal Payouts payment is booked to the contract-labor account the book already has. |
| Nov 6 | Portal | Insights: an AG Studio dashboard over the invoices, the contractors and the closed months. |
| Nov 6 | Whole product | The tour restarts from the Guide button and shows the AI runs with their time and cost; the public commit history, with its real dates since June 14. |
| Nov 8 | Portal | The dashboard assistant, on AG Studio's Agent Framework with a swapped-in harness of our own: Qwen3 235B, chosen among seven models; the figures come from SQL and each one is checked before it is shown. Our own widgets ("Who owes me" and indicators), a floating chat and a customer search. The Invoices cards filter the list of invoices. |
| Nov 8 | Whole product | The tour adds the Insights dashboard and its assistant (fourteen stops). |
| Nov 9 | Portal | Each partial PayPal payment enters the books when it arrives; the webhook verifies the signature on the notice's original bytes; the dashboard blends into the portal, with no gray frame. |
| Nov 10 | Portal | The Summary draws its charts with AG Charts and the dashboard's palette; Insights becomes a tab of Invoices, with a Remind button on each unpaid invoice; the assistant answers on any screen and keeps the conversation; after testing it with the real model, its tools give the payment habit in words, the total owed and each month's merchants grouped by category. |
| Nov 10 | Whole product | The guide for judges, `docs/JUDGES.md`, with one detail document per criterion. In the tour, the overdue invoice shows Collect and the Insights table shows Remind; the collect panel opens read-only (nothing is sent and the AI is not called). |
| Nov 11 | Whole product | The guide for judges becomes a single document, with one detail document (technological implementation); the README opens with what ContaBu is and carries the logo; a cover GIF of the PayPal cycle in real screens, with the PayPal tool of each step (a real invoice sent and paid by card in the sandbox); two images: the two doors over the same books and the assistant answering in Insights. The guided tour names the models it measured. The demo video (2:46, YouTube): an animated opening with real screens, the owner's benefits, and the PayPal tool of each step on screen. |
| Nov 12 | Whole product | Every seeded book has an overdue and a not-yet-due invoice, so Insights always has someone to remind. The assistant writes amounts in US format and replies in the language of the question. Dictation, measured with 100 dictations twice, gets 195 of 200 right, and in the other 5 the code says so instead of guessing. The docs name Pay Later and the AG Grid work up front, and the README has a block to run it on Windows PowerShell. |
