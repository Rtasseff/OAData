# Sending email from the Project Office address

**Goal:** reminder and completion emails go out from
`PublicationsData@biomagune.onmicrosoft.com` (the address users already
know from the protocol) instead of the operator's personal address.

All Microsoft references below were re-checked against Microsoft Learn
on **2026-10-27** (every link opened; cmdlet pages now live under
`/powershell/module/exchangepowershell/`, and the Exchange admin center
moved to `admin.cloud.microsoft/exchange`). Microsoft moves UI labels
around; if a label doesn't match, trust the PowerShell commands — they
change far less often.

---

## What to ask IT (the request as sent, copy-paste ready)

The whole ask is **one Exchange Online permission: "Send As" for the
operator on the office address**. No app registration, no admin consent
(the 2026-10-27 spike below showed `Mail.Send.Shared` is user-consentable
here), no licence, no mailbox access.

> **Subject:** Request: "Send As" permission on PublicationsData@biomagune.onmicrosoft.com
>
> Hello,
>
> Management has approved sending the Open Access data-archiving emails
> (reminders and confirmations to authors) automatically from the
> PublicationsData address instead of from my personal mailbox.
>
> **What I need:** one Exchange Online permission — **Send As** on
> `PublicationsData@biomagune.onmicrosoft.com` for my account
> `rtasseff@cicbiomagune.es`.
>
> Nothing else is needed:
> - No app registration, admin consent or licence. Our existing
>   "OA Archive Tracker" app already has what it needs; I tested sending
>   with it today from my own address and it works.
> - Send As does not give me access to the PublicationsData mailbox.
>   Microsoft: it "doesn't allow the delegate to read the contents of the
>   mailbox". I am not asking for Full Access or Send on Behalf.
>
> **How (about 2 minutes, Exchange Online PowerShell):**
>
> 1. Connect ([how to connect](https://learn.microsoft.com/en-us/powershell/exchange/connect-to-exchange-online-powershell?view=exchange-ps)):
>    `Connect-ExchangeOnline -UserPrincipalName <your admin account>`
> 2. Optional check of what kind of address it is
>    ([Get-Recipient](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-recipient?view=exchange-ps)):
>    `Get-Recipient PublicationsData@biomagune.onmicrosoft.com | Format-List Name,RecipientTypeDetails`
>    I expect `GroupMailbox` (the Microsoft 365 group behind the
>    PublicationsData SharePoint site); `SharedMailbox` is also possible.
>    Step 3 is the same command for both.
> 3. Grant Send As
>    ([Add-RecipientPermission](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/add-recipientpermission?view=exchange-ps);
>    it pauses to confirm, or add `-Confirm:$false`):
>    `Add-RecipientPermission -Identity "PublicationsData@biomagune.onmicrosoft.com" -Trustee "rtasseff@cicbiomagune.es" -AccessRights SendAs`
> 4. Verify
>    ([Get-RecipientPermission](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/get-recipientpermission?view=exchange-ps));
>    it should list `SendAs`:
>    `Get-RecipientPermission -Identity "PublicationsData@biomagune.onmicrosoft.com" -Trustee "rtasseff@cicbiomagune.es"`
>
> **Or in the [Exchange admin center](https://admin.cloud.microsoft/exchange):**
> - Microsoft 365 group: **Recipients → Groups** → select PublicationsData
>   → **Settings** → under **Manage delegates** click
>   **Edit manage delegates** → add `rtasseff@cicbiomagune.es` with
>   **Send as** → **Save**.
> - Shared mailbox: **Recipients → Mailboxes** → select it →
>   **Mailbox delegation** → **Send as** → **Edit** → **Add members** →
>   `rtasseff@cicbiomagune.es` → **Save** → **Confirm**.
>
> Microsoft's guide for both routes:
> [Manage permissions for recipients in Exchange Online](https://learn.microsoft.com/en-us/exchange/recipients-in-exchange-online/manage-permissions-for-recipients).
> See the sections "Use the EAC to assign permissions to groups" and
> "Use Exchange Online PowerShell to assign the Send As permission to
> mailboxes and groups".
>
> **To undo it at any time:** the same command with `Remove-RecipientPermission`
> ([docs](https://learn.microsoft.com/en-us/powershell/module/exchangepowershell/remove-recipientpermission?view=exchange-ps)).
>
> For background, the app sends through Microsoft Graph as described in
> [Send Outlook messages from another user](https://learn.microsoft.com/en-us/graph/outlook-send-mail-from-other-user),
> which needs exactly this Send As grant.
>
> Please let me know when it's done. I'll immediately send a test from
> PublicationsData to myself and confirm.
>
> Thank you,
> Ryan

Notes behind the request (from the reference page, last updated
2026-08-03):

- "Send as: Allows the delegate to send messages as if they came directly
  from the mailbox or group. There's no indication that the message was
  sent by the delegate." — this is what we want (clean `From:`).
- "Send on behalf" would instead show "*Ryan on behalf of
  PublicationsData*" — acceptable fallback, but not preferred. "If a user
  has both Send as and Send on behalf permissions to a mailbox or group,
  the Send as permission is always used."
- The same page warns: "If you assign the Send As permission to a
  mailbox that is hidden from address lists, the delegate won't be able
  to send messages from the mailbox." If the test still fails with
  `ErrorSendAsDenied` after the grant has had time to take effect, ask IT
  whether PublicationsData is hidden from address lists.

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

## Sending through Microsoft Graph (approved 2026-10-27)

Management approved automatic sending from PublicationsData on
2026-10-27 (projectoffice@ was the other allowed sender; it is a
distribution list with no mailbox, so there would be no Sent Items and
replies would go to every member, so PublicationsData is preferred).

**How it works:** the tracker signs in with the operator's account on
the existing `OA Archive Tracker` Entra app (device code, token cached
like the SharePoint one) and calls `POST /me/sendMail` with the message's
`from` set to PublicationsData. Exchange Send As makes the recipient see
only PublicationsData. The sent copy lands in the operator's Sent Items.

- Scope: delegated **`Mail.Send.Shared`** ("Send mail on behalf of
  others", `AdminConsentRequired: No`), named by
  [Send Outlook messages from another user](https://learn.microsoft.com/en-us/graph/outlook-send-mail-from-other-user)
  as the scope for sending from another mailbox/group/list. See also
  [user: sendMail](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0)
  and the [permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference)
  (all checked 2026-10-27). The 2026-07-02 note here saying
  `Mail.Send.Shared` had disappeared from the reference was wrong.
- **Spike 2026-10-27:** device-code sign-in requesting `Mail.Send.Shared`
  → granted by user consent, **no admin approval needed**. `/me/sendMail`
  to self → `202`, delivered. Same with `from: PublicationsData@…` →
  `403 ErrorSendAsDenied` (expected: Send As not granted yet). So the only
  IT ask is the Send As grant above. After it, re-run the from-PublicationsData
  send to self: expect `202`, and the received message shows only
  PublicationsData as sender.

**Rollout** (new paths go through the operator first, then get promoted):

1. IT grants Send As → re-run the spike send → set
   `[email] sender_email` (the `.eml` drafts pick it up too).
2. **Send from the web page:** a "Send" button on each email draft. The
   operator reads the draft and presses it, and it goes out from
   PublicationsData. The matching "email sent" step is then recorded
   automatically (audit source `web:<user>`). The From is always
   PublicationsData; the event records who pressed the button.
3. **Automatic sending** by `oa auto`, switched on per email kind
   (reminder, completion, handover, …) in `[automation]` once step 2 has
   run for a while without surprises. If the cached login has expired,
   the run holds the emails and says so in the digest. It never drops
   them silently.
