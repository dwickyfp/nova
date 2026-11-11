---
title: Production Health
description: See which health alerts are firing and how the cluster scores on a production readiness audit.
---

## What it is

**Production Health** checks the cluster against two lists:

- **Alert rules**: live checks that fire when something is wrong now, for
  example *StarRocks FE is down* or *No FE Leader elected*.
- **Production readiness audit**: a read-only review of how the cluster is set
  up, for example whether there are enough nodes to survive one failing.

## Before you start

Use an administrator role, such as `ACCOUNTADMIN`.

## Open the page

In the left menu, under **Operations** > **Monitoring**, click **Production
Health**. The alerts refresh every 30 seconds; **Refresh** updates the page at
once.

## Read the alerts

The top of the page sums up the alerts: **Overall**, **Critical alerts**,
**Warnings**, and **No data**.

Each alert under **Alert rules** shows its title, its status (**ok**, the
severity when it fires, or **no data** when the check could not be measured),
a description, the measured value, and the threshold it is compared with.

## Read the readiness audit

Each finding under **Production readiness audit** has a category (such as
membership, version, or storage), a name, a status, and a sentence explaining
it. For example, a single-node test cluster reports *Only 1 voting FE(s); a
3-FE candidate/leader topology is the minimum for metadata fault tolerance.*
That is expected on a development machine and important on a production one.

## Limits

The page only reads. It does not change the cluster. When the engine does not
answer, the page says so and the cluster may still be serving queries.
