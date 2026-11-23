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
5. Wait for the answer. While the agent works, a **Working** line shows each
   step and how long it has taken. Click **Stop** (the square button) to cancel.
   Stop also cancels a **Reconsider this answer** pass. A stopped answer shows
   "Stopped before the answer finished."

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

Above the answer is a summary such as "Planned the run, Reasoned, Ran
semantic_query, and more · 7 steps". Click it to open every step: the plan,
each tool the agent called, and, for data queries, the **SQL** that ran. Use
**Copy** to copy that SQL into a worksheet.

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
| **Approval needed** | Permission to run a data step | **Allow once**, **Allow for this chat**, **Edit then run** or **Deny** |
| **Question** | Which of a few things you mean | Click a suggested answer, or type in **Your answer** and press **Enter** |
| **Details needed** | A few values, such as a period or a region | Fill in the fields and click **Continue** |
| **Choose one** | Which of a few approaches to take | Pick a card (the agent may mark one **Suggested**) and click **Continue with this option** |

On a question, form or choice, **Skip, let the agent assume** lets the agent
continue with its own assumption, which it states in the answer.

### Approve a data step

An agent can be set to ask before each data step, and any step that changes
data or switches your role always asks. The card shows:

- **Semantic request**: the Semantic View and the question the agent will ask
  it, for a step that reads through a Semantic View. Nova builds the SQL from
  that view and runs it read-only. The exact SQL appears on the step after it
  runs.
- **SQL that will run**, for a step that runs SQL directly.
- The class of step (for example **Read-only** or **Changes data**), the role
  it runs as, the objects it uses, and why the agent asks.

Choose:

- **Allow once** runs this step.
- **Allow for this chat** also lets later read-only steps in this chat run
  without asking. It is not offered for steps that change data.
- **Deny** stops this step; the agent continues without it.
- **Edit then run** opens the request for editing. Change it and click **Run
  edited request**, or **Cancel edit**. Nova checks the edited request again
  against your role's access. If your role may not run it, or the edit turns a
  read-only step into one that changes data, the step does not run and the card
  shows "Nova refused the edited request" with the reason.

### After you answer

The card closes to one line, such as "You chose: By month" or "You allowed this
step". Click the line to see what was asked. The same line appears when you
open the conversation again from **History**.

A request waits up to 10 minutes; after that it shows "The request expired".
Clicking **Stop** while a request is waiting closes it as "Stopped before you
answered". An agent asks at most four times per answer, and never asks during
scheduled automations, where nobody is there to answer.

Approvals belong to the login and active role that requested them. After
switching roles or signing in again, start a new request to approve a step. A
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
