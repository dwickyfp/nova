---
title: Business Ontology
description: Look up shared business definitions, their owners, and the metrics that compute them.
---

## What it is

Business Ontology is the shared dictionary used by Studio. A **concept** defines
a business term, such as revenue or customer. A **domain** groups concepts and
identifies the roles responsible for reviewing them. A metric binding connects
a concept to a calculation in a published Semantic View.

## Find a definition

1. Open **AI & ML → Business Ontology**.
2. Select **Concepts** and find the business term.
3. Open its name to read the definition, **Unit**, **Other names**,
   **Classification**, and **Version**.
4. Review **Computed by** for its metric bindings and **Related concepts** for
   connections to other terms.
5. Read **History** to see earlier versions, then click **Close**.

Different names may describe the same concept. Check the definition and unit
before treating two similarly named measures as interchangeable.

## Review ownership and proposed changes

**Domains** lists each domain's **Stewards**, **Readers**, and **Status**.
Steward roles review changes; reader roles determine who sees the concepts.
Visibility of a definition does not grant access to the underlying data.

**Proposals** separates changes into **In review**, **Approved**, **Applied**,
and **Closed**. An empty **In review** list means no proposal is waiting for
review. An approved proposal and a definition currently used by Studio are
separate states: check the release before relying on a change.

## Check consistency

**Derived** contains relationships inferred from published definitions.
**Checks** compares business definitions with formulas and checks names for
possible confusion. These checks advise stewards; the page identifies them as
warnings rather than release blockers.

Use [Semantic Views](/docs/ai-ml/semantic-views) to inspect the calculation behind
a bound metric. A familiar business name is not a substitute for a valid,
readable source and a published calculation.

## See what Studio uses

Open **Releases** and inspect **In use by Studio**. Release history distinguishes
prepared changes from the definitions selected for use. **Export as OSI** is
available on this page for exporting the ontology.

## Limits

- Only the concepts visible to your role appear.
- A term with no metric binding supplies a definition, not a runnable measure.
- Proposals and prepared releases do not become live merely because they exist.
- Domain access and source-data permissions are separate checks.
