import { createContext } from "react";
import type { UiSource } from "./types";

export const UiRuntimeContext = createContext<{
  readOnly: boolean;
  source?: UiSource;
  markAction: (id: string) => void;
}>({ readOnly: true, markAction: () => {} });
