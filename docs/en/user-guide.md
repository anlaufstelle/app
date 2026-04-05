> This is the English translation of [user-guide.md](../user-guide.md).
> The German version is the authoritative source. Last synced: 2026-03-28 (v0.9.0).

# Anlaufstelle -- User Guide

This guide is intended for social workers, managers, and assistants working in drop-in centers, emergency shelters, and other low-threshold social service facilities that use Anlaufstelle.

---

## Table of Contents

1. [Login and Password](#1-login-and-password)
2. [Home -- Dashboard](#2-home--dashboard)
3. [Documenting a Contact (Creating an Event)](#3-documenting-a-contact-creating-an-event)
4. [Managing Clients](#4-managing-clients)
5. [Hints and Tasks (Work Items)](#5-hints-and-tasks-work-items)
6. [Search](#6-search)
7. [Statistics and Export](#7-statistics-and-export)
8. [Installing the PWA (App on the Home Screen)](#8-installing-the-pwa-app-on-the-home-screen)
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

### Changing Your Password

1. Click on your name or the user menu in the top-right corner.
2. Select **Change Password** (URL: `/password/`).
3. Enter your current password.
4. Enter the new password twice.
5. Click **Save**.

> **Tip:** Choose a secure password (at least 12 characters, including upper and lower case letters and digits). The system enforces minimum requirements.

### Signing Out

Click **Sign Out** in the top-right corner. For data protection reasons, you will be automatically signed out after a configured period of inactivity (default: 30 minutes).

---

## 2. Home -- Dashboard

After signing in, you land on the **Dashboard** (`/`). The dashboard gives you a personalized overview of your daily work.

### Widgets

The dashboard consists of four widgets:

| Widget | Description |
|--------|-------------|
| **My Tasks** | Your open and in-progress tasks, sorted by priority and due date |
| **Overview** | Key figures: contacts today, open cases, your tasks, total tasks |
| **Today** | A compact daily feed showing recent activities and contacts. Use "Show all" to go to the full activity log. |
| **Recent Clients** | Recently visited client profiles for quick access |

### Showing and Hiding Widgets

1. Click the **gear icon** next to the "Dashboard" heading.
2. In the dropdown, toggle individual widgets on or off.
3. Your settings are saved automatically and persist across logins.

### Entry Ban Banner

If there are active entry bans in your facility, they are displayed as a red banner below the heading -- on the dashboard as well as in the activity log.

### Activity Log and Timeline

In addition to the dashboard, two more views are available:

**Activity Log** (`/aktivitaetslog/`): The full daily feed of all documented contacts and system activities. Accessible via the sidebar navigation or the "Show all" link in the dashboard widget.

- **Change date:** Navigate to the previous or next day using the arrow buttons.
- **Filter feed type:** Select "All", "Events", or "Activities" from the dropdown.

**Timeline** (`/timeline/`): A shift-based view with TimeFilter tabs (e.g., "Early shift", "Late shift", "Night shift"). The timeline shows only contacts (events), not system activities.

1. Click on the desired shift tab.
2. The event list updates immediately and shows only contacts from that time window.

> **Note:** The tab marked as default is automatically activated when the timeline is opened.

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

### Editing a Contact

1. Open the event (via the activity log or the client chronology).
2. Click **Edit**.
3. Change the desired fields and click **Save**.

> **Note:** Edits are recorded in the event's change history. Previous versions remain visible in the history.

### Deleting a Contact

1. Open the event.
2. Click **Delete**.
3. Enter a reason.
4. Confirm the deletion.

> **Important:** If the contact is associated with a **qualified client**, it will not be deleted immediately. Instead, a **deletion request** is automatically created, which must be approved by a lead or administrator (four-eyes principle). You will receive a corresponding notification.

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

The CSV export contains all events from the selected time period with all non-encrypted fields.

1. Open the statistics dashboard and select the desired time period.
2. Click **Export CSV**.
3. The file is downloaded immediately (filename: `export_YYYY-MM-DD_YYYY-MM-DD.csv`).

> **Note:** Every export is recorded in the audit log.

### PDF Report

The PDF report generates a structured semi-annual report for internal documentation.

1. Select the time period in the statistics dashboard.
2. Click **PDF Report**.
3. The PDF file is downloaded (filename: `bericht_YYYY-MM-DD_YYYY-MM-DD.pdf`).

### Youth Welfare Office Report

The youth welfare office export generates a standardized report in the official youth welfare office format.

1. Select the time period in the statistics dashboard.
2. Click **Youth Welfare Report**.
3. The PDF file is downloaded (filename: `jugendamt_YYYY-MM-DD_YYYY-MM-DD.pdf`).

> **Tip:** For semi-annual reports, select the "Last half-year" time period and manually adjust the start and end dates to 01/01 and 06/30 (or 07/01 -- 12/31) as needed.

### Reviewing Deletion Requests (Lead / Admin)

When a social worker or assistant wants to delete a contact belonging to a qualified client, a **deletion request** is created that must be approved by a lead or administrator.

1. View open deletion requests at `/events/deletion-requests/`.
2. Click on a request to see the details and the affected event.
3. Click **Approve** or **Reject** and confirm.

> **Important:** You cannot approve your own deletion request (four-eyes principle).

---

## 8. Installing the PWA (App on the Home Screen)

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

> **Note:** The app is a **Progressive Web App (PWA)** -- it still requires an internet connection to function. It is not an offline application.

> **Firefox (Android):** Firefox does offer "Install", but the app always opens with the address bar visible. For the true app mode without the address bar, use **Chrome, Edge, or Samsung Internet** (Android) or **Safari** (iOS).

---

## 9. Roles and Permissions

Anlaufstelle distinguishes four roles. Your role is assigned by the administrator.

### Role Overview

| Role | Display Name | Brief Description |
|---|---|---|
| `admin` | Administrator | Full access to all areas and settings |
| `lead` | Lead | All social worker functions plus reports and management tasks |
| `staff` | Social Worker | Default role for employees doing documentation |
| `assistant` | Assistant | Limited data entry without access to qualified client data |

### Who Can Do What?

| Function | Assistant | Social Worker | Lead | Admin |
|---|---|---|---|---|
| View dashboard / home page | Yes | Yes | Yes | Yes |
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
| View audit log | No | No | No | Yes |
| Case management (cases, episodes, goals) | No | Yes | Yes | Yes |
| Close / reopen cases | No | No | Yes | Yes |
| Manage users and settings | No | No | No | Yes |

> **Note:** Access is always restricted to your own facility. Employees of one facility cannot see data from other facilities within the same organization.

### Audit Log (Admin Only)

The audit log (`/audit/`) automatically records security-relevant actions: sign-ins, access to qualified data, exports, deletions, and contact level changes. It cannot be modified and serves traceability purposes in accordance with the GDPR.

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

| Function | Assistant | Social Worker | Lead | Admin |
|---|---|---|---|---|
| View case list | No | Yes | Yes | Yes |
| Create and edit cases | No | Yes | Yes | Yes |
| Close / reopen cases | No | No | Yes | Yes |
| Assign / remove contacts | No | Yes | Yes | Yes |
| Manage episodes | No | Yes | Yes | Yes |
| Outcome goals and milestones | No | Yes | Yes | Yes |

---

*Anlaufstelle -- Documentation system for low-threshold social services*

<!-- translation-source: docs/user-guide.md -->
<!-- translation-version: v0.9.0 -->
<!-- translation-date: 2026-03-28 -->
<!-- source-hash: 6e14fb6 -->
