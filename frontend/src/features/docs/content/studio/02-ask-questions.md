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

Tool approvals belong to the login and active role that requested them. After
switching roles or signing in again, start a new request to approve a tool. A
pending approval cannot be accepted from another login, even for the same account.

## Limits and errors

- One agent answers each question. Studio does not hand a question from one
  agent to another.
- If the agent asks which metric you mean (for example two metrics with the
  same name in different areas), answer in the next message with the one you
  want.
- "The agent run stopped. Review saved steps before retrying." means the run
  was interrupted. Open the conversation again from **History**: anything the
  agent saved is shown. Check the results of any actions before asking again.
- "Saved run events are unavailable." means Nova cannot replay all the saved
  steps for a completed answer. Open the conversation from **History** to
  review what remains. Check any actions before asking again; this message
  does not mean an action failed.
- "The run stopped because its progress could not be saved." means Nova
  stopped the agent after a storage problem. A partial answer may remain on
  screen. Pending approvals close, and an unfinished tool is marked **Result
  not confirmed**. Review **History** and check any actions before trying
  again; an action may have completed even if its result could not be saved.
