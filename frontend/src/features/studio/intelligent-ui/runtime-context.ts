import { createContext } from "react";

/** Whether a map in an answer may change its local selection and viewport. */
export const UiRuntimeContext = createContext<{ readOnly: boolean }>({ readOnly: true });
