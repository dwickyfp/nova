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
- **Follow-up suggestions**: buttons with related questions (for example
  "net revenue by payment method"). Click one to ask it.

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

If the agent switches your active role while it answers (for example after you
approve a role switch), a line reads "Role changed to *role*. Earlier messages
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

## When the agent asks you

Sometimes the agent stops and asks you something before it continues. The
request appears in the answer, in the place where the agent stopped, and waits
for you. Only you can answer it: other people, and you under another role or
another sign-in, cannot.

| Card | What it asks | How to answer |
| --- | --- | --- |
| **Approval needed** | Permission to take an action that changes something | **Allow once** or **Deny** |
| **Question** | Which of a few things you mean | Click a suggested answer, or type in **Your answer** and press **Enter** |
| **Details needed** | A few values, such as a period or a region | Fill in the fields and click **Continue** |
| **Choose one** | Which of a few approaches to take | Pick a card (the agent may mark one **Suggested**) and click **Continue with this option** |

On a question, form or choice, **Skip, let the agent assume** lets the agent
continue with its own assumption, which it states in the answer.

### Approve an action

Questions about your data run without asking. The agent reads only what your
active role may read, so there is nothing to approve. Statements that would
change data are never run from a question.

An action that changes something always asks first: for example scheduling an
automation, calling an outside tool, creating an agent or a semantic model,
changing a Dataviz dashboard or a definition, or switching your role. The card
says what the action does, the role it runs as, and why it asks.

- **Allow once** takes this action.
- **Deny** stops this action; the agent continues without it.

Each approval covers one action. The next action asks again.

### After you answer

The card closes to one line, such as "You chose: By month" or "You allowed this
action". Click the line to see what was asked. The same line appears when you
open the conversation again from **History**.

A request waits up to 10 minutes; after that it shows "The request expired".
Clicking **Stop** while a request is waiting closes it as "Stopped before you
answered". An agent asks at most four times per answer, and never asks during
scheduled automations, where nobody is there to answer.

Approvals belong to the login and active role that requested them. After
switching roles or signing in again, start a new request to approve an action. A
pending request cannot be answered from another login, even for the same
account.

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
