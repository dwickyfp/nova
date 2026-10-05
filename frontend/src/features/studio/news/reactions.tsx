import { ThumbsDown, ThumbsUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Reaction, Story } from "./newspaper-api";

/**
 * The reader's like and dislike for one story. Pressing the active one clears
 * it. The choice is theirs alone and shapes which stories lead their edition.
 */
export function Reactions({
  story,
  reaction,
  pending = false,
  onReact,
}: {
  story: Pick<Story, "id" | "narrative">;
  reaction: Reaction | null | undefined;
  pending?: boolean;
  onReact: (id: string, reaction: Reaction | null) => void;
}) {
  const button = (value: Reaction, Icon: typeof ThumbsUp, label: string) => {
    const pressed = reaction === value;
    return (
      <Button
        type="button"
        variant="ghost"
        size="icon"
        aria-label={`${label}: ${story.narrative.headline}`}
        title={label}
        aria-pressed={pressed}
        disabled={pending}
        className={cn(
          "size-9 rounded-md text-muted-foreground hover:text-foreground",
          pressed && (value === "like" ? "text-info-strong" : "text-destructive"),
        )}
        onClick={(event) => {
          event.stopPropagation();
          onReact(story.id, pressed ? null : value);
        }}
      >
        <Icon className="size-4" fill={pressed ? "currentColor" : "none"} />
      </Button>
    );
  };
  return (
    <span className="inline-flex items-center gap-0.5" role="group" aria-label="Your reaction">
      {button("like", ThumbsUp, "More like this")}
      {button("dislike", ThumbsDown, "Less like this")}
    </span>
  );
}
