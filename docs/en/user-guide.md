> This is the English translation of [user-guide.md](../user-guide.md).
> The German version is the authoritative source. Last synced: 2026-07-12 (Offline V2 Wave 3, #1499), § 8 offline section only (#1417) and 2026-07-14 (§ 1 passkey second factor [#1492], § 7 PDF export rename, #1551).

# Anlaufstelle -- User Guide

This guide is intended for social workers, managers, and assistants working in drop-in centers, emergency shelters, and other low-threshold social service facilities that use Anlaufstelle.

---

## Table of Contents

1. [Login and Password](#1-login-and-password)
2. [Home -- Zeitstrom](#2-home--zeitstrom)
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

> **Tip:** If the QR code cannot be scanned, click **Enter secret manually** and copy the string into the app (field "Secret" / "Key" -- Base32, no spaces). In the app, pick type **TOTP / time-based**.

**Signing in with 2FA:**

1. Enter username and password as usual.
2. On the next page, enter the 6-digit code currently shown by the app.
3. Each code is valid for **30 seconds** -- if it expires, just use the next one.

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

### Changing the date

- **Arrow buttons** above the feed: jump to the previous or next day.
- **Today** link: returns to the current day.

### Shift filter

Below the date navigation you find **TimeFilter tabs** (e.g., "Early shift", "Late shift", "Night shift" -- configurable by the administration). When you open the Zeitstrom, the shift that matches the current time is auto-selected; night shifts crossing midnight are handled correctly.

When a shift filter is active, an expandable **shift handover block** appears above the feed with statistics (number of contacts, activities, new tasks) and highlights (crisis events, new entry bans, urgent tasks).

### Filtering the feed

Two dropdowns above the feed:

- **Type**: All · Events · Activities · Tasks · Entry Bans
- **Documentation type**: only entries of a specific type (e.g., "Contact", "Crisis counseling") -- you only see types your role is allowed to view.

Filter changes reload only the feed (HTMX), without a full page refresh.

### Entry Ban banner

If there are active entry bans in your facility, they are displayed as a red banner below the heading.

---

## 2a. Work Center

Alongside the Zeitstrom there is the **Work Center** (`/start/`) -- a role-specific overview page that condenses existing data into compact tiles. You can reach it via the sidebar link **"Work Center"** (above the Zeitstrom). The Zeitstrom at `/` remains unchanged -- the Work Center is an additional entry point, not a replacement.

Depending on your role, a different variant is shown:

**Social Worker / Assistant** -- title "Work Center", today's tasks and contacts at a glance:

- **Today's contacts** -- number of events recorded today (links to the Zeitstrom)
- **My tasks** -- your own open or in-progress tasks (links to the task inbox)
- **Recently edited** -- number of recently updated persons (links to the client list)
- Below these tiles: the lists **"My open tasks"** (up to 5, with due dates) and **"Recently edited persons"** (up to 5)

**Lead** -- title "Lead Work Center", GDPR workflows, retention, and statistics:

- **Deletion requests** -- pending requests (links to the deletion-request list)
- **Retention proposals** -- pending retention proposals (links to the retention dashboard)
- **Legal holds** -- active retention locks
- **Statistics** -- month/year of the last statistics snapshot

**Facility Admin** -- title "Admin Work Center", users, security, and configuration:

- **Users without MFA** -- active users with no confirmed 2FA (highlighted orange when > 0)
- **Configuration warnings** -- e.g. MFA not enforced facility-wide or k-anonymization disabled; individual warnings are listed below the tile

**Super Admin** -- title "System Work Center", cross-facility status and audit activity:

- **Tenants** -- number of facilities
- **Active users** -- across all tenants
- **Audit events 24h** -- audit entries from the last 24 hours
- **Critical events** -- security-relevant actions in the last 24 hours (failed logins, security violations, deletions, approved deletion requests; highlighted orange when > 0)

---

## 3. Documenting a Contact (Creating an Event)

An **event** is a single documented contact -- for example, a counseling session, a syringe exchange, or an anonymous visit.

### Recording a New Contact

1. Click **New** in the sidebar and select **New Contact** (or navigate to `/events/new/`).
2. **Select a documentation type:** Choose the appropriate type from the list (e.g., "Contact", "Crisis counseling", "Syringe exchange"). The available types are configured by your facility.
3. **Fill in the fields:** After selecting the type, the corresponding input fields are loaded. Fill in all relevant fields.
4. **Timestamp:** The "Timestamp" field is automatically set to the current time. You can adjust it if you are recording a contact retroactively.
5. **Assign a client (optional):**
 - For an **anonymous contact** (without a pseudonym): Enable the "Anonymous" option. No client will be linked.
 - For an **identified client**: Start typing the pseudonym in the client field. A suggestion list appears -- select the matching client.
 - If the client has not been registered yet, create them first under **Clients** (see [Section 4](#4-managing-clients)).
6. Click **Save**.

You will be redirected to the detail view of the newly created entry. A success message confirms the save.

> **Tip:** If you create a contact from the client detail page, the client is already pre-filled.

### Quick Templates

If your administrator has set up **quick templates** for recurring documentation patterns (e.g., "Counseling 30 min", "Standard check-in"), they appear as buttons at the top of the "New Contact" page.

- **Click a template button** to pre-fill the form with the template's saved values and documentation type.
- **You can still edit every field** before saving -- the template provides defaults, not a locked form.
- **Fields you have already filled in are not overwritten** -- the template only populates empty fields.
- **Sensitivity filter:** You only see templates whose documentation type you are allowed to access according to your role. Assistants therefore do not see templates for elevated- or high-sensitivity types.
- **Self-healing:** If an administrator has deactivated a selection option after the template was created, the corresponding value is silently dropped when you apply the template -- no error, you simply pick an active option yourself.

> **Note:** Templates are created and maintained by administrators in the Django admin interface. If you find yourself repeating the same documentation pattern, ask your admin to add a quick template.

### File Attachments

A file can be attached to each event -- for example, a scanned form, a photo of a document, or a PDF.

- **Upload:** The contact form includes a file upload field. Pick a file from your device and save the event as usual.
- **Virus scan:** Before saving, every file is automatically checked by a virus scanner (ClamAV). Infected files are rejected -- you receive an error and the event is not saved.
- **Encryption:** All attachments are stored encrypted (AES-GCM). That means the file is not readable in cleartext on the server and can only be retrieved through Anlaufstelle itself.
- **Maximum size:** By default, **up to 10 MiB per file** are allowed. Your administrator can adjust this limit.
- **Supported formats:** PDF, Office documents, and images. Ask your administrator for the exact list allowed in your facility.
- **Download:** Open the detail view of the event and click the file link. The file is automatically decrypted on retrieval and delivered in the browser.
- **Replacing (with version history):** When editing an event, you can replace the existing file with a new one. The old file is **not** deleted -- it is preserved as a **previous version** and remains downloadable from the event detail page via an expandable accordion. Previous versions are only removed when the event itself is fully deleted.

> **Offline note:** Events with file attachments currently **cannot** be saved offline. If you work offline, attaching a file shows an explicit hint. See [Section 8](#8-installing-the-pwa-and-working-offline).

> **Central files overview:** All file attachments in your facility can be browsed and filtered as a single list via the **Files page** -- see [Section 3a: Files Overview](#3a-files-overview).

### Editing a Contact

1. Open the event (via the Zeitstrom or the client chronology).
2. Click **Edit**.
3. Change the desired fields and click **Save**.

> **Note:** Edits are recorded in the event's change history. Previous versions remain visible in the history.

> **Concurrent editing:** If someone else edited the same record at the same time, an error message appears. Reload the page and re-enter your changes -- this ensures that no changes are silently overwritten.

### Deleting a Contact

1. Open the event.
2. Click **Delete**.
3. Enter a reason.
4. Confirm the deletion.

> **Important:** If the contact is associated with a **qualified client**, it will not be deleted immediately. Instead, a **deletion request** is automatically created, which must be approved by a lead or administrator (four-eyes principle). You will receive a corresponding notification.

---

## 3a. Files Overview

The **Files page** (`/attachments/`, sidebar entry **Files**) shows all attachments in your facility as a searchable list -- without having to open each event individually first.

### Access

- **Sidebar navigation -> Files**
- Available to assistants, social workers, leads, and administrators.

### Visibility

You only see files whose event **and** field reach your sensitivity role:

- Fields with "high" sensitivity remain hidden for social workers at a lower level -- the same gate as in the event detail view.
- Attachments of deleted events are not included.
- Facility scoping: you only see files from your own facility.

### Filters

Two filters above the table:

- **Documentation type** -- only attachments belonging to events of a specific type (e.g., "Counseling").
- **Client** -- all attachments for a specific pseudonym.

The filters can be combined and update the list via HTMX without a full page reload.

### Download

Each row shows the original filename. Clicking it delivers the decrypted file -- exactly as in the event detail view. Downloads are recorded in the audit log.

### Encryption

All files are stored encrypted on the server (AES-GCM). Decryption only happens at download request time inside the Django process; admins with direct disk access only see the encrypted binary data.

### Previous versions

Replaced files are preserved as previous versions (see [Section 3 -- File Attachments](#file-attachments)). The Files overview currently only shows the latest version per entry; previous versions are accessible via the entry on the event detail page.

---

## 4. Managing Clients

Clients in Anlaufstelle are recorded **exclusively using pseudonyms** -- no real names. The pseudonym is the primary identifier.

### Contact Levels

Each client has a **contact level**:

| Level | Description |
|---|---|
| **Identified** | Pseudonym known, basic data (age group) available |
| **Qualified** | Additional personal data available (restricted access, audit log) |

> **Note:** Access to qualified client profiles is automatically recorded in the audit log.

### Creating a New Client

1. Navigate to **Clients** (`/clients/`) and click **New Client** (or go directly to `/clients/new/`).
2. Fill in the form:
 - **Pseudonym:** A unique name within your facility (e.g., a self-chosen nickname). The pseudonym must be unique within a facility.
 - **Contact level:** Select "Identified" or "Qualified".
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

From the detail page, you can directly record a new contact for this client or create a new task.

### Editing a Client

1. Open the client detail page.
2. Click **Edit**.
3. Change the desired fields (e.g., upgrading the contact level).
4. Click **Save**.

> **Note:** A change in contact level is automatically recorded in the audit log.

> **Concurrent editing:** If someone else edited the same record at the same time, an error message appears. Reload the page and re-enter your changes -- this ensures that no changes are silently overwritten.

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

### Creating a New Task or Hint

1. Click **New Task** in the inbox (or navigate to `/workitems/new/`).
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

For more extensive research, the search page at `/search/` is available. It shows all results (up to 20 clients and 20 events) and is also accessible via the "Show all results" link in the quick search.

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
 - **Last month** (default)
 - **Last quarter** (90 days)
 - **Last half-year** (182 days)
 - **Custom:** Enter start and end dates manually.
3. The dashboard updates automatically and displays aggregated key figures for contact numbers, documentation types, and client groups.

### Year Navigation

To view data for a specific year:

1. Click **Year**.
2. Use the arrow buttons to the left and right of the year number to navigate to the previous or next year.
3. For the current year, the period from January 1st to today is shown. For past years, the full year (January 1st -- December 31st) is displayed.

### Trend Charts

Below the key figures, the dashboard shows three interactive charts:

| Chart | Description |
|-------|-------------|
| **Contacts Over Time** | Line chart with monthly breakdown of contacts (Total, Anonymous, Identified, Qualified) |
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

1. Select the time period in the statistics dashboard.
2. Click **Example Activity Report**.
3. The PDF file is downloaded (filename: `jugendamt_YYYY-MM-DD_YYYY-MM-DD.pdf`).

> **Tip:** For semi-annual reports, select the "Last half-year" time period and manually adjust the start and end dates to 01/01 and 06/30 (or 07/01 -- 12/31) as needed.

### Privacy-friendly External Reports (Lead / Admin)

For sharing data with external parties (e.g., funding bodies or municipal authorities), an **external report with a privacy profile** is available at `/statistics/external/`. It uses the same time-period filters as the statistics dashboard but is deliberately data-minimal:

- **No pseudonym ranking** -- the top-persons list from the internal dashboard is omitted entirely.
- **K-anonymization** -- aggregates with fewer than *k* records (threshold from the facility settings, default 5) are shown as **"suppressed"** rather than as a concrete number. This applies to "Unique persons", "By documentation type", and "By age group".
- **Privacy profile header** -- facility, profile (`external`), time period, k-anonymity threshold, and generation timestamp appear at the top of the report.

Via **"Export as JSON"** (or by appending `?format=json` to the URL) you receive the same data in machine-readable form. Every request is logged as an `EXPORT` event in the audit log. The external report is accessible to **leads** and **administrators** only.

### Reviewing Deletion Requests (Lead / Admin)

When a social worker or assistant wants to delete a contact belonging to a qualified client, a **deletion request** is created that must be approved by a lead or administrator.

1. View open deletion requests at `/deletion-requests/`.
2. Click on a request to see the details and the affected event.
3. Click **Approve** or **Reject** and confirm.

> **Important:** You cannot approve your own deletion request (four-eyes principle).

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
- You can **record new events** (via **"New Contact"**) and **edit existing ones**; entries are stored encrypted locally (AES-GCM-256; the key is derived from your password) and sent automatically once you are back online. **File attachments** cannot be captured offline -- add them once you are back online.
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

> **No file attachments offline:** Events with file attachments **cannot** be saved offline. For security reasons, no unencrypted file blobs are stored in the browser. In that case, record the event without the file first and attach the file once you are back online.

---

## 9. Roles and Permissions

Anlaufstelle distinguishes five roles. Your role is assigned by the facility administrator (Anwendungsbetreuung) of your facility.

### Role Overview

| Role | Display Name | Brief Description |
|---|---|---|
| `assistant` | Assistant | Limited data entry without access to qualified client data |
| `staff` | Social Worker | Default role for employees doing documentation |
| `lead` | Lead | All social worker functions plus reports and management tasks |
| `facility_admin` | Facility Admin | Full access to all areas and settings **of your facility** (audit log, GDPR package, user management) |
| `super_admin` | System Admin | Hosting/carrier role: cross-facility, bootstrap tools, separate `/system/` area. **Not visible in the regular staff UI** -- end users do not interact with it. |

> **Note:** The System Admin (`super_admin`) is a hosting/carrier role. It sets up the installation and creates the first facility and the first Facility Admin. You will not encounter it in day-to-day operations -- it works exclusively in the `/system/` area, which is not accessible to facility-bound users.

### Who Can Do What?

Table covers the four facility-bound roles only -- `super_admin` is excluded here because it does not work in the staff UI:

| Function | Assistant | Social Worker | Lead | Facility Admin |
|---|---|---|---|---|
| View Zeitstrom / home page | Yes | Yes | Yes | Yes |
| Document anonymous contacts | Yes | Yes | Yes | Yes |
| Document identified contacts | Yes | Yes | Yes | Yes |
| Create and search for clients | Yes | Yes | Yes | Yes |
| View qualified client details | No | Yes (own facility) | Yes | Yes |
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

The audit log (`/audit/`) automatically records security-relevant actions: sign-ins, access to qualified data, exports, deletions, and contact level changes. It cannot be modified and serves traceability purposes in accordance with the GDPR. Access is granted to the Facility Admin (`facility_admin`) for their own facility.

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

### Creating a New Case

1. Click **New Case** on the case list (or navigate to `/cases/new/`).
2. Fill in the form:
 - **Title** (required): A short label for the case (e.g., "Housing search", "Addiction counseling").
 - **Client:** Start typing the pseudonym -- a suggestion list appears. Select the matching client. A case can also be created without a client.
 - **Description:** More detailed information about the case (optional).
 - **Case owner:** Select the responsible person from the dropdown (optional). Only social workers, leads, and administrators from your facility are available.
3. Click **Create Case**.

You will be redirected to the detail page of the new case.

> **Tip:** If you create a new case from the client detail page, the client is already pre-filled.

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
| Assign / remove contacts | No | Yes | Yes | Yes |
| Manage episodes | No | Yes | Yes | Yes |
| Outcome goals and milestones | No | Yes | Yes | Yes |

---

> **More questions?** The [FAQ](../faq.md) (German only) answers common questions about data protection, 2FA, offline mode, retention periods, and more.

---

*Anlaufstelle -- Documentation system for low-threshold social services*

<!-- translation-source: docs/user-guide.md -->
<!-- translation-version: v0.20.0 -->
<!-- translation-date: 2026-07-14 -->
<!-- source-hash: 67c1567 -->

