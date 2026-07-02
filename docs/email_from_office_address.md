# Sending email from the Project Office address

**Goal:** reminder and completion emails go out from
`PublicationsData@biomagune.onmicrosoft.com` (the address users already
know from the protocol) instead of the operator's personal address.

All Microsoft references below were checked against Microsoft Learn on
**2026-07-02** (doc dates noted inline). Microsoft moves UI labels around;
if a label doesn't match, trust the PowerShell commands — they change far
less often.

---

## What to ask IT (copy-paste ready)

The whole ask is **one Exchange Online permission: "Send As" for the
operator on the office address**. No app registration, no Graph consent,
no license change. Two steps:

### Step 0 — identify what kind of recipient the address is

`PublicationsData@biomagune.onmicrosoft.com` is most likely the
**Microsoft 365 Group** behind the PublicationsData team site, but it
could be a shared mailbox or distribution list. IT can check in one line
(Exchange Online PowerShell):

```powershell
Get-Recipient PublicationsData@biomagune.onmicrosoft.com | Format-List Name,RecipientTypeDetails
```

- `GroupMailbox` → it's a Microsoft 365 Group → use **Path A**
- `SharedMailbox` → use **Path B**
- `MailUniversalDistributionGroup` → Path A's PowerShell command works the same

### Path A — Microsoft 365 Group

Exchange admin center (https://admin.exchange.microsoft.com):

1. **Recipients → Groups** → select the group.
2. Open **Settings**, then under **Manage delegates** click
   **Edit manage delegates**.
3. Under **Add a delegate**, add the operator
   (`rtasseff@cicbiomagune.es`), permission type **Send as**. **Save.**

PowerShell equivalent (one line):

```powershell
Add-RecipientPermission -Identity "PublicationsData@biomagune.onmicrosoft.com" -Trustee rtasseff@cicbiomagune.es -AccessRights SendAs
```

Verify:

```powershell
Get-RecipientPermission -Identity "PublicationsData@biomagune.onmicrosoft.com" -Trustee rtasseff@cicbiomagune.es
```

### Path B — shared mailbox

Exchange admin center:

1. **Recipients → Mailboxes** → select the mailbox.
2. **Mailbox Delegation** → under **Send as**, click **Edit** →
   **Add members** → add the operator. **Save → Confirm.**

PowerShell is identical to Path A (`Add-RecipientPermission ... -AccessRights SendAs`).

Notes for IT, straight from the reference:

- "Send as: Allows the delegate to send messages as if they came directly
  from the mailbox or group. There's no indication that the message was
  sent by the delegate." — this is what we want (clean `From:`).
- "Send on behalf" would instead show "*Ryan on behalf of
  PublicationsData*" — acceptable fallback, but not preferred. "If a user
  has both Send as and Send on behalf permissions to a mailbox or group,
  the Send as permission is always used."
- Send As does **not** grant the operator any right to read the mailbox
  (that would be the separate Full Access permission — not requested).

Reference (retrieved 2026-07-02; page dated 2024-03-14, last updated
2026-05-20):
[Manage permissions for recipients in Exchange Online](https://learn.microsoft.com/en-us/exchange/recipients-in-exchange-online/manage-permissions-for-recipients)

Practical note (experience, not doc-guaranteed): permission changes in
Exchange Online can take a while to propagate. If a test send fails right
after the grant, wait an hour and retry before reporting it broken.

---

## What the operator does once it's granted

1. **In `config.toml`:**

   ```toml
   [email]
   sender_email = "PublicationsData@biomagune.onmicrosoft.com"
   ```

   `oa emails` already writes this into the `From:` header of every
   `.eml` draft (`emails._render_eml`), so drafts open in Outlook
   pre-addressed **from the office address**.

2. **Test send (also the acceptance test for IT's change):** open one
   `.eml` draft, send it to yourself. In classic Outlook, if the From
   line isn't shown: **Options → From** to display it, then
   **From → Other Email Address** and pick the office address.
   The received message must show only
   `PublicationsData@biomagune.onmicrosoft.com` as the sender (no "on
   behalf of").

3. **Replies** go to the office mailbox, not the operator's inbox — that
   is the point (users already email that address per the protocol), but
   make sure it's monitored as part of the weekly session.

---

## Later (optional): fully automatic sending via Graph

Not built, and deliberately so — sending unreviewed reminder emails is a
separate promotion decision, and it needs one more IT conversation. What
we verified today so the future ask is precise:

- The Graph call is `POST /users/{address}/sendMail`. The least-privileged
  **delegated** scope is **`Mail.Send`** ("Send mail as a user",
  `AdminConsentRequired: No` in the permissions reference — though this
  tenant can override consent defaults, so a spike must confirm).
  Retrieved 2026-07-02:
  [user: sendMail](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0),
  [permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference).
- Caution for future work: older guidance mentions a `Mail.Send.Shared`
  scope for shared-mailbox sending; it **no longer appears** in the
  current permissions reference (checked 2026-07-02). Do not put it in an
  IT ask without re-verifying at implementation time.
- The Exchange **Send As** grant above is required for the Graph path
  too, so getting it now is a prerequisite either way — nothing is wasted.

The pattern would mirror the SharePoint track: add `Mail.Send` as a
delegated scope on the existing `OA Archive Tracker` app registration,
spike a one-line send, then gate auto-send per signal class like
everything else. Raise it only after the current automation has earned
trust — and note the operator's standing rule: no asks to this IT contact
without a tested, documented method in hand.
