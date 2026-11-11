---
title: ML Models
description: Train a model from a query, give a version an alias, and predict new values.
---

## What it is

A model learns from examples. **Classification** predicts a category, such as
whether a customer leaves. **Regression** predicts a number, such as spending.
An alias is a name that points to a particular model version for predictions.

## Before you start

Your active role must be able to read the training data. Prepare a query whose
rows contain both the value to predict (the target) and the inputs used to
predict it (the features). Keep identifiers and values learned after the outcome
out of the features.

## Train a model

1. Open **AI & ML → ML Models** and select **Models**.
2. Click **Train model**. Enter **Model name**, **Model type**, and **Algorithm**.
3. In **Training SQL Query**, enter your query. Set **Target column** to a
   column returned by that query.
4. Fill **Feature Columns (optional)** with comma-separated input columns, or
   leave it empty to use every returned column except the target.
5. Set **Test size**, the fraction of rows held aside for evaluation. The
   default is 0.2, or 20%.
6. Set **Database (optional)** to keep the model in the intended database.
7. Click **Train Model** and wait for the result. Check **Status**, **Metrics**,
   and **Rows** in the model list.

For classification, **Random Forest** is one available algorithm. Training can
fail if the query omits the target or a selected feature. A model record by
itself does not establish that training succeeded: check its version status.

The list also shows models trained through SQL, including clustering, anomaly
detection, and forecasting. The training form creates classification and
regression models. See [Machine learning with SQL](/docs/ai-ml/machine-learning-sql)
for complete examples of all five model types.

## Give a version an alias

Open **Aliases** and create an alias for a ready model version. Choose the
model and version, then save. Aliases belong to the model's database: the same
name in another database identifies a different alias.

Changing an alias changes the version used by subsequent predictions. Check
its model, version, and database before using it.

## Predict

1. Open **Predict** and select the alias for the model you trained.
2. Supply values for the model's feature columns. Keep numbers numeric; use
   the original category text for categorical features.
3. Run the prediction and inspect the result. Classification can include a
   probability for each class.
4. For batch prediction, supply a query that returns the required feature
   columns. The result contains a prediction for each input row.

A prediction is an estimate. Check evaluation metrics and representative
examples before using it to make decisions.

## Limits and errors

- An alias must refer to a model version in the same database scope.
- **Model version not found** can indicate the wrong model, version, or database.
- Prediction inputs must include the features used in training.
- Deleting a model or alias can break workflows that use it. Remove only
  resources you no longer need.
