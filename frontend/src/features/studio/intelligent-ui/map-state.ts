import { createContext, useContext, useState } from "react";
import { useStateField } from "@openuidev/react-lang";

/** The slice of an OpenUI state field the maps read and write. */
export type MapField = {
  value: unknown;
  setValue: (value: unknown) => void;
  isReactive: boolean;
};

type UseMapField = (name: string, value: unknown) => MapField;

/**
 * A map's selection and viewport. Inside an OpenUI program they are program
 * state; a registry `GeoMap` has no program, so it keeps them on the map.
 */
export const MapFieldContext = createContext<UseMapField>(
  useStateField as unknown as UseMapField,
);

export function useLocalMapField(_name: string, value: unknown): MapField {
  const [current, setCurrent] = useState<unknown>(value);
  return { value: current, setValue: setCurrent, isReactive: true };
}

export function useMapField(name: string, value: unknown): MapField {
  // The hook is fixed for a mounted map: the provider never changes it.
  const use = useContext(MapFieldContext);
  return use(name, value);
}
