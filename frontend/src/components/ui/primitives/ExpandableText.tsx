import { useState } from "react";

export interface ExpandableTextProps {
  text: string;
  charLimit: number;
  className?: string;
}

export function ExpandableText({ text, charLimit, className }: ExpandableTextProps) {
  const [expanded, setExpanded] = useState(false);
  const isLong = text.length > charLimit;

  if (!isLong) {
    return <p className={className}>{text}</p>;
  }

  return (
    <>
      <p className={className}>
        {expanded ? text : `${text.slice(0, charLimit)}…`}
      </p>
      <button
        type="button"
        className="expand-link"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? "收起" : "展开"}
      </button>
    </>
  );
}
