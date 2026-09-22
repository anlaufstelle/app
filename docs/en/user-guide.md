> This is the English translation of [user-guide.md](../user-guide.md).
> Synchronization update 2026-09-22: § 8 now states that a sign-in, second-factor or password-change request during the transfer is not a save -- the transfer pauses, local entries stay, and it continues without duplicates after signing in (#2289).
> Synchronization update 2026-09-18: the handover-note section now states the final no-hold boundary and the organizational step for procedure-relevant content (#2231).
> Synchronization update 2026-09-15: initial 2FA setup now mirrors the mandatory backup-code acknowledgement and the increasing wait after repeated incorrect codes in the German guide (#2248).
> Most recently synchronized on 2026-09-10 (§ 2 offline capture rejects the doubled hour with an explanatory message, #2187; § 4 merging duplicate clients: reverting a merge is not possible while a legal hold covers either record, #2200; § 2 the manual handover note above the shift statistics: who may write it, the editing window until the shift ends, the comparison of both versions when two people save at the same time, and its retention period, #2042).
> Previously synchronized on 2026-09-08 (§ 2 capturing the doubled hour on the fall-back night -- the occurrence choice "Which of the two hours?", the timestamp being read-only when editing, and the spring-forward hour staying rejected, #2127; intro line opened to open consultation hours/advice centers, awareness teams, women's shelters, and youth work, #2140). Previously synchronized on 2026-09-07 (§ 1 quick exit and privacy screen, including the four named limits -- browser history, second window, dropped connection, and a stalled or failed deletion of the local key, #2136). Previously synchronized on 2026-09-02 (§ 2 shift filter and daylight-saving changes, #2057). Previously synchronized on 2026-09-02 (§ 7 self-withdrawal of a deletion request, #2092). Previously synchronized on 2026-08-24 (§ 3 authorized roles see decrypted before-values in event change history, #1961). Previously synchronized on 2026-08-22 (§ 10 deleting a case: the two paths, the trash, and what happens to assigned contacts, #1818). Previously synchronized on 2026-08-22 (§ 9 the System Admin creates the first facility in the Django admin -- the `/system/` area only displays it, #1894). Previously synchronized on 2026-08-15 (§ 2 tally opt-in and inline numeric capture, #1637, #1906).
> The German version is the authoritative source. Last synced: 2026-07-12 (Offline V2 Wave 3, #1499), § 8 offline section only (#1417), 2026-07-14 (§ 1 passkey second factor [#1492], § 7 PDF export rename, #1551), 2026-08-03 (§ 7 reports section, #1061; § 7 data-source declaration per report artifact, #1743) 2026-08-04 (§ 2a Lead tile removed the Legal Holds tile, #1759) and 2026-08-04 (§ 3 file attachments on clients and cases, removing a file, type change discards values and files; § 3a "Assigned to" column, visibility and filters across all three owners, #1738; plus the § 9 audience note the 2026-07-14 re-sync had missed, #1785) and 2026-08-11 (§§ 3-5, 7-10: renamed contact levels "Pseudonym only"/"With additional details" and the create-action wording "Document contact"/"Create person"/"Create task"/"Create case", #1805) and 2026-08-12 (§ 7 report selection: cards instead of a dropdown, "Dataset Reports" tab, PDF export moved onto the report card, #1804) and 2026-08-13 (§ 2a role landing redirects, #1813; § 6 search results page, #1803).

# Anlaufstelle -- User Guide

This guide is intended for social workers, managers, and assistants working in open consultation hours and advice centers, drop-in centers, emergency shelters, awareness teams, women's shelters, youth work, and other low-threshold social service facilities that use Anlaufstelle.

---

## Table of Contents

1. [Login and Password](#1-login-and-password)
2. [Home -- Zeitstrom](#2-home--zeitstrom)
   - [Tally](#tally)
   - [Work Center](#2a-work-center)
3. [Documenting a Contact (Creating an Event)](#3-documenting-a-contact-creating-an-event)
   - [Files Overview](#3a-files-overview)
4. [Managing Clients](#4-managing-clients)
5. [Hints and Tasks (Work Items)](#5-hints-and-tasks-work-items)
6. [Search](#6-search)
7. [Statistics and Export](#7-statistics-and-export)
8. [Installing the PWA and Working Offline](#8-installing-the-pwa-and-working-offline)
9. [Roles and Permissions](#9-roles-and-permissions)
10. [Case Management](#10-case-management)

---

## 1. Login and Password

### Signing In

1. Open your facility's Anlaufstelle URL in a browser (e.g., `https://anlaufstelle.meine-einrichtung.de`).
2. You will be automatically redirected to the login page (`/login/`).
3. Enter your **username** and **password**.
4. Click **Sign In**.

> **Note:** If you were told to change your password on first login, you will be redirected to the password change page immediately after signing in.

> **Account locked after multiple failed attempts?** After **10 failed sign-in attempts**, your account is automatically locked. You will then see a corresponding notice page and can no longer sign in. On the login page you will find three self-service options ([Refs #869](https://github.com/anlaufstelle/app/issues/869)):
>
> - **Forgot password?** -- Classic reset by email. Setting a new password automatically unlocks your account.
> - **Unlock account by email** -- Sends an unlock link to your registered email address, valid for 30 minutes. No new password required.
> - **Redeem a backup code** -- If you have 2FA enabled, enter your username plus one of your backup codes. Each code is single-use.
>
> If none of these options works, ask an administrator to unlock the account. All actions are recorded in the audit log.

### Changing Your Password

1. Click on your name or the user menu in the top-right corner.
2. Select **Change Password** (URL: `/password-change/`).
3. Enter your current password.
4. Enter the new password twice.
5. Click **Save**.

> **Tip:** Choose a secure password (at least 12 characters, including upper and lower case letters and digits). The system enforces minimum requirements.

> **Inviting new users:** The first invitation for a new user now arrives as a token link (no more plaintext initial password). The first time you open the link, you set your own password.

### Two-Factor Authentication (2FA)

Anlaufstelle supports time-based one-time passwords (TOTP) as a second login factor. Administrators can enforce 2FA for individual users or the whole facility; independently, each user can enable 2FA voluntarily.

**Initial setup:**

1. Click your name in the top-right corner > **Two-Factor Authentication** (URL: `/mfa/settings/`).
2. Click **Set up 2FA** -- a QR code is displayed.
3. Install an authenticator app and scan the QR code. Tested apps:
   - **Google Authenticator** (Android/iOS)
   - **Microsoft Authenticator** (Android/iOS)
   - **Authy** (Android/iOS/Desktop)
   - **FreeOTP / FreeOTP+** (Android, open source)
   - **1Password**, **Bitwarden**, **Proton Pass** (as built-in authenticator)
4. Enter the 6-digit code shown by the app and click **Confirm & activate**.
5. Store the displayed backup codes securely, select the required **I have stored the codes safely.** checkbox, and confirm. Setup is complete only after this step. If you leave the page first, the next attempt starts again at `/mfa/setup/`.

> **Tip:** If the QR code cannot be scanned, click **Enter secret manually** and copy the string into the app (field "Secret" / "Key" -- Base32, no spaces). In the app, pick type **TOTP / time-based**.

**Signing in with 2FA:**

1. Enter username and password as usual.
2. On the next page, enter the 6-digit code currently shown by the app.
3. Each code is valid for **30 seconds** -- if it expires, just use the next one.

After several incorrect entries, `django-otp` increasingly throttles further checks. A correct code can therefore also be rejected during that waiting period; wait for the lockout interval instead of entering more codes in quick succession.

**Disabling 2FA:**

Under `/mfa/settings/` > **Disable 2FA**. This is **not possible** if your facility enforces 2FA or your account is individually marked as 2FA-required -- contact your administrator in that case.

**Backup codes (for emergencies):**

When setting up 2FA, you receive **10 single-use backup codes**. Store them safely -- e.g., printed and kept in your wallet, or in your password manager. If you lose your phone or your authenticator app has been reset, you can enter a backup code instead of a TOTP code at the 2FA login screen. Each code only works once.

> **All codes used up or lost?** Contact your administrator -- they can reset your TOTP device, after which you set up 2FA and new backup codes again.

**Passkey / security key (optional, in addition to the authenticator app):**

In addition to the authenticator app, you can register a **passkey** as a second factor -- e.g., your device's biometrics (fingerprint/face), a smartphone, or a USB security key (FIDO2). A passkey is phishing-resistant because it is bound to the application's address.

1. Set up the authenticator app (TOTP) **first** -- a passkey is only possible **in addition** to it. This keeps your backup codes as the recovery path in case a passkey is lost.
2. Under `/mfa/settings/`, in the **Passkeys** section, click **Add passkey** and follow the browser's instructions.
3. You can register multiple passkeys (e.g., laptop and smartphone) and **remove** individual ones again (requires re-confirming your password/2FA).

At login, the 2FA page then offers **Sign in with passkey** -- alternatively, you can still use the authenticator app or a backup code.

> **Note:** Passkeys are bound to the web address. A passkey registered on `dev.anlaufstelle.app` will not work on a different address -- register it where you actually use the application.

### Signing Out

Click **Sign Out** in the top-right corner. For data protection reasons, you will be automatically signed out after a configured period of inactivity (default: 30 minutes).

### Quick Exit

At the top of every page there is a **Quick exit** button. It is meant for situations where someone is reading along or unexpectedly standing next to you -- at a reception desk, in a women's shelter, when documenting in a public place.

One click does three things, in this order:

1. The screen content is **covered immediately**.
2. You are **signed out** -- the session ends on the server as well, not just in the browser.
3. The browser navigates to a **neutral page** (your facility decides which one; the default is the Wikipedia home page).

Two **keyboard shortcuts** trigger the same action, including from inside a text field:

| Shortcut | Note |
|---|---|
| `Ctrl` + `Shift` + `X` | on macOS `Cmd` + `Shift` + `X` |
| `Shift` + `Esc` | alternative if the first combination is taken |

> **Unsaved input is lost in the process.** That is not special behavior of this button: every sign-out clears the locally cached drafts. If you have time, save first; if you do not, the protection matters more than the draft.

Pressing **Back** in the browser does not bring the application back: the page covers itself again and reloads -- you end up on the sign-in form.

**Without an internet connection** the button works as well: the content is covered, the local key for the offline data is deleted, and the browser navigates to the neutral page. Signing out on the server is not possible offline -- but the offline data is unreadable without signing in again, the offline authorization expires after 48 hours anyway, and restarting the device clears it.

#### What the button cannot do

One thing is certain: **your screen is cleared immediately.** Everything beyond that the application attempts as best it can -- four limits remain, and they are named here rather than left unsaid:

- **The browser history.** The button only replaces the *current* history entry. Every page you opened earlier in this session stays in the browser's history (`Ctrl`+`H`) -- with address and page title, and the title of a client page contains the pseudonym. **Recommendation:** clear the history at the end of the shift, or work in a private window on shared devices.
- **A second open window.** The button acts on the window you press it in. A second open tab keeps showing its already loaded content until something is clicked or reloaded there -- at which point it ends up on the sign-in form as well. Its access to the local offline data is locked along with the first window, provided the deletion there succeeded (see the next point). **Recommendation:** work in a single window and close any second window beforehand.
- **A dropped connection.** The sign-out is sent to the server without waiting. If there is no network at that exact moment, it may be lost; the session then only ends through the inactivity timeout (30 minutes by default) or the maximum session duration.
- **A stalled or failed deletion of the local key.** Before switching pages the application waits half a second for the key to the offline data to actually be deleted -- which is enough in the normal case. If it does not succeed within that time, the page is switched anyway (your screen matters more), and the key remains until the next regular sign-out, until the automatic lock after a longer period of inactivity, or until the 48-hour offline authorization expires. Whoever opens the device afterwards may therefore still be able to see the locally stored data. **Recommendation:** do not leave the device unattended after quitting.

### Privacy Screen on Window or Tab Change

Your facility can additionally enable the **privacy screen** (default: off). When it is active, a neutral surface covers the content as soon as the window loses focus or the tab moves to the background -- for example when you switch to another program.

To continue, click **Continue** or press `Enter` or the space bar. The cursor then returns to where it was before. There is no countdown and nothing moves; the surface stays until you release it.

---

## 2. Home — Zeitstrom

After signing in, you land on the **Zeitstrom** (`/`) -- a chronological daily feed that unifies contacts, system activities, tasks, and entry bans in a single view.

### What the Zeitstrom shows

The feed combines four sources for the current day:

| Source | What is shown |
|--------|---------------|
| **Contacts** (Events) | Documentation entries with preview fields |
| **Activities** | System operations (created, edited, deleted...) |
| **Tasks** (Work Items) | Hints and tasks with priority and status |
| **Entry Bans** | Active entry bans -- additionally shown as a red banner on top |

The right sidebar shows your **5 most urgent open tasks** (sorted by priority, due date, and created date) for quick access during a shift.

### Tally

When the administration has enabled suitable documentation types, the **tally** appears in the Zeitstrom. It counts anonymous contacts per type for the current time window. When a shift is currently running, its time window applies; otherwise the current calendar day applies.

- **Without required fields:** `+1` immediately creates one anonymous contact.
- **With required number fields:** `+1` opens a compact value form. Enter every required value and select **Count contact**. This creates exactly one anonymous contact. For a needle exchange, for example, issued and returned needles belong to the same contact rather than counting multiple contacts.
- A type appears only when it is enabled for the tally and your role may see it as well as every required field. Types with other required field types do not appear in the tally.

### Changing the date

- **Arrow buttons** above the feed: jump to the previous or next day.
- **Today** link: returns to the current day.

### Shift filter

Below the date navigation you find **TimeFilter tabs** (e.g., "Early shift", "Late shift", "Night shift" -- configurable by the administration). When you open the Zeitstrom, the shift that matches the current time is auto-selected; night shifts crossing midnight are handled correctly.

**Daylight-saving changes.** On both changeover weekends a clock time is ambiguous, and the shift filter resolves that explicitly. On the night the clocks fall back to standard time, the hour from 02:00 to 03:00 runs twice; a shift that ends within that hour ends at the **second** occurrence -- a night shift from 20:00 to 02:45 therefore actually lasts 7 hours 45 minutes that night, and everything documented during the extra hour stays visible in the Zeitstrom, the handover, and the tally. On the night the clocks spring forward to daylight-saving time, the hour from 02:00 to 03:00 is skipped; a shift that would have ended within it instead ends after its agreed duration from shift start and is actually one hour shorter. Full-day views are unaffected -- the changeover day in autumn still spans 25 hours, the one in spring 23.

**Capturing the doubled hour.** A contact that really did take place during that doubled hour can be documented with its true time -- you do not have to fall back on 01:59 or 03:00. Enter the **timestamp** as usual when creating the contact (25 October 2026 at 02:30, say) and save. Because that clock time exists twice on that day, the form asks once: right below the timestamp the select **"Which of the two hours?"** appears, offering **Daylight-saving time -- first hour (CEST)** and **Standard time -- second hour (CET)**. Pick the hour that applies and save again. The select appears in this edge case only; on every other day and at every other time you will not see it.

**Offline capture.** The offline create shell has no fold selector, so it refuses a timestamp in the doubled hour with an explanatory message. Choose an unambiguous time (outside 02:00-02:59 on the fall-back day) or capture the contact online.

For the application the two hours really are different points in time, one hour apart: the contact lands in the shift it took place in, and the order in the Zeitstrom is correct. Afterwards both cases display the same wall-clock time (02:30) -- the difference lives in the stored point in time, not in what is displayed.

**When editing** a contact the timestamp can no longer be changed at all; it is shown there for reference only. So you make the choice when creating the contact. If an entry ended up in the wrong hour by mistake, create it again and have the wrong entry deleted ([Deleting a Contact](#deleting-a-contact)).

On the night the clocks spring forward, by contrast, the hour from 02:00 to 03:00 **does not exist at all** -- there the form still rejects a time from that window and says why. There is nothing to choose: a time that does not exist is not made real by any selection.

When a shift filter is active, an expandable **shift handover block** appears above the feed with statistics (number of contacts, activities, new tasks) and highlights (crisis events, new entry bans, urgent tasks).

#### Handover note

At the very top of the handover -- before the statistics -- sits the **handover note**: a free-text field for everything no metric captures ("B coped very well today, please follow up"). You see two blocks there: the note of the **shift that just ended**, the one you are taking over, and below it the field for the **current shift** you are working in. If **no shifts** are configured for your facility, the calendar day is the shift: the note from **yesterday** appears at the top, the field for **today** below it.

**Who may.** Staff, leads and application administrators may read and write. Assistants do not see the note -- not even a hint that one exists.

**How long it stays editable.** A note stays editable until the **end of the following shift**: whoever writes during the early shift can amend it until the late shift ends; after that the entry is read-only and states until when editing was possible. The deadline is shown below the text field. On the changeover weekends the same shift logic described above applies. **The deadline is fixed when the note is created:** if the application administration later changes the shift times or switches a shift off, that does **not** reopen a note that has already closed -- what was handed over stays handed over.

**Empty field = no note.** Save the field empty and the entry is removed -- an empty note must not mislead the following shift into reading "nothing to report".

**When two people write at once.** If two people create the first note of the same shift at the same moment, one of them wins. If the other one wrote the same text, nothing further happens. With a different text the page shows **both versions**: the one that was saved in the meantime at the top (with author and time), your own input below it in the text field -- nothing is lost. You then decide: **Use my input and replace** writes your text and replaces **exactly the version shown**, **Discard** keeps the saved one. Nothing is ever overwritten silently -- not even if the stored version changes again while you are comparing: in that case nothing is saved, and the version that is current by then is shown for another comparison, with your input unchanged in the field.

**Please no real names.** Use the same pseudonyms as in the rest of the documentation. The note is stored encrypted and is deleted automatically after the retention period configured by the application administration (default: 90 days). It is deliberately **not** part of the Article 15 disclosure and **not** searchable.

If its content matters to a procedure, record it as a case note on the relevant case within that configurable period; the handover note itself cannot be protected by a legal hold and is deleted permanently when the period expires.

Every change to a note appears in the audit log -- shift and timestamp are recorded, never the text itself.

### Filtering the feed

Two dropdowns above the feed:

- **Type**: All · Events · Activities · Tasks · Entry Bans
- **Documentation type**: only entries of a specific type (e.g., "Contact", "Crisis counseling") -- you only see types your role is allowed to view.

Filter changes reload only the feed (HTMX), without a full page refresh.

### Entry Ban banner

If there are active entry bans in your facility, they are displayed as a red banner below the heading.

---

## 2a. Work Center

The **Work Center** (`/start/`) is the landing page of the **Lead** role -- an overview page that condenses existing data into compact tiles. The sidebar link **"Work Center"** (above the Zeitstrom) therefore only appears for Leads. The Zeitstrom at `/` remains unchanged.

All other roles are redirected from `/start/` straight to their own workplace:

- **Social Worker / Assistant** -- to the Zeitstrom (`/`); the former tiles "Today's contacts", "My tasks", and "Recently edited" together with the lists "My open tasks" and "Recently edited persons" live on there as a cockpit block above the feed.
- **Facility Admin** (`facility_admin`) -- to the administration overview (`/verwaltung/`).
- **System Admin** (`super_admin`) -- to the system area (`/system/`).

**Lead** -- title "Lead Work Center", GDPR workflows, retention, and statistics:

- **Deletion requests** -- pending requests (links to the deletion-request list)
- **Retention proposals** -- pending retention proposals (links to the retention dashboard)
- **Statistics** -- month/year of the last statistics snapshot

---

## 3. Documenting a Contact (Creating an Event)

An **event** is a single documented contact -- for example, a counseling session, a syringe exchange, or an anonymous visit.

### Documenting a Contact

1. Click **Create new** in the sidebar and select **Document contact** (or navigate to `/events/new/`).
2. **Select a documentation type:** Choose the appropriate type from the list (e.g., "Contact", "Crisis counseling", "Syringe exchange"). The available types are configured by your facility.
3. **Fill in the fields:** After selecting the type, the corresponding input fields are loaded. Fill in all relevant fields.
4. **Timestamp:** The "Timestamp" field is automatically set to the current time. You can adjust it if you are recording a contact retroactively.
5. **Assign a client (optional):**
   - For an **anonymous contact** (without a pseudonym): Enable the "Anonymous" option. No client will be linked.
   - For a **client with a pseudonym**: Start typing the pseudonym in the client field. A suggestion list appears -- select the matching client.
   - If the client has not been registered yet, create them first under **Clients** (see [Section 4](#4-managing-clients)).
6. Click **Save**.

You will be redirected to the detail view of the newly created entry. A success message confirms the save.

> **Tip:** If you create a contact from the client detail page, the client is already pre-filled.

### Quick Templates

If your administrator has set up **quick templates** for recurring documentation patterns (e.g., "Counseling 30 min", "Standard check-in"), they appear as buttons at the top of the "Document contact" page.

- **Click a template button** to pre-fill the form with the template's saved values and documentation type.
- **You can still edit every field** before saving -- the template provides defaults, not a locked form.
- **Fields you have already filled in are not overwritten** -- the template only populates empty fields.
- **Sensitivity filter:** You only see templates whose documentation type you are allowed to access according to your role. Assistants therefore do not see templates for elevated- or high-sensitivity types.
- **Self-healing:** If an administrator has deactivated a selection option after the template was created, the corresponding value is silently dropped when you apply the template -- no error, you simply pick an active option yourself.

> **Note:** Templates are created and maintained by administrators in the Django admin interface. If you find yourself repeating the same documentation pattern, ask your admin to add a quick template.

### File Attachments

A file can be attached to each event -- for example, a scanned form, a photo of a document, or a PDF.

> **On clients and cases as well:** File fields are no longer restricted to events. If your administrator has set up a file field on a **client** or **case type**, it appears in the client's or the case's form and works exactly as described here -- upload, replacement with previous versions, download from the detail view. Two differences: **case files** can only be seen and downloaded by social workers, leads, and administrators (assistants have no access to cases), and files on a client or a case are **deleted together with the client** -- they have no retention period of their own.

- **Upload:** The contact form includes a file upload field. Pick a file from your device and save the event as usual. On clients and cases it works identically: pick the file in the form, save the record.
- **Virus scan:** Before saving, every file is automatically checked by a virus scanner (ClamAV). Infected files are rejected -- you receive an error and the event is not saved.
- **Encryption:** All attachments are stored encrypted (Fernet, AES-128-CBC with HMAC-SHA256). That means the file is not readable in cleartext on the server and can only be retrieved through Anlaufstelle itself.
- **Image metadata is removed:** For images (JPG, PNG, WebP, TIFF -- not for GIF and BMP) the embedded metadata is deleted on upload -- above all the **GPS capture location** (on phone photos often the home or current address of the person concerned), plus device model, capture time and the embedded thumbnail, which may still show the uncropped original. The picture itself stays unchanged, and portrait photos stay portrait. This does **not** happen for PDF and Office files -- their document properties may still name the staff member who worked on them.
- **Maximum size:** By default, **up to 10 MiB per file** are allowed. Your administrator can adjust this limit.
- **Supported formats:** PDF, Office documents, and images. Ask your administrator for the exact list allowed in your facility.
- **Download:** Open the detail view of the event (or of the client or the case) and click the file link. The file is automatically decrypted on retrieval and delivered in the browser.
- **Replacing (with version history):** When editing, you can replace the existing file with a new one. The old file is **not** deleted -- it is preserved as a **previous version** and remains downloadable from the detail view via an expandable accordion. Previous versions are only removed when the record itself is fully deleted. This applies to events, clients, and cases alike.
- **Removing:** A stored file can be deselected in the form. It disappears from the detail view and can no longer be downloaded; the version chain is kept for traceability until the record itself is deleted. The file remains encrypted on the server and is reported as "removed on..." in an Art. 15 GDPR access request. This is **not a dead end**: a removed file can still be deleted permanently later (see below and [Section 3a](#deleting-permanently)).
- **Deleting permanently:** For genuine mistakes -- wrong file, wrong person -- there is additionally permanent deletion. It physically destroys the encrypted file including all previous versions. This is **irreversible** and therefore restricted to managers and administration. There are **two** entry points: directly at the file in the detail view of the event, the client, or the case (link "delete permanently" next to the filename) and in the [Files overview](#3a-files-overview).

> **Changing the type discards values *and* files:** If you change the documentation type on a client or a case -- or remove it ("— No type —") --, the additional fields of the old type no longer apply. A confirmation prompt before saving names both: how many **field values** will be discarded **and** how many stored **files** will be removed. "Cancel" leaves everything unchanged; only "OK" carries it out.

> **Offline note:** Events with file attachments currently **cannot** be saved offline. If you work offline, attaching a file shows an explicit hint. See [Section 8](#8-installing-the-pwa-and-working-offline).

> **Central files overview:** All file attachments in your facility can be browsed and filtered as a single list via the **Files page** -- see [Section 3a: Files Overview](#3a-files-overview).

### Editing a Contact

1. Open the event (via the Zeitstrom or the client chronology).
2. Click **Edit**.
3. Change the desired fields and click **Save**.

> **Note:** Edits are recorded in the event's change history. Previous versions remain visible in the history. Encrypted before-values are also shown in cleartext when your role may see the field; fields above your sensitivity level remain protected as `[Eingeschränkt]` (this fixed history placeholder is currently displayed in German in both interface languages).

> **Concurrent editing:** If someone else edited the same record at the same time, an error message appears. Reload the page and re-enter your changes -- this ensures that no changes are silently overwritten.

### Deleting a Contact

1. Open the event.
2. Click **Delete**.
3. Enter a reason.
4. Confirm the deletion.

> **Important:** If the contact is associated with a **client at the "With additional details" contact level**, it will not be deleted immediately. Instead, a **deletion request** is automatically created, which must be approved by a lead or administrator (four-eyes principle). You will receive a corresponding notification.

---

## 3a. Files Overview

The **Files page** (`/attachments/`, sidebar entry **Files**) shows all attachments in your facility as a searchable list -- without having to open the respective record first. It covers files on **events, clients, and cases**.

### Access

- **Sidebar navigation -> Files**
- Available to assistants, social workers, leads, and administrators.

### Visibility

You only see files whose record (event, client, or case) **and** whose field reach your sensitivity role:

- Fields with "high" sensitivity remain hidden for social workers at a lower level -- the same gate as in the detail view.
- Attachments of deleted events, clients, and cases are not included; neither are files you have removed again in the form.
- **Assistants do not see files on cases** -- cases are not visible to them at all, and consequently neither are their files.
- Facility scoping: you only see files from your own facility.

### "Assigned to" column

Each row states **what** the file is attached to: **Event**, **Client**, or **Case**. The entry is a link and leads directly to the corresponding detail page (for cases only for roles that are allowed to see cases).

### Filters

Two filters above the table:

- **Documentation type** -- only attachments belonging to records of a specific type (e.g., "Counseling", "Intake form"). Only types you are allowed to view according to your role are offered.
- **Client** -- all attachments for a specific pseudonym. The filter covers **all three** paths by which a file can belong to a client: via their event, directly on the client, and via one of their cases.

The filters can be combined and update the list via HTMX without a full page reload.

### Download

Each row shows the original filename. Clicking it delivers the decrypted file -- exactly as in the detail view of the event, the client, or the case. Downloads are recorded in the audit log.

### Encryption

All files are stored encrypted on the server (Fernet, AES-128-CBC with HMAC-SHA256). Decryption only happens at download request time inside the Django process; admins with direct disk access only see the encrypted binary data.

### Previous versions

Replaced files are preserved as previous versions (see [Section 3 -- File Attachments](#file-attachments)). The Files overview currently only shows the latest version per entry; previous versions are accessible via the entry on the detail page of the event, the client, or the case.

### Deleting permanently

Next to the download, every row carries a trash icon for **managers and administration**: delete permanently. The same entry point sits directly at the file in the detail view of the event, the client, or the case. It is meant for genuine mistakes -- a file that should never have been uploaded (wrong person, wrong file, a real name inside the document).

- The click first opens a **confirmation page**. It names the record in question: filename, size, the assigned record **including the client ID**, the documentation type, the upload timestamp, and the number of affected versions. Check these details -- two identically named files belonging to two different people are otherwise indistinguishable.
- Once confirmed, the encrypted file **including all previous versions is removed from disk**. **Within the application** there is no trash bin and no way to restore it afterwards -- not even by the administration.
- **Backups are not covered by this.** If your facility runs a media backup, the encrypted file stays there until the backup retention expires and could be brought back by restoring the overall state. If a data subject insists on complete erasure, do not promise otherwise and additionally involve your administration.
- **Legal hold:** if the record or its client is under an active legal hold, the button is disabled and the confirmation page states the reason. Files that must be preserved as evidence cannot be destroyed. The rejected attempt is recorded.
- The operation is recorded in the audit log -- **without** the plaintext filename, but with the record reference and the timestamp. In an Art. 15 GDPR access request it appears there as "attachment deleted"; the file itself is no longer reported as stored, because it no longer is.

**Files already removed.** Anyone who first took a file out of the documentation via the "Remove" checkbox will no longer find it in the regular overview -- yet it remains encrypted on the server. Managers and administration can show these files via **"Show removed files"** above the table and delete them permanently from there.

> **When is removing the better choice?** If the file was professionally correct once and merely should not appear in the running documentation any more, **remove** it via the edit form (see [Section 3 -- File Attachments](#file-attachments)). It then disappears from the interface but stays verifiable -- and the route to permanent deletion stays open via "Show removed files".

---

## 4. Managing Clients

Clients in Anlaufstelle are recorded **exclusively using pseudonyms** -- no real names. The pseudonym is the primary identifier.

### Contact Levels

Each client has a **contact level**:

| Level | Description |
|---|---|
| **Pseudonym only** | Only the pseudonym is stored, plus the age group |
| **With additional details** | Additional personal details are stored (restricted access, opening the detail page is logged) |

> **Note:** Access to client profiles at the "With additional details" level is automatically recorded in the audit log.

### Creating a Client

1. Navigate to **Clients** (`/clients/`) and click **Create person** (or go directly to `/clients/new/`).
2. Fill in the form:
   - **Pseudonym:** A unique name within your facility (e.g., a self-chosen nickname). The pseudonym must be unique within a facility.
   - **Contact level:** Select "Pseudonym only" or "With additional details".
   - **Age group:** "Under 18", "18--26", "27+", or "Unknown".
   - **Notes:** Internal remarks about the client (optional).
3. Click **Save**.

### Searching for Clients

1. Navigate to **Clients** (`/clients/`).
2. Type part of the pseudonym into the search field. The list filters automatically.
3. Optionally, you can also filter by **contact level** or **age group**.
4. Click on an entry to open the detail view.

> **Tip:** The quick search via the global search field (magnifying glass at the top) finds clients and events simultaneously.

### Client Detail Page and Chronology

The detail page of a client (`/clients/<id>/`) shows:

- **Master data:** Pseudonym, contact level, age group, notes
- **Chronology:** All previously documented contacts for this client, newest first
- **Open work items:** Tasks and hints assigned to this client

From the detail page, you can use **Document contact** to record a contact for this client directly, or **Create task** to add a task.

### Editing a Client

1. Open the client detail page.
2. Click **Edit**.
3. Change the desired fields (e.g., upgrading the contact level).
4. Click **Save**.

> **Note:** A change in contact level is automatically recorded in the audit log.

> **Concurrent editing:** If someone else edited the same record at the same time, an error message appears. Reload the page and re-enter your changes -- this ensures that no changes are silently overwritten.

### Merging Clients (Duplicates)

If the same person was accidentally created twice -- for example under two spellings of the pseudonym -- the two records can be merged afterwards. A hint already helps to avoid this at **creation time**: as soon as you type a pseudonym that resembles an existing client, "A similar client already exists" appears below the field with a link to that record. The hint blocks nothing -- you can still save.

If the duplicate already exists:

1. Open the record of the client that is to be **dissolved** and choose **Merge with duplicate** in the **More actions** menu (⋮).
2. Search for the target client -- the record that remains. The search also finds similar spellings.
3. The confirmation page shows **both records side by side** (contact level, age group, creation date, last contact, number of events/cases/attachments) plus a statistics warning and the deadline note. The page itself tells you which of the two paths applies:
   - **Merge directly** -- if both clients are at "Pseudonym only"; only the **lead** may do this.
   - **Request with confirmation** -- as soon as one of the two clients carries "With additional details" (or a staff member takes this path). Enter a reason; confirmation happens under **Privacy → Deletion requests**, where the request appears as its own kind, **Merge**, with its target. Until then the record is flagged as "merge requested" and stays usable.

What happens:

- **Events, cases, tasks and the recent-visits list move completely** to the target record. For the additional details, the target keeps its values; values from the source only fill empty fields. The contact level rises to the higher of the two -- never the other way around.
- The dissolved record disappears from list, search and selection fields and sits in the **trash** with the note "merged into...". Until the trash period expires, the facility admin can use **Revert merge** there -- the moved entries return; entries newly documented since the merge stay with the target. After that, the empty shell is anonymized for good like any deleted client; the reference to the target remains. If a **legal hold** covers either record, reverting is not possible while it lasts -- the facility admin gets a corresponding message.
- In an **Art. 15 GDPR access record**, the target record states in its "Merges" section which record was merged into it and when; the access record of the dissolved record conversely states "Merged into". The access log of the dissolved record stays with its own dataset -- a request covering the full history of both predecessors is answered with **both** access records.

> **Offline:** Data of the dissolved record cached on devices is discarded at the next sync. If an event for it was **already documented offline** but not yet transmitted, it is not silently rebooked to the target record -- as with a deletion, it goes to the **conflict list** (dead letter) for follow-up, so that nothing is rewritten in the documenting person's name without their knowledge.

---

## 5. Hints and Tasks (Work Items)

**Work items** are used for internal team communication. There are two types:

| Type | Description |
|---|---|
| **Hint** | Information for the team that does not require direct action |
| **Task** | A specific assignment that needs to be completed |

### Status Lifecycle

```
Open → In Progress → Done
           ↓
       Dismissed
```

Work items can have three priority levels: **Normal**, **Important**, or **Urgent**. Urgent tasks appear at the top of the list.

### Opening the Inbox

Navigate to **Tasks** (`/workitems/`). The inbox shows:

- **Open:** New tasks and hints assigned to you or not yet assigned to anyone
- **In Progress:** Tasks that you (or colleagues) have taken on
- **Completed:** Items completed or dismissed within the last 7 days

### Changing Status

Directly on the inbox card of a work item, you can change the status with a click:

- **"Take on"** -- automatically assigns the task to you
- **"Done"** -- closes the task
- **"Dismiss"** -- for tasks that are no longer relevant

The list updates without a page reload.

### Creating a Task or Hint

1. Click **Create task** in the inbox (or navigate to `/workitems/new/`).
2. Fill in the form:
   - **Type:** "Task" or "Hint"
   - **Title:** A brief, concise description
   - **Description:** More detailed information (optional)
   - **Priority:** Normal, Important, or Urgent
   - **Assigned to:** A specific person from your facility (optional -- leave blank if the task applies to everyone)
   - **Client:** If the task concerns a specific client (optional)
3. Click **Save**.

> **Tip:** If you create a new task from the client detail page, the client is already pre-filled.

### Editing a Task

1. Open the task by clicking on the title.
2. On the detail page, click **Edit**.
3. Change the desired fields and save.

> **Concurrent editing:** If someone else edited the same record at the same time, an error message appears. Reload the page and re-enter your changes -- this ensures that no changes are silently overwritten.

### Filter: Assigned to Me

In the inbox view you can activate the **"Assigned to me"** filter. With it enabled, you only see tasks and hints that are assigned to you personally -- general (unassigned) entries and entries for other people are hidden.

The filter is useful when you want a clear overview of your own open items without being distracted by the whole team list.

### Bulk Edit

If you want to change several tasks at once, use bulk edit mode:

1. In the inbox, select the desired tasks via the **checkbox** to the left of each card.
2. A **bulk dropdown** with the available actions appears at the top.
3. Change **status**, **priority**, or **assignment** centrally for all selected entries at once.
4. Confirm the change -- all marked entries are updated together.

### Reminder vs. Due Date

Tasks have two distinct time fields:

| Field | Meaning |
|---|---|
| **Due date (due_date)** | Deadline -- the latest point at which the task must be completed |
| **Reminder (remind_at)** | The moment you want to be notified -- usually before the due date |

**Example:** The due date is April 15th, and you set the reminder to April 10th -- that way you get a heads-up five days ahead and do not run into last-minute stress on the deadline.

Both fields are optional. You can also create a task with only a due date, or only a reminder.

### Recurring Due Dates

For recurring tasks (e.g., monthly counseling sessions, weekly check-ins), you can set a **recurrence rhythm** (e.g., weekly, monthly).

As soon as you set such a task to **"Done"**, a **follow-up task** with the same title and a new date based on the chosen rhythm is created automatically. That way you do not have to recreate the task manually every time.

---

## 6. Search

**Search** simultaneously searches clients (by pseudonym) and events (by client pseudonym and content fields). It is accessible in two ways:

### Global Search (Quick Search)

The search field is **permanently visible in the sidebar** (desktop). On smartphones, tapping the search icon in the bottom navigation opens an overlay.

1. Start typing in the search field. After a short delay, results appear as a dropdown.
2. A maximum of **5 clients** and **5 events** are displayed.
3. Click on a result to jump directly to the detail page.
4. Use **"Show all results"** to go to the full search page.

> **Tip:** Press the Escape key to close the search dropdown.

### Full Search Page

For more extensive research, the search page at `/search/` is available. It is accessible via the "Show all results" link in the quick search; pressing Enter in either navigation search field (sidebar and mobile overlay) also takes you straight there.

- The **grouped overview** shows up to **10 results** per group, but the group heading always states the **total number**.
- The filter chips **"All types · Persons only (N) · Events only (N)"** narrow the page to one type (`?type=person` or `?type=event`); within the filtered list you page through all results.
- At the bottom of the page, the block **"What search does not find"** explains which data is deliberately excluded from search.

> **Note:** Fields configured as encrypted are not included in search results.

### Typo-tolerant Search (Fuzzy)

Search also finds results when you make a typo or a name has been stored in a slightly different spelling. Examples:

- Entering **"Muller"** also finds **"Müller"**.
- Entering **"Tomas"** also finds **"Thomas"**.

Technically this is based on trigram similarity in the PostgreSQL database (pg_trgm) -- you do not have to worry about the details.

**Similarity threshold:** Per facility, your administrator can configure how "strict" fuzzy search behaves (value range 0.0--1.0, default around 0.3). A **lower value** returns more results but also more false matches; a **higher value** is stricter and only shows very similar terms.

---

## 7. Statistics and Export

> **Access:** Statistics and exports are only available to **leads** and **administrators**.

### Statistics Dashboard

1. Navigate to **Statistics** (`/statistics/`).
2. Select a time period:
   - **Last 30 days** (default)
   - **Last 90 days**
   - **Last 180 days**
   - **Calendar year** (with year navigation, see below)
   - **Custom period:** Enter start and end dates manually.

   > The three "Last N days" windows are **rolling** and end today; both ends count, so "Last 30 days" spans exactly 30 days. Calendar words are reserved for calendar units -- which is why the calendar period is called "Calendar year" and the rolling windows are named after their length.
3. The dashboard updates automatically and displays aggregated key figures for contact numbers, documentation types, and client groups.

> **Note:** The "Overview" tab -- this dashboard -- counts events in the period **regardless of their report assignment**, including events whose documentation type is not assigned to a report or is explicitly excluded from reports. The report assignment (called "report key" in the configuration) affects the figures only on the "Dataset reports" tab. The two tabs may therefore show different figures for the same period; the "Dataset reports" section explains further reasons. The population evaluated by an individual table is stated below its heading: "Contacts by age group", for example, counts only events linked to a person.

### Year Navigation

To view data for a specific year:

1. Click **Calendar year**.
2. Use the arrow buttons to the left and right of the year number to navigate to the previous or next year.
3. For the current year, the period from January 1st to today is shown. For past years, the full year (January 1st -- December 31st) is displayed.

### Trend Charts

Below the key figures, the dashboard shows three interactive charts:

| Chart | Description |
|-------|-------------|
| **Contacts Over Time** | Line chart with monthly breakdown of contacts (Total, Anonymous, Pseudonym only, With additional details) |
| **Documentation Types** | Bar chart showing the distribution by documentation type (e.g. Contact, Counseling, Needle Exchange) |
| **Age Groups** | Doughnut chart showing the demographic distribution by age group |

The charts update automatically when the time period is changed.

**Data Source Indicator:** The line chart legend shows whether a data point comes from a **Snapshot** (pre-computed monthly data) or from **Live Data** (current database query). Snapshots ensure that historical trends are preserved even after retention periods expire.

**Documentation Type Filter:** Use the "All Documentation Types" dropdown above the charts to filter the view to a specific documentation type.

> **Note:** Charts are not shown when printing. Use the PDF or CSV export functions for reports.

### CSV Export

The CSV export contains all events from the selected time period. Fields whose sensitivity level exceeds the exporting user's role permissions are shown as "[Restricted]".

1. Open the statistics dashboard and select the desired time period.
2. Click **Export CSV**.
3. The file is downloaded immediately (filename: `export_YYYY-MM-DD_YYYY-MM-DD.csv`).

> **Note:** Every export is recorded in the audit log.

### PDF Export

The PDF export generates a structured statistics report for the period selected at the top of the dashboard, for internal documentation.

1. Select the time period in the statistics dashboard.
2. Click **PDF Export**.
3. The PDF file is downloaded (filename: `bericht_YYYY-MM-DD_YYYY-MM-DD.pdf`).

### Example Activity Report

The example activity report generates a structured report based on a fixed example layout. It is a customizable example or template -- not a binding or current official proof, and no guarantee for state-specific requirements.

1. Select the time period. It applies to the entire statistics area and is kept when you switch tabs.
2. Open the **Dataset Reports** tab and pick the **Example Activity Report** card.
3. Click **PDF export** below the preview. The file is downloaded (filename: `sample-report-1_YYYY-MM-DD_YYYY-MM-DD.pdf`).

> **Tip:** For semi-annual reports, select "Custom period" and set the start and end dates to 01/01 and 06/30 (or 07/01 -- 12/31). "Last 180 days" is a rolling window and never lines up exactly with a calendar half-year.

**Data source: monthly aggregates.** The footer of the PDF states where the figures come from. The activity report PDFs -- both the example activity report and the semi-annual report export -- read from the stored **monthly aggregates**. Two consequences: periods whose events were removed after the retention deadlines still show figures in the PDF -- and across several months the monthly values are added up, so anyone who appeared in two months is counted twice under "Unique persons". The reports in the "Dataset Reports" tab (CSV/JSON/XML, see below) are calculated live and can therefore show different figures for the same period.

### Dataset Reports (CSV / JSON / XML / PDF)

The **Dataset Reports** tab lists every reporting standard configured for your facility -- the example activity report as well as any others your administrator has added.

1. Pick the **card** of the report you want. Each card states in one sentence what the report shows, which population it evaluates, and whether it is calculated live or read from monthly aggregates.
2. A **preview** with the figures for the selected period appears below. The address remembers your choice (`?ansicht=datensaetze&bericht=…`), so bookmarks and the back button work.
3. Use the **CSV export**, **JSON export**, and **XML export** buttons to download the same data as a file. CSV suits spreadsheets; JSON and XML are meant for further processing in other systems. The example activity report adds **PDF export**.

Clicking a time period does not discard your report selection -- the card and preview stay in place and the figures update.

**Completeness notices:** Above the preview you can see what the report does *not* show -- for example events whose documentation type maps to no report category, or events without a person reference. A yellow **Note** badge means data is missing, so the figures are too low. A grey **Info** badge means the data is included but missing from individual sub-analyses. If it says "No completeness issues detected", the report has no gaps.

**Small case numbers are suppressed:** The preview *and* the download show the same privacy-friendly data -- values below your facility's k-anonymity threshold (default 5) are removed, so that no individual person can be derived from a report. How a removed value is **displayed** depends on the format: as a dash in the preview, as an empty cell in the CSV (the metadata line at the top of the file points out the suppression), as `null` in JSON, and as an empty element in XML. Occasionally a second, larger value is dropped as well -- this is intentional, because otherwise the suppressed value could be calculated back. For the same reason, completeness notices contain no digits at all when the numbers are very small.

**Data source: live query.** Below the preview you can see where the figures come from. The preview *and* the download recalculate the figures from the event data on **every request**. Periods whose events were removed after the retention deadlines therefore appear empty -- unlike the example activity report PDF, which reads from the monthly aggregates. The file formats carry the same statement in machine-readable form: in the CSV metadata line at the top of the file, and in JSON and XML as the field `data_source` (value `live`).

> **Note:** Every download is recorded in the audit log; the preview is not. An empty period looks like suppression in the preview -- when in doubt, check the selected time period.

### Privacy-friendly External Reports (Lead / Admin)

For sharing data with external parties (e.g., funding bodies or municipal authorities), an **external report with a privacy profile** is available at `/statistics/external/`. It uses the same time-period filters as the statistics dashboard but is deliberately data-minimal:

- **No pseudonym ranking** -- the top-persons list from the internal dashboard is omitted entirely.
- **K-anonymization** -- aggregates with fewer than *k* records (threshold from the facility settings, default 5) are shown as **"suppressed"** rather than as a concrete number. This applies to "Unique persons", "By documentation type", and "By age group".
- **Privacy profile header** -- facility, profile (`external`), time period, k-anonymity threshold, generation timestamp, and **data source** appear at the top of the report. This report is calculated live as well; in the JSON version the statement appears as `metadata.data_source` (value `live`).

Via **"Export as JSON"** (or by appending `?format=json` to the URL) you receive the same data in machine-readable form. Every request is logged as an `EXPORT` event in the audit log. The external report is accessible to **leads** and **administrators** only.

### Reviewing Deletion Requests (Lead / Admin)

When a social worker or assistant wants to delete a contact belonging to a client at the "With additional details" contact level, a **deletion request** is created that must be approved by a lead or administrator.

1. View open deletion requests at `/deletion-requests/`.
2. Click on a request to see the details and the affected event.
3. Click **Approve** or **Reject** and confirm.

> **Important:** You cannot approve your own deletion request (four-eyes principle).

Regardless of your role: As the requesting person, you can withdraw your own deletion request yourself as long as it is still **pending**. At `/deletion-requests/`, your own pending request shows the **Withdraw** link next to the four-eyes notice. The confirmation dialog explicitly says: **Nothing is deleted — the data remains unchanged.** Afterwards, a new deletion request for the same target is possible at any time. The request remains visible to the approver pool in the **Withdrawn** section, and the withdrawal is logged with the final status **Withdrawn**.

---

## 8. Installing the PWA and Working Offline

Anlaufstelle can be installed on your smartphone or tablet home screen like a native app -- no app store required.

### On Android (Chrome)

1. Open Anlaufstelle in Chrome.
2. Tap the three-dot menu at the top right.
3. Select **Install App** or **Add to Home Screen**.
4. Confirm with **Install**.

The app now appears as an icon on your home screen and opens in full-screen mode without the browser address bar.

### On iOS (Safari)

1. Open Anlaufstelle in Safari.
2. Tap the share icon (square with an upward arrow).
3. Scroll down in the action list and select **Add to Home Screen**.
4. Optionally enter a name and tap **Add**.

### On Desktop (Chrome / Edge)

1. Open Anlaufstelle in the browser.
2. An install icon (screen with arrow) appears on the right side of the address bar.
3. Click on it and confirm with **Install**.

The installed app behaves like a regular program and is accessible via the Start menu or the desktop.

> **Note:** The app is a **Progressive Web App (PWA)**. An **offline mode** is now available for streetwork use (see below) -- you can record events without an internet connection and sync them later.

> **Firefox (Android):** Firefox does offer "Install", but the app always opens with the address bar visible. For the true app mode without the address bar, use **Chrome, Edge, or Samsung Internet** (Android) or **Safari** (iOS).

### Offline Capture (Streetwork)

For assignments without a reliable internet connection -- for example, outreach work -- you can use Anlaufstelle in offline mode.

**Before the assignment (online):**

1. Open the client list.
2. For each client you want to take along, tap the offline-toggle button in the **Offline** column of the client list -- or, on the client's detail page, tap **Take offline**. Pseudonym, master data, and the most recent events are then encrypted and loaded onto your device for offline use; an **Available locally** badge confirms it. Up to 20 clients at a time.
3. An overview of your taken-along clients is available any time under **"Clients"** (`/clients/`) -- this view also works offline since Wave 2 (see below).

**During the assignment (offline):**

- **Clients, tasks, and the timeline stay available at their usual address (since Waves 2/3, #1499):** The **client list** (`/clients/`), the **task list** (`/workitems/`), and the **timeline** as the start page (`/`) render offline the same local excerpt as online -- assembled from your taken-along clients, in the usual look. The task list and timeline also show **anonymous or client-less entries** (standalone tasks, contacts without a client), clearly marked as such; the timeline is deliberately labeled a "local record" -- it shows only the offline-available activity, not the facility's complete history. You no longer need a separate offline start page for this.
- Open a taken-along client as usual via their detail page -- the view stays at the **normal address** and is built from the encrypted offline store (no separate "offline" link anymore).
- You can **record new events** (via **"Document contact"**) and **edit existing ones**; entries are stored encrypted locally (AES-GCM-256; the key is derived from your password) and sent automatically once you are back online. **File attachments** cannot be captured offline -- add them once you are back online.
- **Capture anywhere offline (since Wave 1, #1499):** you no longer need an **open client dossier**. Offline, tap **"Document contact"** or **"Create task"** in the **"+" menu** (or open the forms `/events/new/` / `/workitems/new/` directly) -- a real capture form opens **at the usual address**: pick a document type, optionally assign a client you took offline, and save. The old "only within the dossier" dead end (#1483/#1485) is gone.
- **Contacts without a client** (anonymous contacts) and **standalone tasks** can now be **started offline too** -- choose **"-- no client --"** in the client picker. If the chosen document type requires a contact stage, "no client" is disabled; assign a taken-along client instead (the server re-checks the contact stage on sync).
- **Task capture is staff-only.** As an assistant, opening the task form offline shows a note instead of the fields -- so no entry is created that would be rejected on sync.
- If nothing has been **prepared for offline capture** yet (the facility was never opened online, the local cache is empty, or the installed app is not updated yet), the form shows a short **"open it once online"** hint instead of the fields.
- **`/offline/` is a purely technical fallback since Wave 3**, no longer an entry point: the "No local view available" page appears only when **no** local view matches the requested address (e.g. an unknown URL, or the app has not yet been updated to the current version) -- with links to the client list, tasks, and the timeline, plus "Try again".
- The interface shows a hint that you are working offline and how many entries are still waiting to be synced.

**Back online:**

- As soon as the device is back online, the queue is **synced automatically**. Events recorded offline land on the server and become visible to the team.

**Resolving conflicts:**

If an event was edited online (by someone else) and offline (by you) at the same time, Anlaufstelle shows a **side-by-side diff** with three choices during sync:

- **Keep mine** -- your offline edit wins.
- **Keep server** -- the online edit wins, your offline version is discarded.
- **Merge manually** -- you decide field by field which content is kept.

**Managing conflicts and undeliverable entries:**

**`/offline/conflicts/`** shows every entry that needs a decision -- the page opens even without a network connection, since #1396. Two sections:

- **"Awaiting decision"** -- open conflicts; click through to the side-by-side diff above.
- **"Undeliverable"** -- entries that could not be sent permanently, each with a reason (e.g. deleted on the server, access revoked, rejected by the server as invalid, no permission). You can **retry** or **discard** these entries -- for undeliverable events you can also **export** the content first, so nothing is lost.

> **Important -- avoid data loss:** **Logout** and **password change** delete the locally stored offline data -- always **sync first** before signing out or changing your password. After a period of inactivity, offline access is **locked** for security reasons; after signing in again, any not-yet-synced entries become available again. Simply **closing the tab**, however, deletes nothing. If you have not-yet-synced entries when changing your password, Anlaufstelle warns you before you submit, showing the number of affected entries.

> **Sign-in required during the transfer:** If the server asks for a new sign-in, for the second factor or for a password change while syncing, that does **not** count as saved -- the transfer pauses, every not-yet-transferred entry stays unchanged on your device, and the offline view says so, including the number. Sign in again; the transfer then continues without duplicate entries.

> **No file attachments offline:** Events with file attachments **cannot** be saved offline. For security reasons, no unencrypted file blobs are stored in the browser. In that case, record the event without the file first and attach the file once you are back online.

---

## 9. Roles and Permissions

Anlaufstelle distinguishes five roles. Your role is assigned by the facility administrator (Anwendungsbetreuung) of your facility.

> **Audience of this section:** the **practical** view -- what you are allowed to do with your role in day-to-day work. The professional rationale behind the model is in the [domain concept (Role)](../fachkonzept-anlaufstelle.md#role--die-rolle) (German only), the technical details (DB values, RLS scoping) in [FAQ § C](../faq.md#die-fünf-rollen) (German only), and creating and managing roles in [Admin Guide § 2.4](admin-guide.md#role-descriptions).

### Role Overview

| Role | Display Name | Brief Description |
|---|---|---|
| `assistant` | Assistant | Limited data entry without access to client data at the "With additional details" level |
| `staff` | Social Worker | Default role for employees doing documentation |
| `lead` | Lead | All social worker functions plus reports and management tasks |
| `facility_admin` | Facility Admin | Full access to all areas and settings **of your facility** (audit log, GDPR package, user management) |
| `super_admin` | System Admin | Hosting/carrier role: cross-facility, bootstrap tools, separate `/system/` area. **Not visible in the regular staff UI** -- end users do not interact with it. |

> **Note:** The System Admin (`super_admin`) is a hosting/carrier role. It sets up the installation and creates the first facility and the first Facility Admin -- both in the Django admin interface (`/admin-mgmt/`); the `/system/` area only displays the organization and its facilities. You will not encounter it in day-to-day operations -- neither area is accessible to facility-bound users.

### Who Can Do What?

Table covers the four facility-bound roles only -- `super_admin` is excluded here because it does not work in the staff UI:

| Function | Assistant | Social Worker | Lead | Facility Admin |
|---|---|---|---|---|
| View Zeitstrom / home page | Yes | Yes | Yes | Yes |
| Document anonymous contacts | Yes | Yes | Yes | Yes |
| Document contacts for clients at the "Pseudonym only" level | Yes | Yes | Yes | Yes |
| Create and search for clients | Yes | Yes | Yes | Yes |
| View client details at the "With additional details" level | No | Yes (own facility) | Yes | Yes |
| Edit own events | Yes | Yes | Yes | Yes |
| Edit other users' events | No | Yes | Yes | Yes |
| Create and edit work items | Yes | Yes | Yes | Yes |
| Use search | Yes | Yes | Yes | Yes |
| Statistics and export | No | No | Yes | Yes |
| Export client data (Art. 15 / 20 GDPR) | No | No | Yes | Yes |
| Download GDPR documentation package | No | No | No | Yes |
| Submit deletion requests | Yes | Yes | Yes | Yes |
| Approve deletion requests | No | No | Yes | Yes |
| Manage pseudonyms / change contact level | No | No | Yes | Yes |
| View audit log (own facility) | No | No | No | Yes |
| Case management (cases, episodes, goals) | No | Yes | Yes | Yes |
| Close / reopen cases | No | No | Yes | Yes |
| Manage users and settings | No | No | No | Yes |

> **Note:** Access is always restricted to your own facility. Employees of one facility cannot see data from other facilities within the same organization -- this applies to the Facility Admin (`facility_admin`) as well.

### Audit Log (Facility Admin Only)

The audit log (`/audit/`) automatically records security-relevant actions: sign-ins, access to data at the "With additional details" level, exports, deletions, and contact level changes. It cannot be modified and serves traceability purposes in accordance with the GDPR. Access is granted to the Facility Admin (`facility_admin`) for their own facility.

---

## 10. Case Management

Not every contact with a client stands on its own. When collaboration with a person extends over a longer period -- e.g., a counseling process, crisis support, or housing referral -- you can bundle this work into a **case**.

A case is a **bracket** around thematically related contacts. Case management is **optional**: you can use Anlaufstelle just as well without cases if your facility does not document ongoing counseling processes.

> **Access:** Case management is available to **social workers**, **leads**, and **administrators**. Assistants do not have access.

### Case List

1. Navigate to **Cases** (`/cases/`).
2. You see a table of all cases in your facility, sorted by creation date (newest first).
3. **Search:** Enter a title in the search field -- the list filters automatically as you type.
4. **Status filter:** Select "Open", "Closed", or "All statuses" from the dropdown to narrow the display.

### Creating a Case

1. Click **Create case** on the case list (or navigate to `/cases/new/`).
2. Fill in the form:
   - **Title** (required): A short label for the case (e.g., "Housing search", "Addiction counseling").
   - **Client:** Start typing the pseudonym -- a suggestion list appears. Select the matching client. A case can also be created without a client.
   - **Description:** More detailed information about the case (optional).
   - **Case owner:** Select the responsible person from the dropdown (optional). Only social workers, leads, and administrators from your facility are available.
3. Click **Create case**.

You will be redirected to the detail page of the new case.

> **Tip:** If you start from the client detail page via **Create case**, the client is already pre-filled.

### Case Detail Page

The detail page of a case (`/cases/<id>/`) is divided into three sections:

**Header:**
- Title and status badge (Open / Closed)
- Link to the associated client
- Case owner, created on, created by
- Description (if available)
- Buttons: **Edit**, **Close** or **Reopen**

**Left column -- Contacts:**
- All contacts (events) assigned to the case, sorted chronologically
- Option to assign additional contacts from the client or remove existing ones

**Right column -- Episodes and Outcome Goals:**
- List of episodes (phases within the case)
- List of outcome goals with milestones

### Editing a Case

1. Open the case detail page.
2. Click **Edit**.
3. Change the title, client, description, or case owner.
4. Click **Save**.

### Closing and Reopening a Case

When work on a case is finished, the case can be closed.

1. Open the case detail page.
2. Click **Close**. The case is marked as closed with the current timestamp.

A closed case can be reopened at any time:

1. Open the closed case.
2. Click **Reopen**.

> **Important:** Only **leads** and **administrators** can close and reopen cases.

### Deleting a Case

A case created by mistake can be deleted. "Closing" is **not** the right tool for that: it is a statement about the course of support, and a case that never existed has no course.

1. Open the case detail page.
2. In the **More actions** menu (⋮), click **Delete**.
3. The next page tells you which of the two paths applies:
   - **Straight to the trash** — for clients at contact stage "Anonymous" or "Pseudonym only".
   - **Deletion request with confirmation** — for a client at stage "With additional details". Enter a reason; the page also names who can confirm the request. Confirmation happens under **Privacy → Deletion requests**, just like for clients and contacts. While the request is pending, the case stays fully usable and is flagged as "deletion requested".
4. Confirm.

What happens:

- The case disappears from the case list, statistics and the activity feed, and moves to the **trash**. Episodes, outcome goals and file attachments go with it — and all come back if the case is restored.
- **Assigned contacts stay with the client.** They are not deleted along with the case and do not block its deletion; they only lose their case assignment once the case is purged for good.
- Once the trash period expires (30 days by default, the same one that applies to deleted clients), the case is **anonymized** automatically: title, description, custom fields, episodes, outcome goals and files are then irreversibly gone. Restoring is no longer possible after that.
- Until then, a case in the trash appears in an Art. 15 GDPR access record in its **own section** "Cases in the trash" — stating since when it has been deleted and whether it has already been anonymized for good.

> Only the **facility admin** can **restore** a case — via **Trash** in the header of the case list. That page also offers **Anonymize permanently now** if waiting for the deadline is not an option.

> **Offline:** a deleted case also disappears from the data cached on your device at the next sync. Cases cannot be created, edited or deleted offline anyway.

### Assigning and Removing Contacts

On the case detail page, below the assigned contacts, you see a list of the client's **unassigned contacts**. To assign a contact:

1. Click the assign button next to the desired contact.
2. The contact is immediately moved to the case list (without a page reload).

To remove a contact from the case:

1. Click **Remove** next to the assigned contact.
2. The contact is detached from the case and reappears in the list of unassigned contacts.

> **Note:** Assigning and removing only changes the case association. The contact itself remains unchanged.

### Episodes

An **episode** is a distinct phase within a case. For example, if a client enters a crisis situation three times a year, these can be documented as three separate episodes within the same case.

**Creating a new episode:**

1. On the case detail page (right column), click **New Episode**.
2. Fill in the form:
   - **Title** (required): A label for the phase (e.g., "Crisis episode March 2026").
   - **Start** (required): The start date of the episode.
   - **Description:** Additional details (optional).
   - **End:** End date (optional -- leave blank if the episode is still ongoing).
3. Click **Save**.

> **Note:** Episodes can only be created for **open** cases.

**Editing an episode:** Click **Edit** next to the episode to adjust the title, description, start, or end date.

**Completing an episode:** Click **Complete**. The end date is automatically set to today's date.

Each episode shows its status:
- **active** (green) -- no end date yet
- **completed** (gray) -- end date set

### Outcome Goals and Milestones

**Outcome goals** document what the work on a case should achieve -- e.g., "Stable housing situation" or "Connection to addiction counseling". Each goal can be broken down into specific **milestones**.

**Creating a new outcome goal:**

1. On the case detail page (right column, "Outcome Goals" section), enter the title in the text field.
2. Click **Add**.
3. The goal appears immediately in the list (without a page reload).

**Editing an outcome goal:** Click the edit icon next to the goal title. An inline form opens where you can adjust the title and description.

**Marking a goal as achieved:** Click **Goal Achieved**. The goal is marked as achieved with today's date and receives a green badge. If a goal was marked as achieved by mistake, you can undo this with **Not Achieved**.

**Adding milestones:**

1. Below an outcome goal, enter the milestone title in the text field.
2. Click **+**.
3. The milestone appears as a checklist entry.

**Checking off a milestone:** Click the circle next to the milestone. Checked milestones are shown with a strikethrough. Clicking again removes the checkmark.

**Deleting a milestone:** Hover over the milestone and click the **x** icon.

> **Example:** Outcome goal "Stable housing situation" with milestones:
> - Initial housing assistance consultation completed
> - Application submitted
> - Housing found

### Permissions in Case Management

| Function | Assistant | Social Worker | Lead | Facility Admin |
|---|---|---|---|---|
| View case list | No | Yes | Yes | Yes |
| Create and edit cases | No | Yes | Yes | Yes |
| Close / reopen cases | No | No | Yes | Yes |
| Delete a case / request its deletion | No | Yes | Yes | Yes |
| Case trash: restore, anonymize permanently | No | No | No | Yes |
| Assign / remove contacts | No | Yes | Yes | Yes |
| Manage episodes | No | Yes | Yes | Yes |
| Outcome goals and milestones | No | Yes | Yes | Yes |

---

> **More questions?** The [FAQ](../faq.md) (German only) answers common questions about data protection, 2FA, offline mode, retention periods, and more.

---

*Anlaufstelle -- Documentation system for low-threshold social services*

<!-- translation-source: docs/user-guide.md -->
<!-- translation-version: v0.99.1 -->
<!-- translation-date: 2026-09-22 -->
<!-- source-hash: 213ac57505df6fa323112012ed47d29302b2e906 -->
