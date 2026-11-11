---
title: AI Monitoring
description: Track AI token usage across Nova Studio, the assistant, and AI SQL functions, by day, action, model, and user.
---

## What it is

**AI Monitoring** counts the AI work Nova records: saved assistant and Nova
Studio turns, and AI SQL functions such as `AI_COMPLETE`. It shows how many
**tokens** (the units AI providers count and bill by) were used, by whom, and
for what.

It is a usage record, not a bill. Nova does not estimate missing token counts
and does not calculate money.

## Before you start

Use an administrator role, such as `ACCOUNTADMIN`.

## Open the page

In the left menu, under **Operations** > **Monitoring**, click **AI
Monitoring**.

## Choose what to look at

Use the filters at the top:

| Filter | Choices |
| --- | --- |
| **Period** | **Today**, **Last 7 days**, **Last 14 days**, **Last 30 days**. |
| **Source** | **All sources**, **Nova Studio / Assistant**, **AI Functions**, or **Measured AI function tokens**. |
| **Model** | **All models** or one model. |
| **User** | **All users** or one person. |

**Clear filters** resets them. The page refreshes itself every 60 seconds while
**Auto-refresh** is on; **Refresh** updates it at once.

## What the page shows

| Section | What it tells you |
| --- | --- |
| **Usage summary** | **Reported tokens**, **Input / output**, **Observed activities**, and **Token coverage** (the share of activities that reported their tokens), compared with the previous period. |
| **Daily token usage** | A chart of tokens per UTC day. Today is still in progress. |
| **Daily breakdown** | The exact numbers per day: **Input**, **Output**, **Total**, **Activities**. |
| **Usage by action** | Tokens per kind of action, with **Avg. tokens**, **Missing totals**, and **Failed**. |
| **Top models** and **Top users** | The five biggest users of tokens. Click a name to filter the whole page by it. |
| **Activity history** | Each activity, most recent first. |
| **Data coverage & definitions** | Which sources were available for the period, and how the numbers are counted. |

An **activity** is one saved assistant turn or one audited AI SQL statement. It
can include several calls to the provider.

## Limits

- Activities whose provider did not report token counts are listed but left
  out of the totals. **Token coverage** tells you how complete the totals are.
- Dates are UTC.
