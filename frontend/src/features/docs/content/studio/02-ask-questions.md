---
title: Ask a question
description: Choose an agent, ask a question in plain language, and read the answer and how it was found.
---

## What it is

On the **Agent** tab you type a question, such as "Show net revenue per month
from May to October 2026 as a line chart", and the selected agent finds the
data, runs the query for you, and writes the answer. You do not need to know
SQL or table names.

## Before you start

- Open Studio from **Nova Studio** in the Console menu.
- Check the role under your name in the bottom-left corner. The agent answers
  only from data that role may read.

## Steps

1. Click **New chat** in the left rail.
2. Below the message box, click the agent button (it shows the current agent,
   or **Select an agent**) and choose an agent from the **Agents** list. Pick
   the one whose description matches your question, for example a sales
   agent for revenue questions.
3. Click in the box that says **Ask a question about your data** and type your
   question.
4. Press **Enter** to send. Press **Shift+Enter** if you want a new line
   instead.
5. Watch the agent work. While it works, a **Working** line shows each step
   and how long it has taken, and new steps appear as they happen. Click
   **Stop** (the square button) to cancel. Stop also cancels a **Reconsider
   this answer** pass. A stopped answer shows "Stopped before the answer
   finished.", and its step summary starts with "Stopped" and a grey square
   instead of a green check.

### What appears while the agent works

Steps appear in the order the agent takes them, as soon as each one starts:

- **Understanding the request**: the first step names the data and the number
  of skills the agent is choosing from, for example "Understanding the request:
  weighing the Sales orders data and 6 skills". With some AI models this step
  takes several seconds.
- **What the agent is about to do**, in one sentence, before it runs a step.
  For example "I will compare revenue by region first." These sentences never
  contain figures. Figures come only in the checked answer.
- **Model reasoning**: some AI models write a short summary of their thinking.
  When the model sends one, it appears as a **Model reasoning** step with the
  latest words shown under the label. It folds away when the answer is done;
  click it to read it in full. Models that do not send a summary show no such
  step. The summary is for you to read: the agent does not reuse it in later
  answers, and credentials and keys are removed from it before it is shown.
- **Each tool call**, such as a query, with its own status.

The answer text appears only after its figures were checked against the
results the agent received, so a number you see in an answer never changes
afterwards. A short note under the answer says "Figures checked against the
results". When a figure could not be found in the results, it is left out and
the note adds "unsupported ones were left out".

## Use a command

Type **/** at the start of the message box to see the **Commands** list above
the box. Keep typing to narrow it. Use the up and down arrows to pick a
command and press **Enter** or **Tab** to insert it, or click it. Press
**Esc** to close the list.

**/create-skill-with-chat** starts writing a skill with Nova. Type the task
you repeat after the command and press **Enter**. See
[Capabilities](/docs/studio/capabilities) for where your saved skills live.

## Read the answer

When the agent finishes you see:

- **The answer text**, written for a business reader.
- **Tables, charts or maps** when the answer rests on data. See
  [Charts in answers](/docs/studio/charts-in-answers).
- **Source data**: when the agent drew a chart, the rows it used are folded
  under **Source data (1 table)**. Click it to see them.
- **Tools used**: one chip per tool the agent used, such as **SQL query** or
  a connected tool, with **×2** when it ran more than once and a dot for how
  it ended (green: succeeded, red: failed, grey: not finished). Click a chip
  to see each call: what it was asked, a short result and how long it took.
  A failed call also shows why, in one short line, such as "Nova could not
  compare those periods as asked." **Show in steps** opens that step above the answer and **Show data** opens
  the rows it returned. Web pages the agent read are listed under the web
  search chip.
- **Follow-up suggestions**: related questions under the answer, each with a
  return arrow (for example "Net revenue by payment method"). Click one to
  ask it.

### See how the answer was found

Above the answer is a summary such as "Planned the run, Reasoned, Queried the
semantic model, and more · 7 steps". Click it to open every step: the plan,
the tools the agent chose (for example "Chose the semantic view and a chart"),
each tool it called, and, for data queries, the **SQL** that ran. Use **Copy**
to copy that SQL into a worksheet.

### Read a result table

Amounts show with thousands separators and at most two decimals (for example
24,999,000 or 10,165,333.33), dates show as dates (Jun 1, 2025), and column
headings show as words (Shipping city). Identifiers such as IDs, codes and
years are shown exactly as stored. Hover over a heading to see the column's
name in the data. **Download CSV** keeps the values exactly as the query
returned them.

Steps and answer parts appear in the order the agent produced them. When the
agent writes part of the answer and then runs more steps, those steps appear
between the parts they came between, with their own summary line.

### When the role changes during an answer

If the agent switches your active role while it answers (for example because
you asked it to work as another of your roles), a line reads "Role changed to *role*. Earlier messages
are hidden because they belong to the previous role." Steps and text produced
under the previous role are removed, and the answer continues below the line
under the new role.

### Answer buttons

Under each answer:

| Button | What it does |
| --- | --- |
| **Copy answer** | Copies the answer text. |
| **Like answer** / **Dislike answer** | Tells the agent's owner whether the answer helped. |
| **Reconsider this answer** | Asks the agent to work the same question again. |
| **Answer details** | Shows the model used and the tokens the answer took. |

The token count next to the buttons (for example "13,189 tokens") is how much
AI model work the answer used.

## How the agent works out figures

- **A date without a year** means the year in the data. "Revenue in July"
  uses July of the year the data covers. When the data covers more than one
  year, the agent asks which one and offers only the years in the data.
- **Comparing periods**: name both periods, such as "June 2025 and July 2025",
  "this week against last week", or ask for "the trend by month". When the
  agent cannot read a comparison, it says "Nova could not compare those
  periods as asked" and asks you to name both periods.
- **Median and percentiles**: ask for "the median order value" or "the 90th
  percentile of order value". These work for measures that add up, average or
  take the highest or lowest of one value per record. A median of a count is
  not available.
- **What if**: "What if revenue grew 10%?" or "What if it doubled?" The agent
  takes the current figure from the data and works out the new figure and the
  change exactly. It does not estimate them.
- **Against an overall figure**: "Which city has the highest average order
  value, and how far is it above the overall average?" The agent fetches both
  figures and works out the difference or the percentage between them.
- **Worked-out figures** (a percent change, a difference, a what-if total)
  appear as their own table under the answer. A follow-up about the data, such
  as "Show the distribution", still uses the rows those figures came from.
- **Conditions that match nothing**: "orders shipped to Bandung" means orders
  whose shipping city is Bandung. If a combination of conditions matches no
  record, the agent says so, then gives the figure without the condition that
  left nothing, labelled as such.
- **A table the agent cannot use**: if you name a table or schema that this
  agent does not use, such as `analytics.sales`, the agent says first that it
  is not available here. Any figure it gives instead names the data it came
  from. It never shows data your active role cannot read.

## What the agent does on its own

A Studio agent works without asking permission. When your question needs it,
the agent runs its tools and takes actions itself: for example it calls an
outside tool, schedules an automation, creates an agent or a semantic model,
changes a Dataviz dashboard or a definition, or switches to another of your
roles. Each step appears in the answer's steps as it happens.

What stays in place:

- The agent works as you, with your active role. It reads only what that role
  may read, and every query goes through the same access policies, row filters
  and masks as your own.
- It never runs a statement that would change data from a question.
- Only an administrator connects outside tools, and an administrator can turn
  any tool off.
- Every tool call is recorded in the audit log under your name and role, with
  its inputs (passwords, tokens and keys are hidden).

When the agent schedules an automation, for example "Send a revenue summary
every morning at 7", the answer states only the next run and the time zone
that Nova's scheduler returned, for example 07:00 in Asia/Jakarta. If the
schedule could not be created, the answer says "Nothing was scheduled." and
why. Each run appears in **History**.

A scheduled automation runs with nobody there, so it only reads: it answers
from data and can call an outside tool whose server says it only reads, but it
never takes an action, calls any other outside tool, or schedules another
automation.

Answers from **Neva** work differently: Neva asks before an action that changes
something. See [Write SQL in the SQL Workspace](/docs/sql/sql-workspace).

## When the agent asks you

Sometimes the agent needs you to say what you mean before it continues. The
request appears in the answer, in the place where the agent stopped, and waits
for you. Only you can answer it: other people, and you under another role or
another sign-in, cannot.

| Card | What it asks | How to answer |
| --- | --- | --- |
| **Question** | Which of a few things you mean | Click a suggested answer, or type in **Your answer** and press **Enter** |
| **Details needed** | A few values, such as a period or a region | Fill in the fields and click **Continue** |
| **Choose one** | Which of a few approaches to take | Pick a card (the agent may mark one **Suggested**) and click **Continue with this option** |

On a question, form or choice, **Skip, let the agent assume** lets the agent
continue with its own assumption, which it states in the answer.

### After you answer

The card closes to one line, such as "You chose: By month". Click the line to
see what was asked. Conversations from before agents acted on their own may
show a closed approval line such as "You allowed this action"; it cannot be
answered again. The same line appears when you
open the conversation again from **History**.

A request waits up to 10 minutes; after that it shows "The request expired".
Clicking **Stop** while a request is waiting closes it as "Stopped before you
answered". An agent asks at most four times per answer, and never asks during
scheduled automations, where nobody is there to answer.

Requests belong to the login and active role that asked them. A pending
request cannot be answered from another login, even for the same account.

## Limits and errors

- When the latest question failed or you stopped it, click **Retry** under it
  to send the same question, with the same attached files, again as a new
  message. The failed attempt stays in the conversation. A question reopened
  from **History** that had attached files does not offer **Retry**, because
  the files are not kept; attach them again and ask. Check the results of any
  actions before retrying.

- One agent answers each question. Studio does not hand a question from one
  agent to another.
- If the agent asks which metric you mean (for example two metrics with the
  same name in different areas) in its answer text rather than in a card,
  answer in the next message with the one you want.
- "The agent run stopped. Review saved steps before retrying." means the run
  was interrupted. Open the conversation again from **History**: anything the
  agent saved is shown. Check the results of any actions before asking again.
- "Saved run events are unavailable." means Nova cannot replay all the saved
  steps for a completed answer. Open the conversation from **History** to
  review what remains. Check any actions before asking again; this message
  does not mean an action failed.
- "The run stopped because its progress could not be saved." means Nova
  stopped the agent after a storage problem. A partial answer may remain on
  screen. Waiting requests close, and an unfinished tool is marked **Result
  not confirmed**. Review **History** and check any actions before trying
  again; an action may have completed even if its result could not be saved.
