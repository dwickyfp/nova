import { useEffect, useState } from "react";
import { clientTimeZone, type Daypart, daypartAt, hourIn } from "./greeting";

/** The reader's daypart, re-read each minute so a long-open page stays right. */
export function useClientDaypart(): Daypart {
  const read = () => daypartAt(hourIn(new Date(), clientTimeZone()));
  const [daypart, setDaypart] = useState(read);
  useEffect(() => {
    const timer = window.setInterval(() => setDaypart(read()), 60_000);
    return () => window.clearInterval(timer);
  }, []);
  return daypart;
}
