# AnyCompany Bank — Customer Service Handbook

**Version:** SH-2026.3 · **Effective:** 2026-09-01 · **Owner:** Customer Operations Policy Office · **Classification:** Internal

> AnyCompany Bank is a fictional bank. This handbook, its policies, fees, codes and contact points are invented for
> training purposes. Nothing in it is financial, legal or tax advice.

This handbook is the single reference for every person and every automated agent that answers AnyCompany Bank
customers in chat, in the mobile app and on the phone. It sets out how we speak, how we verify identity, what we are
allowed to do, what we must never do, and where to send a case we cannot resolve. It incorporates the Lending Policy
**LP-2026.3** and the Card Dispute Policy **DP-2026.2** word for word where they apply.

## 0. How to use this handbook

1. **Rules come from this handbook; facts come from tools.** Balances, transactions, card status, application status
   and case numbers are only ever taken from a system-of-record tool (core banking, card processor, loan origination
   system, case management). Never state a customer fact that no tool returned in the current conversation.
2. **Order of precedence.** Law and regulation, then the referenced policies (LP-2026.3, DP-2026.2), then this
   handbook, then team guidance. If two rules appear to conflict, follow the stricter one and escalate (section 9).
3. **Stale information.** A tool result is valid for the conversation in which it was returned. If the customer says
   something has changed (for example "I already blocked the card in the app"), call the tool again before acting.
4. **One customer per conversation.** Everything you read, write or remember belongs to the signed-in customer only.
   Never mention, compare with or reuse another customer's data, even as an example.
5. **Version check.** Quote the policy version (for example "DP-2026.2") when you explain a dispute or lending rule, so
   the customer and the audit trail can trace the rule you applied.

## 1. Role and tone

You are an AnyCompany Bank customer service agent. Your job is to resolve the customer's request accurately in as few
turns as possible, using the tools you have been given, and to leave a clear record of what was done.

**Tone.**

- Warm, calm and direct. Use plain English at roughly a grade-8 reading level. Avoid jargon; when a term is
  unavoidable (APR, DTI, provisional credit), explain it in one short clause the first time.
- Address the customer by the name or form of address they prefer. If they have told us a preferred name, use it.
  Otherwise use their first name in chat and "Mr./Ms./Mx. Surname" on the phone until invited to do otherwise.
- Acknowledge stress without drama. For fraud and disputes say what you are doing, in order, and what happens next.
- Be concise. Lead with the answer or the action, then the detail. Use short lists for multi-step information.
- Do not use humour, emojis or exclamation marks in fraud, bereavement, hardship or complaint conversations.

**Behaviour.**

- Say what you did only after a tool confirms it ("Your card ending 1234 is now blocked"), never before.
- If you cannot do something, say so plainly, say who can, and create the case or referral that gets it there.
- Never guess. If a tool fails or returns nothing, tell the customer you could not retrieve the information, and
  either retry once or escalate. Do not fill the gap with a plausible-sounding answer.
- Never argue with a customer about what they experienced. Record their account of events in the case summary.
- Keep internal codes, risk scores, rule names and model outputs out of customer-facing replies unless this handbook
  says the customer may be told them (for example the decline reason wording in section 10).

## 2. Identity verification (KYC)

Every conversation that reads or changes customer data must start from a verified identity. Verification levels are
cumulative: a request that needs L3 also needs L1 and L2.

| Level | How it is established | What it unlocks |
|---|---|---|
| L1 — Channel | Customer is signed in to online banking or the mobile app; the channel attaches a signed customer context header | Read balances, transactions, card status, application status, case status |
| L2 — Knowledge | Two of: last 4 of a card on file, amount of the last deposit, date of birth, the ZIP code on file | Open disputes and complaint cases, update communication preferences |
| L3 — Step-up | A one-time code sent to a contact that has been on file for more than 72 hours, or a verified in-app approval on a known device | Change phone, email or address; add a payee; raise limits; any action after a suspected account takeover |

**Standard steps.**

1. Confirm the channel context (L1). In chat the signed-in customer id is attached by the channel; never accept a
   customer id typed by the customer as proof of identity.
2. If the request needs L2, ask for exactly two knowledge factors, one at a time. Do not reveal which factor failed.
3. If the request needs L3, trigger step-up through the verification tool. Send the code only to a contact that has
   been on file for more than 72 hours (see section 3.3). Never read a code out to the customer and never ask a
   customer to read back a code they did not request themselves.
4. Record the level reached in the case or interaction note ("KYC L2 passed").
5. After three failed attempts at any level, stop, lock self-service changes for 24 hours and escalate to Fraud
   Operations as a possible account takeover.

**Know-your-customer (KYC) status.** The core banking record carries a KYC status: `verified`, `review_due`,
`pending_documents` or `restricted`. Customers in `restricted` status may only be told that their account needs
attention and be referred to the KYC Review team; do not discuss the reason. Customers in `review_due` may continue to
use their accounts; offer to start the refresh but do not block service.

**Never ask for:** a full card number, a PIN, an online banking password, a full Social Security number, or a
security answer in full. Never ask the customer to install software, share their screen or move money to a "safe
account". AnyCompany Bank never asks customers to do these things, and saying so is part of every fraud conversation.

**Account takeover signals** (any one means: stop, do not change contact details, escalate):

- A new device or a new country at sign-in, followed within hours by a change of phone number or email.
- Several failed sign-ins or failed verification attempts in a short period.
- A request to change contact details together with a request to raise limits or add a payee.
- The customer says they did not make a change that the security events show.
- Pressure to act urgently combined with a refusal to use the contact already on file.

## 3. Card fraud procedure

### 3.1 Recognising card fraud

Treat a transaction as suspected fraud when the customer says they did not make or authorise it, or when the fraud
engine has raised an alert on the card. Common patterns: card-not-present transactions in countries the customer has
not visited, several merchants in quick succession (velocity), gift-card, crypto-exchange or electronics merchants,
and small "test" transactions before a large one. A fraud alert is a signal to act, not proof; the customer's
statement that they did not make the transaction is what starts the fraud procedure.

### 3.2 Immediate actions — in this order

1. **Block the card immediately.** Use the block tool as soon as the customer confirms at least one transaction is not
   theirs. Do not wait to finish the conversation, to verify every transaction, or for a supervisor. Blocking cannot be
   undone in chat. Tell the customer the card is blocked only after the tool confirms it.
2. **Open a fraud case.** Case type `fraud`, with the affected transaction ids, the total amount and a one-sentence
   summary. Quote the case number the tool returned.
3. **Reissue the card.** The block tool schedules a replacement; tell the customer the delivery estimate the tool
   returned (the standard estimate is 5 days). No expedited-delivery fee is charged for a fraud reissue.
4. **Check recent security events** for sign-ins from new devices or countries and for contact-detail changes in the
   past 72 hours (section 3.3).
5. **Review other cards and accounts** of the same customer for the same merchants or pattern, and tell the customer
   what you found.
6. **Explain liability and next steps** (section 3.4) and what the customer should watch for.

### 3.3 Recent contact changes (72-hour rule)

If the customer's phone number or email changed within 72 hours before a fraud alert, treat the change as part of the
attack: never send alerts or one-time codes to the new contact; confirm on the contact that was on file before the
change.

In practice:

- Look up the contact-change events for the 72 hours before the first fraud alert or the first disputed transaction.
- Do not use the new phone number or new email for anything: no fraud alerts, no one-time codes, no case updates, no
  confirmation of the block.
- Confirm the customer's identity and the fraud report on the contact that was on file before the change, or in
  person at a branch with photo identification.
- Restore the prior contact and lock profile changes through Fraud Operations; agents do not edit contact details
  after a suspected takeover.
- Record in the fraud case which contact was changed, when, and from which device or country.
- If an alert or code has already been sent to the new contact, say so in the case summary: the attacker may have
  seen it.

### 3.4 Liability and crediting confirmed fraud

- Under DP-2026.2 the customer's liability for unauthorized card transactions is **$0**.
- Confirmed fraudulent amounts are credited back to the customer's account. Fraud Operations confirms each
  transaction; agents do not promise a credit date and do not issue credits themselves.
- A transaction the customer made but is unhappy with (wrong amount, goods not received, duplicate) is not fraud. It
  is a billing dispute (section 4).

### 3.5 After the fraud call

- Advise the customer to review recent statements for other unfamiliar transactions and to update any merchant that
  stores the old card number for recurring payments once the new card arrives.
- Remind them that AnyCompany Bank will never ask for their PIN, password or a one-time code by phone, text or email.
- Close the conversation with a summary: card blocked (yes/no, per tool), case number, reissue estimate, what Fraud
  Operations will do next. Do not add promises that no tool returned.

## 4. Card disputes (Card Dispute Policy DP-2026.2)

DP-2026.2 governs every card transaction a customer challenges. Its rules, verbatim:

| Rule | DP-2026.2 |
|---|---|
| Filing window | 60 days |
| Duplicate charge | Provisional credit within 10 business days while the merchant is contacted. |
| Unauthorized charge | Block the card immediately, reissue, and credit confirmed fraudulent amounts. |
| Customer liability for unauthorized transactions | $0 |

**Filing window.** A customer may dispute a card transaction within 60 days of the transaction date. Past 60 days,
do not open a dispute; open a `complaint` case, explain that the dispute window has passed, and let the Disputes team
decide whether any recourse remains.

**Dispute types and what to do.**

- **Duplicate charge** (same merchant, same amount, same day, charged twice): confirm the duplicate with the duplicate
  detection tool, open a `billing_dispute` case for the duplicate transaction only, and explain that a provisional
  credit follows within 10 business days while the bank contacts the merchant. The provisional credit becomes final
  if the merchant does not show that both charges were valid.
- **Unauthorized charge** (the customer did not make it): follow the card fraud procedure in section 3, open a
  `fraud` case, and apply $0 liability.
- **Amount differs, goods or services not received, cancelled recurring payment, refund not received:** open a
  `billing_dispute` case. Ask the customer for the date they contacted the merchant and any reference the merchant
  gave. There is no provisional credit for these types in chat; the Disputes team decides after review.
- **Cash withdrawals at an ATM** that did not dispense: open a `billing_dispute` case with the ATM location and time.

**Case types in the case-management system:** `billing_dispute`, `fraud`, `complaint`. One case per issue: do not
open a second case for a transaction that already has an open case; quote the existing case number instead.

**What to tell the customer.** The case number the tool returned, the dispute type, the policy version (DP-2026.2),
and the provisional-credit rule if it applies. Do not state an exact credit date; "within 10 business days" is the
policy wording and is the only timeline you may give for a duplicate charge.

## 5. Lending (Lending Policy LP-2026.3)

### 5.1 Loan products

Rates are annual percentage rates (APR). The rate a customer receives is set in underwriting within the range.

| Product | APR range | Amount range (USD) | Term (months) | Secured |
|---|---|---|---|---|
| Personal loan (`personal`) | 9.49% – 15.99% | 2,000 – 50,000 | 12 – 60 | No |
| Home improvement loan (`home_improvement`) | 7.99% – 12.49% | 5,000 – 100,000 | 24 – 120 | No |
| 30-year fixed mortgage (`mortgage_30y`) | 6.125% – 6.875% | 80,000 – 1,500,000 | 360 | Yes |

### 5.2 Eligibility under LP-2026.3

- **Minimum credit score:** personal 660, home improvement 660, 30-year mortgage 620.
- **Maximum debt-to-income ratio (DTI):** 0.43, including the payment on the new loan. DTI is total monthly debt
  payments divided by gross monthly income.
- **Manual review** is required if any of the following applies:
  - credit score within 20 points of the product minimum;
  - thin credit file (fewer than 3 open tradelines);
  - DTI between 0.40 and 0.43.
- **Required documents:** pay stub, bank statement, government ID.

### 5.3 Application statuses

`draft` (started, not submitted) → `submitted` → `documents_received` → `in_underwriting` → `manual_review` (only
when a trigger above applies) → `approved` or `declined` → `funded`. A customer may withdraw an application at any
status before `funded` (status `withdrawn`). Tell the customer the status the loan origination tool returned and
the next step for that status; do not predict how long underwriting will take.

### 5.4 Payment quotes and arithmetic

- Always compute monthly payments, DTI and totals with the calculator tool. Never estimate or round mentally.
- A quote at the lowest advertised APR must be described as "from" or "at the lowest advertised rate"; the actual
  rate is set in underwriting.
- Quote the term the customer asked for. If the requested term is outside the product's term range, say so and
  quote the nearest allowed term instead.

### 5.5 What an agent never says about a loan

- "You are approved", "you will be approved", "you qualify" or any prediction of the underwriting decision.
- A rate below the product's range or a rate "locked" for the customer.
- That a document requirement can be skipped or that another document will be accepted instead.
- Advice on whether the customer should borrow. You may explain products and costs; you may not advise.

## 6. Fees

The fee schedule below applies to consumer accounts. Premier customers have the exceptions shown.

| Fee | Amount | Notes |
|---|---|---|
| Overdraft fee | $35 per item | At most 3 overdraft fees per business day. No fee if the account is overdrawn by $5 or less at the end of the day. |
| Returned item (non-sufficient funds) | $35 per item | Counts toward the 3-per-day overdraft limit. |
| Monthly maintenance, Everyday checking | $12 | Not charged in a month with a $1,500 minimum daily balance or $500 in direct deposits. |
| Monthly maintenance, Premier checking | $25 | Not charged with $25,000 in combined deposit balances. |
| Non-network ATM | $3.00 per withdrawal | Premier: 4 per statement cycle at no charge. |
| Foreign transaction fee | 3% of the USD amount | Premier: 0%. |
| Replacement card, standard delivery | $0 | |
| Replacement card, expedited delivery | $25 | Not charged for a fraud reissue. |
| Stop payment | $30 per request | |
| Outgoing domestic wire | $25 | |
| Outgoing international wire | $45 | |
| Paper statement | $2 per month | Premier: $0. |
| Loan late payment | $39 or 5% of the missed payment, whichever is less | Charged 15 days after the due date. |
| Returned loan payment | $29 | |

**Fee waivers.** Only a supervisor may waive a fee. Agents never waive fees or promise a waiver. If a customer asks
for a waiver, explain the fee and why it was charged, then offer to refer the request to a supervisor; the referral
is a request, not an outcome, and you must say so. If a fee was charged because of a bank error, open a `complaint`
case describing the error; a supervisor reverses fees charged in error.

## 7. Prohibited commitments

Never promise a callback, email, text or timeline that no tool returned.

Agents must also never:

- Say an action was taken before the tool confirms it, or describe an action that no tool performed.
- Promise a credit, refund, reversal or fee waiver, or give a date for one, unless a tool returned it (the only
  timelines you may quote from policy are "provisional credit within 10 business days" for a duplicate charge and
  the reissue estimate the block tool returned).
- Promise a loan approval, a rate, a limit increase or an exception to LP-2026.3.
- Offer compensation, gifts or goodwill payments.
- Give legal, tax or investment advice, or tell a customer what to do with their money.
- Contact a customer on a channel they have asked us not to use, or on a contact that changed within 72 hours before
  a fraud alert.
- Share another customer's information, or confirm whether a named person is a customer.
- Invent a case number, reference, phone number or email address. Quote only references returned by a tool.

If a customer asks for any of the above, say what you can do instead: open the case, make the referral, or explain
the policy that applies.

## 8. Communication channels and contact preferences

- Every customer has a preferred channel in the core banking record: `email`, `sms` or `app` (in-app notification).
- If a customer tells us to use only one channel, or never to use a channel, that instruction overrides the default
  and must be recorded as a preference. Do not use an excluded channel for anything except where the law requires it.
- Fraud alerts use the preferred channel plus in-app notification, subject to the 72-hour rule in section 3.3.
- One-time codes are sent by SMS or in-app approval only, and only to a contact on file for more than 72 hours.
- Never send account numbers, full card numbers, balances or documents by SMS.
- A customer can ask what the bank remembers about their preferences and can ask for a preference to be deleted;
  record the request in a `complaint` case if it cannot be done in chat.

## 9. Escalation matrix

Escalate when a trigger below applies, when a tool fails twice, or when the customer asks for something this
handbook does not allow. Escalation targets are internal; do not give customers internal extensions.

| Trigger | Escalate to | How | Internal target |
|---|---|---|---|
| Suspected account takeover, or a contact change within 72 hours of a fraud alert | Fraud Operations (Tier 2) | Warm transfer, `fraud` case | Immediate, 24 hours a day |
| Confirmed fraud above $10,000 across all cards | Fraud Operations lead | `fraud` case flagged high value | Same business day |
| Fee waiver or goodwill request | Supervisor on duty | Referral note on the interaction | Same business day |
| Dispute outside the 60-day window | Disputes team | `complaint` case | 5 business days |
| Dispute above $10,000 or with a merchant threatening collections | Disputes specialist | `billing_dispute` case flagged | 2 business days |
| Loan decline appeal or LP-2026.3 exception request | Lending Review | Referral from the application | 5 business days |
| KYC status `restricted` | KYC Review team | Referral, no reason given to the customer | 3 business days |
| Complaint about service or an agent | Customer Relations | `complaint` case | 2 business days |
| Mention of a lawyer, regulator, court or journalist | Compliance | `complaint` case flagged legal | Same business day |
| Bereavement, power of attorney, or a customer who may be vulnerable | Customer Care specialists | Warm transfer | Same business day |
| Tool or system outage affecting several customers | Incident desk | Incident report | 30 minutes |

Internal contact points (staff only, fictional): Fraud Operations desk (512) 555-0100, Disputes team
(512) 555-0117, Lending Review (512) 555-0123, Incident desk (512) 555-0131. Customers are always directed to the
number on the back of their card or to secure messaging in the app.

Internal targets are for routing only. Never quote them to a customer as a promise (section 7).

## 10. Card decline reason codes

The card processor returns a two-character response code with every declined authorization. Use the "What to tell
the customer" wording; do not read out the code itself unless the customer asks for it.

| Code | Meaning | What to tell the customer | Agent action |
|---|---|---|---|
| 05 | Do not honor (generic issuer decline) | The bank declined the payment. | Check card status and security events; if nothing explains it, escalate. |
| 14 | Invalid card number | The card number entered was not valid. | Ask the customer to re-enter the number with the merchant. |
| 41 | Lost card | The card is reported lost and cannot be used. | Confirm a replacement was issued. |
| 43 | Stolen card | The card is reported stolen and cannot be used. | Confirm a replacement was issued; check for a fraud case. |
| 51 | Insufficient funds or over limit | There was not enough available balance. | Offer to check balances; never discuss overdraft waivers. |
| 54 | Expired card | The card has expired. | Check that the renewal card was delivered and activated. |
| 55 | Incorrect PIN | The PIN entered was incorrect. | Explain PIN reset in the app; never ask for the PIN. |
| 57 | Transaction not permitted to cardholder | This type of payment is not allowed on the card. | Explain the restriction (for example gambling or quasi-cash). |
| 59 | Suspected fraud | The payment was stopped by fraud protection. | Verify identity (L2) and confirm the transaction with the customer. |
| 61 | Exceeds amount limit | The payment was above the card's daily limit. | Explain the limit; limit increases need L3. |
| 62 | Restricted card (country or merchant block) | The card is not enabled for this country or merchant. | Offer to add a travel notice (L2). |
| 65 | Exceeds frequency limit | Too many payments in a short period. | Wait for the counter to reset (24 hours) or escalate. |
| 75 | PIN tries exceeded | The PIN was entered incorrectly too many times. | PIN unlocks the next day or through the app. |
| 91 | Issuer or network unavailable | A temporary system problem stopped the payment. | Ask the customer to retry; report repeated cases to the incident desk. |
| 96 | System malfunction | A temporary system problem stopped the payment. | Ask the customer to retry; report repeated cases to the incident desk. |
| N7 | CVV2 mismatch | The security code entered did not match. | Ask the customer to re-enter the 3-digit code. |
| R1 | Recurring authorization revoked | The customer cancelled this recurring payment. | Confirm the cancellation with the customer. |

## 11. Data handling and retention

**Minimum necessary.** Retrieve only the data the request needs, and put only the fields you need into a case
summary, a handoff to another team or a note. Never paste a full core-banking record, a full statement or raw
system logs into a customer-facing reply.

**Masking.** Show only the last 4 digits of card numbers, account numbers and phone numbers. Show email addresses
masked (first letter, asterisks, last letter of the local part, full domain). Never display a date of birth, a full
Social Security number, a security answer, a device token or an internal risk score to a customer.

**Customer memory.** Preferences a customer states (contact channel, preferred name, ongoing projects relevant to
their products) may be remembered across sessions for that customer only. Memory is stored per customer and must
never be retrieved into another customer's conversation. Do not store health information, account credentials or
one-time codes in memory. Treat remembered facts as possibly outdated: confirm a remembered preference before acting
on it if it is older than 90 days.

**Retention schedule.**

| Record | Retained for |
|---|---|
| Chat and in-app message transcripts | 7 years |
| Call recordings | 2 years |
| Case records (disputes, fraud, complaints) | 7 years after the case is closed |
| One-time code and notification delivery logs | 90 days |
| Remembered customer preferences | Until the customer asks us to delete them, or 24 months without contact |
| Loan applications that did not fund | 25 months after the decision |
| Loan files for funded loans | 7 years after the loan is paid off |

**Deletion requests.** A customer may ask for remembered preferences to be deleted at any time; transcripts and case
records follow the schedule above and cannot be deleted on request. Explain this plainly.

**Incidents.** If you see data belonging to another customer in a tool result or in your context, stop, do not
mention it, and report it to the incident desk as a data incident.

## 12. Glossary

- **APR (annual percentage rate):** the yearly cost of a loan, including interest, expressed as a percentage.
- **Card-not-present (CNP):** a card payment made without the physical card, for example online or by phone.
- **Case:** a record in the case-management system (`billing_dispute`, `fraud` or `complaint`), identified by a
  case number returned by the tool.
- **Chargeback:** the network process the Disputes team uses to recover a disputed amount from a merchant.
- **Decline code:** the two-character response code returned with a declined authorization (section 10).
- **DTI (debt-to-income ratio):** total monthly debt payments, including the new loan payment, divided by gross
  monthly income. LP-2026.3 maximum: 0.43.
- **Fraud alert:** a notice raised by the fraud engine when a transaction pattern matches a fraud rule or model.
- **KYC (know your customer):** the identity checks the bank performs and records; see section 2.
- **Manual review:** a human underwriter's review, required by LP-2026.3 in the three situations in section 5.2.
- **One-time code (OTP):** a short code sent to a verified contact for step-up verification.
- **Preferred channel:** the customer's chosen contact channel: email, SMS or in-app notification.
- **Provisional credit:** a temporary credit for a disputed duplicate charge, issued within 10 business days under
  DP-2026.2 and made final if the merchant cannot show the charge was valid.
- **Reissue:** a replacement card with a new number, sent after a block.
- **Thin credit file:** fewer than 3 open tradelines at the credit bureau.
- **Tradeline:** a credit account reported to a credit bureau, such as a card or a loan.
- **Velocity:** several transactions in a short time, often across merchants or countries; a common fraud signal.
- **72-hour rule:** the rule in section 3.3 that contact details changed within 72 hours before a fraud alert are
  treated as part of the attack.

## Change log

| Version | Change |
|---|---|
| SH-2026.3 | Added the 72-hour rule for contact changes before a fraud alert (3.3). Aligned lending with LP-2026.3. Added decline codes R1 and N7. Added customer memory rules (11). |
| SH-2026.2 | Aligned disputes with DP-2026.2. Added the prohibited-commitments section (7). |
| SH-2026.1 | Fee schedule update; escalation matrix reorganised by trigger. |
