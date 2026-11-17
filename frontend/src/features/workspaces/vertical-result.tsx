import type { MouseEvent } from "react";

type Cell = string | number | boolean | null;

/**
 * A result shown one record per block, as the MySQL client prints a statement
 * that ends with `\G`: each column name beside its value.
 */
export function VerticalResult({
  columns,
  rows,
  firstRowNumber,
  onCellContextMenu,
}: {
  columns: string[];
  rows: Cell[][];
  /** The 1-based number of the first row shown (pages after the first). */
  firstRowNumber: number;
  onCellContextMenu?: (
    event: MouseEvent,
    rowIndex: number,
    columnIndex: number,
  ) => void;
}) {
  return (
    <div className="min-w-0 px-3 py-1 text-sm">
      {rows.map((row, rowIndex) => {
        const number = firstRowNumber + rowIndex;
        return (
          <section
            key={number}
            aria-label={`Row ${number}`}
            className="border-b border-border py-2 last:border-b-0"
          >
            <h3 className="mb-1 text-xs font-medium text-muted-foreground">
              Row {number}
            </h3>
            <dl className="grid grid-cols-[fit-content(40%)_minmax(0,1fr)] gap-x-4 gap-y-0.5 font-mono text-xs">
              {columns.map((column, columnIndex) => {
                const value = row[columnIndex];
                return (
                  <div key={`${column}-${columnIndex}`} className="contents">
                    <dt className="break-all text-right text-muted-foreground">
                      {column}
                    </dt>
                    <dd
                      className="min-w-0 whitespace-pre-wrap break-all"
                      onContextMenu={(event) =>
                        onCellContextMenu?.(event, number - 1, columnIndex)
                      }
                    >
                      {value === null || value === undefined ? (
                        <span className="text-muted-foreground">NULL</span>
                      ) : (
                        String(value)
                      )}
                    </dd>
                  </div>
                );
              })}
            </dl>
          </section>
        );
      })}
    </div>
  );
}
