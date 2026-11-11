---
title: Create and run a notebook
description: Combine Python, SQL and written explanations in a Workspace notebook.
---

## What a notebook is

A notebook is a document made of cells. Each cell contains Python, SQL or
Markdown. Its runtime keeps Python variables and local SQL results available
between cells while it is running.

## Before you start

Sign in with the role you want to use for data access. Your deployment needs
notebook runtimes configured by an administrator. You can edit a notebook
without a runtime, but executing Python or SQL needs one.

## Create and run

1. Open **Workspaces** and select your folder.
2. Open **More things to create**, beside **SQL file**, and choose **Notebook**.
3. Click **Start runtime** and wait until it is ready.
4. Use **Add cell** or **+ Python**, **+ SQL**, and **+ Markdown** between cells.
5. Click a cell's editor to enter code. Use **Run this cell** to execute it.
6. Use **Run all** to run the document in order. It stops on an error; the
   adjacent **More ways to run** menu offers **Run all, continue after errors**.

In a Python cell, this returns `[(42,)]`:

```python
import duckdb
duckdb.sql('SELECT 41 + 1').fetchall()
```

Markdown cells contain explanations, headings and lists. Click **Edit Markdown
cell** to change one. A cell's optional **Cell title** helps identify it in
the outline.

## Save and organize

Changes save automatically; check that the toolbar says **Saved** before
leaving. Use **Move up**, **Move down**, or the drag handle to reorder a cell.
**Undo the last cell change** restores the last supported document edit.

Open **Outline, variables and packages** to inspect the notebook outline,
live variables and package controls. **Keyboard shortcuts** lists the keys
supported by the editor. **Download as .ipynb** downloads the notebook document.

## Runtime controls

Open the runtime status menu to stop the runtime or restart its kernel.
Restarting clears Python variables and local SQL tables. Stopping releases
the runtime; the saved document remains. Run the setup cells again after a
restart or a new runtime.

The runtime is also stopped after inactivity or its lifetime limit. Closing
the document is not a substitute for stopping a runtime you no longer need.

## Limits and errors

- If startup reports that the runtime service did not answer in time, wait
  for the failed start to finish before trying **Start runtime** again.
- A Python exception appears with its traceback under the cell. Fix the code
  and run it again.
- If the connection ends before a result arrives, the code may already have
  run. Check any writes before repeating the cell.
- A notebook's saved code and its running state are separate. Saving does
  not preserve live Python variables across runtime restarts.
